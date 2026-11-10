// Sign in with a code.
//
// Two kinds of code are accepted:
//   * the fixed test code, for the one test email, both set in Vercel
//     (MUSE_TEST_LOGIN_EMAIL, MUSE_TEST_LOGIN_CODE). Nothing is emailed.
//   * a normal one-time code that Supabase emailed after "Email me a code".
// Five wrong codes for an email lock it for 15 minutes. Attempts are counted
// in the login_attempts table, which needs SUPABASE_SECRET_KEY (server only).

import { createClient as createAdminClient } from "@supabase/supabase-js";
import { createHash, timingSafeEqual } from "node:crypto";
import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";

const WINDOW_MINUTES = 15;
const MAX_FAILURES = 5;

function adminClient() {
  const key = process.env.SUPABASE_SECRET_KEY;
  if (!key) return null;
  return createAdminClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

/** Compare without leaking, through timing, how much of the code was right. */
function sameCode(a: string, b: string): boolean {
  const digest = (s: string) => createHash("sha256").update(s).digest();
  return timingSafeEqual(digest(a), digest(b));
}

function fail(status: number, message: string) {
  return NextResponse.json({ ok: false, message }, { status });
}

export async function POST(request: NextRequest) {
  let body: { email?: unknown; code?: unknown };
  try {
    body = await request.json();
  } catch {
    return fail(400, "Something went wrong sending the form. Try again.");
  }
  const email = String(body.email ?? "").trim().toLowerCase();
  const code = String(body.code ?? "").trim();
  if (!email || !code) return fail(400, "Enter your email and the code.");

  const admin = adminClient();

  if (admin) {
    const since = new Date(Date.now() - WINDOW_MINUTES * 60_000).toISOString();
    const { count } = await admin
      .from("login_attempts")
      .select("id", { count: "exact", head: true })
      .eq("email", email)
      .eq("ok", false)
      .gte("attempted_at", since);
    if ((count ?? 0) >= MAX_FAILURES) {
      return fail(429, `Too many wrong codes. Try again in ${WINDOW_MINUTES} minutes.`);
    }
  }

  const supabase = await createClient();
  const testEmail = process.env.MUSE_TEST_LOGIN_EMAIL?.trim().toLowerCase();
  const testCode = process.env.MUSE_TEST_LOGIN_CODE?.trim();
  let ok = false;
  let message = "That code isn't right.";

  if (admin && testEmail && testCode && email === testEmail && sameCode(code, testCode)) {
    // Make a one-time sign-in token without sending an email, then use it.
    const { data, error } = await admin.auth.admin.generateLink({ type: "magiclink", email });
    if (!error && data?.properties?.hashed_token) {
      const { error: verifyError } = await supabase.auth.verifyOtp({
        type: "email",
        token_hash: data.properties.hashed_token,
      });
      ok = !verifyError;
      if (verifyError) message = "Sign-in failed. Try again.";
    } else {
      message = "Sign-in failed. Check the account exists in Supabase.";
    }
  } else if (/^\d{6,10}$/.test(code)) {
    // A code Supabase emailed.
    const { error } = await supabase.auth.verifyOtp({ email, token: code, type: "email" });
    ok = !error;
    if (error) message = "That code isn't right, or it has expired.";
  }

  if (admin) await admin.from("login_attempts").insert({ email, ok });
  return ok ? NextResponse.json({ ok: true }) : fail(401, message);
}

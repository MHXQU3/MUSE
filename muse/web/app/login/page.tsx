"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase/client";

type State =
  | { kind: "idle" }
  | { kind: "working" }
  | { kind: "sent" }
  | { kind: "error"; message: string };

function friendly(message: string): string {
  if (/signups not allowed|not found/i.test(message))
    return "There's no MUSE account for that email. Accounts are added in Supabase.";
  if (/rate limit/i.test(message))
    return "Too many sign-in emails in the last hour. Try again later.";
  return message;
}

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  async function signIn(e: React.FormEvent) {
    e.preventDefault();
    setState({ kind: "working" });
    try {
      const res = await fetch("/auth/code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, code }),
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        window.location.assign("/timeline"); // full load so the server sees the new session
      } else {
        setState({ kind: "error", message: body.message ?? "Sign-in failed. Try again." });
      }
    } catch {
      setState({ kind: "error", message: "Couldn't reach MUSE. Check your connection and try again." });
    }
  }

  async function emailCode() {
    if (!email) {
      setState({ kind: "error", message: "Enter your email first." });
      return;
    }
    setState({ kind: "working" });
    const { error } = await createClient().auth.signInWithOtp({
      email,
      options: {
        emailRedirectTo: `${window.location.origin}/auth/callback`,
        shouldCreateUser: false, // MUSE is private: only existing accounts can sign in
      },
    });
    setState(error ? { kind: "error", message: friendly(error.message) } : { kind: "sent" });
  }

  return (
    <main className="login">
      <p className="wordmark login-wordmark">MUSE</p>
      <p className="login-tagline">Your music. Your world.</p>
      <form onSubmit={signIn} className="login-form">
        <label htmlFor="email">Email</label>
        <input id="email" type="email" required autoComplete="email" value={email}
               onChange={(e) => setEmail(e.target.value)} />
        <label htmlFor="code">Code</label>
        <input id="code" type="text" inputMode="numeric" autoComplete="one-time-code" required
               pattern="[0-9]*" value={code} onChange={(e) => setCode(e.target.value.trim())} />
        <button type="submit" className="button" disabled={state.kind === "working"}>
          {state.kind === "working" ? "Signing in…" : "Sign in"}
        </button>
        <button type="button" className="text-button login-alt" onClick={emailCode}
                disabled={state.kind === "working"}>
          Email me a code
        </button>
        {state.kind === "sent" && (
          <p className="login-note" role="status">
            Sent. Enter the code from the email above, or tap the link in it.
          </p>
        )}
        {state.kind === "error" && <p className="form-error" role="alert">{state.message}</p>}
      </form>
    </main>
  );
}

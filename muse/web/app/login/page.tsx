"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase/client";

type State =
  | { kind: "idle" }
  | { kind: "working" }
  | { kind: "sent"; email: string }
  | { kind: "error"; message: string };

function friendly(message: string): string {
  if (/invalid login credentials/i.test(message)) return "That email and password don't match.";
  if (/signups not allowed|not found/i.test(message))
    return "There's no MUSE account for that email. Accounts are added in Supabase.";
  if (/rate limit/i.test(message))
    return "Too many sign-in emails have been sent in the last hour. Use your password, or try the link again later.";
  return message;
}

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  async function signIn(e: React.FormEvent) {
    e.preventDefault();
    setState({ kind: "working" });
    const { error } = await createClient().auth.signInWithPassword({ email, password });
    if (error) {
      setState({ kind: "error", message: friendly(error.message) });
    } else {
      // Full page load so the server sees the new session cookie.
      window.location.assign("/timeline");
    }
  }

  async function sendLink() {
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
    setState(error ? { kind: "error", message: friendly(error.message) } : { kind: "sent", email });
  }

  return (
    <main className="login">
      <p className="wordmark login-wordmark">MUSE</p>
      <p className="login-tagline">Your music. Your world.</p>
      {state.kind === "sent" ? (
        <p className="login-sent" role="status">
          Check {state.email} for a sign-in link. You can close this tab once you&rsquo;ve opened it.
        </p>
      ) : (
        <form onSubmit={signIn} className="login-form">
          <label htmlFor="email">Email</label>
          <input id="email" type="email" required autoComplete="email" value={email}
                 onChange={(e) => setEmail(e.target.value)} />
          <label htmlFor="password">Password</label>
          <input id="password" type="password" required autoComplete="current-password" value={password}
                 onChange={(e) => setPassword(e.target.value)} />
          <button type="submit" className="button" disabled={state.kind === "working"}>
            {state.kind === "working" ? "Signing in…" : "Sign in"}
          </button>
          <button type="button" className="text-button login-alt" onClick={sendLink}
                  disabled={state.kind === "working"}>
            Email me a sign-in link instead
          </button>
          {state.kind === "error" && <p className="form-error" role="alert">{state.message}</p>}
        </form>
      )}
    </main>
  );
}

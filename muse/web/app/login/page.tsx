"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase/client";

type State = { kind: "idle" } | { kind: "sending" } | { kind: "sent"; email: string } | { kind: "error"; message: string };

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  async function sendLink(e: React.FormEvent) {
    e.preventDefault();
    setState({ kind: "sending" });
    const supabase = createClient();
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: {
        emailRedirectTo: `${window.location.origin}/auth/callback`,
        shouldCreateUser: false, // MUSE is private: only existing accounts can sign in
      },
    });
    if (error) {
      const message = /signups not allowed|not found/i.test(error.message)
        ? "There's no MUSE account for that email. Accounts are added in Supabase."
        : error.message;
      setState({ kind: "error", message });
    } else {
      setState({ kind: "sent", email });
    }
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
        <form onSubmit={sendLink} className="login-form">
          <label htmlFor="email">Email</label>
          <input id="email" type="email" required autoComplete="email" value={email}
                 onChange={(e) => setEmail(e.target.value)} />
          <button type="submit" className="button" disabled={state.kind === "sending"}>
            {state.kind === "sending" ? "Sending link…" : "Email me a sign-in link"}
          </button>
          {state.kind === "error" && <p className="form-error" role="alert">{state.message}</p>}
        </form>
      )}
    </main>
  );
}

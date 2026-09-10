import { useState } from "react";
import { ApiError, requestLink } from "./api";

/* The sign-in screen. An email field and a button, and nothing else.
 *
 * There is no password field because there are no passwords, and no "create an
 * account" link because there is no signup -- an account is made by inserting a
 * business row. Offering either would be a control that does nothing, which the
 * design direction argues against more than once.
 *
 * WHAT IT DELIBERATELY DOES NOT TELL YOU. The success message is the same
 * whether or not the address has an account. That is not vagueness for its own
 * sake: a screen that says "no such account" is an address checker for anyone
 * who wants to know which clinics use Talkwisp. The server behaves the same
 * way; this screen just has to not undo it.
 *
 * While email delivery is the console backend, the link is printed to the
 * server log rather than sent. The message below says so, because a person
 * waiting for an email that is never coming has no way to work that out. */

export default function SignIn() {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<
    { kind: "idle" } | { kind: "sending" } | { kind: "sent" } | { kind: "failed"; message: string }
  >({ kind: "idle" });

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!email.trim() || state.kind === "sending") return;
    setState({ kind: "sending" });
    try {
      await requestLink(email.trim());
      setState({ kind: "sent" });
    } catch (error) {
      setState({
        kind: "failed",
        message: error instanceof ApiError ? error.message : "Something went wrong.",
      });
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "var(--ground)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <div className="card" style={{ width: "100%", maxWidth: 380, padding: 28 }}>
        <div style={{ marginBottom: 22 }}>
          <div style={{ fontSize: 19, fontWeight: 800, letterSpacing: "-0.01em" }}>
            Talkwisp
          </div>
          <div style={{ fontSize: 13, color: "var(--text-faint)", marginTop: 2 }}>
            Sign in to your knowledge base
          </div>
        </div>

        {state.kind === "sent" ? (
          <div>
            <p style={{ margin: "0 0 10px", fontSize: 15, lineHeight: 1.55 }}>
              If that address has an account, a sign-in link is on its way. It
              expires in 15 minutes.
            </p>
            {/* This used to say "Email sending is not switched on yet, so the
                link is printed in the server log rather than sent." It was true
                when written and became false the day Resend was wired -- but it
                was hardcoded and unconditional, so it kept telling people their
                link was never coming while it was already in their inbox. Anyone
                reading it stops trying, which made a working sign-in look
                broken.

                The lesson is narrow: the frontend cannot state a fact about the
                backend's configuration. It does not know, and a hardcoded claim
                is only correct until the day it is not. Either the server says
                so in its response, or nothing says so. */}
            <button
              className="control control-quiet"
              onClick={() => setState({ kind: "idle" })}
            >
              Use a different address
            </button>
          </div>
        ) : (
          <form onSubmit={submit}>
            <label
              htmlFor="email"
              style={{
                display: "block",
                fontSize: 13,
                fontWeight: 600,
                marginBottom: 6,
                color: "var(--text-secondary)",
              }}
            >
              Email
            </label>
            <input
              id="email"
              className="field"
              type="email"
              autoComplete="email"
              autoFocus
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@clinic.uz"
              style={{ width: "100%", marginBottom: 14 }}
            />
            <button
              className="control control-primary"
              type="submit"
              disabled={state.kind === "sending"}
              style={{ width: "100%" }}
            >
              {state.kind === "sending" ? "Sending…" : "Send sign-in link"}
            </button>
            {state.kind === "failed" && (
              <p
                style={{
                  margin: "12px 0 0",
                  fontSize: 13,
                  color: "var(--danger, #a4362f)",
                }}
              >
                {state.message}
              </p>
            )}
          </form>
        )}
      </div>
    </div>
  );
}

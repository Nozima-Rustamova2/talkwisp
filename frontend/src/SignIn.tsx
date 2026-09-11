import { useState } from "react";
import { ApiError, requestLink, signUp } from "./api";

/* The sign-in screen, and now the sign-up screen: one form with two modes.
 *
 * There is no password field because there are no passwords. There IS a way to
 * create an account, which there was not until signup became self-serve -- and
 * this file used to say so at some length, in a block explaining that
 * onboarding was by hand. That block was true when written and is the kind of
 * thing that quietly becomes a lie; it is gone rather than reworded.
 *
 * SIGNING UP DOES NOT GET YOU A WORKING AGENT, and the copy below must not
 * imply it does. It gets you an account that can sign in and look around. The
 * spending -- reading documents, answering questions -- is switched on by a
 * person, and App.tsx says so in a band across the top once you are in.
 *
 * WHAT IT DELIBERATELY DOES NOT TELL YOU. The success message is the same
 * whether or not the address has an account. That is not vagueness for its own
 * sake: a screen that says "no such account" is an address checker for anyone
 * who wants to know which clinics use Talkwisp. The server behaves the same
 * way; this screen just has to not undo it.
 *
 * IT SAYS NOTHING ABOUT DELIVERY, deliberately. It used to announce that email
 * sending was off and the link went to the server log. That was true when
 * written and false the day Resend was wired, but it was hardcoded and
 * unconditional -- so it went on telling people their link was never coming
 * while it sat in their inbox. This screen cannot know how the server is
 * configured; a claim about that is only correct until the day it is not. */

export default function SignIn() {
  const [email, setEmail] = useState("");
  /* The business name, only asked for when creating an account. It is the
     handle every later operation uses -- it is what `onboard.py --approve`
     takes -- so it is required rather than optional, and it is the one thing
     this form asks for that sign-in does not. */
  const [name, setName] = useState("");
  const [mode, setMode] = useState<"in" | "up">("in");
  const [state, setState] = useState<
    { kind: "idle" } | { kind: "sending" } | { kind: "sent" } | { kind: "failed"; message: string }
  >({ kind: "idle" });

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!email.trim() || state.kind === "sending") return;
    if (mode === "up" && !name.trim()) return;
    setState({ kind: "sending" });
    try {
      if (mode === "up") await signUp(email.trim(), name.trim());
      else await requestLink(email.trim());
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
            {mode === "up"
              ? "Create an account for your business"
              : "Sign in to your knowledge base"}
          </div>
        </div>

        {state.kind === "sent" ? (
          <div>
            <p style={{ margin: "0 0 10px", fontSize: 15, lineHeight: 1.55 }}>
              If that address has an account, a sign-in link is on its way. It
              expires in 15 minutes.
            </p>
            {/* IDENTICAL WORDING IN BOTH MODES, and the server sends the same
                sentence for both endpoints. "Account created" here would say
                out loud what /auth/signup refuses to say in its response --
                that this address was not already registered -- and one screen
                is enough to undo the property both endpoints were written to
                have.

                The extra line below is safe because it is true either way: it
                describes what approval is, not what just happened. */}
            {mode === "up" && (
              <p style={{ margin: "0 0 10px", fontSize: 14, lineHeight: 1.55,
                          color: "var(--text-faint)" }}>
                Once you are in you can look around straight away. Reading
                documents and answering questions are switched on by us, by
                hand — we will email you when your account is approved.
              </p>
            )}
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
              onClick={() => {
                // Clear the field, not just the state. Leaving the old value
                // in place means the next keystroke appends to a mistyped
                // address rather than replacing it -- and a form that silently
                // concatenates reads as a broken site, on the screen whose
                // whole job is to let someone in.
                setEmail("");
                setState({ kind: "idle" });
              }}
            >
              Use a different address
            </button>
          </div>
        ) : (
          <form onSubmit={submit}>
            {mode === "up" && (
              <>
                <label
                  htmlFor="business"
                  style={{
                    display: "block",
                    fontSize: 13,
                    fontWeight: 600,
                    marginBottom: 6,
                    color: "var(--text-secondary)",
                  }}
                >
                  Business name
                </label>
                <input
                  id="business"
                  className="field"
                  type="text"
                  autoComplete="organization"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Rangli Salon"
                  style={{ width: "100%", marginBottom: 14 }}
                />
              </>
            )}
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
              // "you@clinic.uz" assumed a clinic. The clinic is the test case, not
              // the market: the positioning is any business with repeat
              // questions -- salons, courses, service businesses.
              placeholder="you@company.uz"
              style={{ width: "100%", marginBottom: 14 }}
            />
            <button
              className="control control-primary"
              type="submit"
              disabled={state.kind === "sending"}
              style={{ width: "100%" }}
            >
              {state.kind === "sending"
                ? "Sending…"
                : mode === "up"
                  ? "Create account"
                  : "Send sign-in link"}
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

        {/* THE SWITCH BETWEEN THE TWO MODES.
            Secondary weight on purpose -- a link, not a second button. Two
            buttons of equal weight would make the real one harder to find, and
            for most people arriving here the real one is sign in.

            This used to be a paragraph explaining that there was no signup and
            that we set every agent up by hand. That was true, and it stopped
            being true, and a hardcoded sentence about how the product works is
            only correct until the day it is not -- the same mistake as the
            "email sending is not switched on yet" line that sat here telling
            people their link was never coming. What replaced it is a control,
            not a claim. */}
        <div
          style={{
            marginTop: 20,
            paddingTop: 16,
            borderTop: "1px solid var(--rule)",
            fontSize: 13,
            lineHeight: 1.55,
            color: "var(--text-faint)",
          }}
        >
          {mode === "in" ? "Don't have an account yet? " : "Already have one? "}
          <button
            type="button"
            onClick={() => {
              setMode(mode === "in" ? "up" : "in");
              /* Clear the outcome, not just the mode. Switching modes under a
                 "link is on its way" message would leave the old result sitting
                 above a form that now does something different. */
              setState({ kind: "idle" });
            }}
            style={{
              background: "none",
              border: "none",
              padding: 0,
              font: "inherit",
              cursor: "pointer",
              color: "var(--accent-pressed)",
              fontWeight: 600,
            }}
          >
            {mode === "in" ? "Create one" : "Sign in instead"}
          </button>
        </div>
      </div>
    </div>
  );
}

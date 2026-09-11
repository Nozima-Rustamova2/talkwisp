import { useEffect, useState } from "react";
import { ApiError, connectTelegram, getChannel, type Channel } from "./api";

/* Connect your own Telegram bot, without anyone being on SSH.
 *
 * THIS SCREEN IS HONEST ABOUT A MANUAL STEP IT CANNOT REMOVE. Storing a token
 * does not start anything: bot.py binds its token, business and owner as module
 * globals at startup -- one tenant per process, permanently -- so a poller has
 * to be started for the business, and that is systemctl, which needs root. The
 * web app deliberately cannot become root. A supervisor that watches for
 * businesses with tokens and starts pollers is what removes this; it does not
 * exist yet, and nothing here may imply it does.
 *
 * So the copy says "we'll switch it on" and names the wait, rather than showing
 * a spinner that will never resolve. The last screen that hardcoded a claim
 * about the backend's configuration -- "email sending is not switched on yet"
 * -- kept telling people their link was never coming after Resend was wired.
 * The lesson taken: state what is true and let the state come from the server.
 *
 * THE TOKEN IS WRITE-ONLY HERE. GET /channel never returns it, so this screen
 * cannot show it back, and that is deliberate rather than a gap -- the only
 * thing sending a live credential to a browser can do is put it somewhere it
 * can leak. */

export default function Settings() {
  const [state, setState] = useState<Channel | "loading">("loading");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [justConnected, setJustConnected] = useState<string | null>(null);

  async function load() {
    try {
      setState(await getChannel());
    } catch {
      setState({
        connected: false, bot_username: null, live: null,
        owner_linked: false, claim_link: null,
      });
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function connect(event: React.FormEvent) {
    event.preventDefault();
    if (!token.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await connectTelegram(token.trim());
      setJustConnected(result.bot_username);
      setToken("");
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  if (state === "loading") return null;

  const shell = { maxWidth: 680, margin: "0 auto", padding: "24px 20px 64px" } as const;

  return (
    <div style={shell}>
      <h1 style={{ fontSize: 20, fontWeight: 800, margin: "0 0 4px" }}>Settings</h1>
      <p style={{ margin: "0 0 22px", fontSize: 14, color: "var(--text-faint)" }}>
        Where your agent answers.
      </p>

      <div className="card" style={{ padding: 22 }}>
        <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>
          Telegram
        </div>

        {!state.connected ? (
          <>
            <p style={{ margin: "0 0 16px", fontSize: 14, lineHeight: 1.6, color: "var(--text-secondary)" }}>
              Create a bot in{" "}
              <a href="https://t.me/BotFather" target="_blank" rel="noreferrer">
                @BotFather
              </a>{" "}
              — send it <code>/newbot</code> and it replies with a token. Paste
              the whole line here.
            </p>
            <form onSubmit={connect}>
              <input
                className="field"
                type="password"
                autoComplete="off"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="123456789:AAE…"
                style={{ width: "100%", marginBottom: 12 }}
              />
              <button
                className="control control-primary"
                type="submit"
                disabled={busy || !token.trim()}
              >
                {busy ? "Checking with Telegram…" : "Connect"}
              </button>
            </form>
          </>
        ) : (
          <>
            <p style={{ margin: "0 0 14px", fontSize: 14, lineHeight: 1.6 }}>
              Connected to{" "}
              <strong>
                @{state.bot_username ?? "your bot"}
              </strong>
              {state.live === false && (
                <span style={{ color: "var(--danger, #a4362f)" }}>
                  {" "}— but Telegram is rejecting the token now. It was
                  probably revoked in @BotFather. Paste a new one below.
                </span>
              )}
              {state.live === null && (
                <span style={{ color: "var(--text-faint)" }}>
                  {" "}— we couldn't reach Telegram to check it just now.
                </span>
              )}
            </p>

            {/* THE STEP THAT IS STILL MANUAL, named rather than hidden.
                
                THIS WORDING STATES A POLICY, NOT A STATE, and the first draft
                got that wrong: it said "your bot is saved, but it isn't
                answering yet", which this screen cannot possibly know. Nothing
                registers a running poller, so the server cannot tell us, and
                the claim was simply hardcoded -- so a business whose bot IS
                running would be told it was not. Caught by looking at the
                screen signed in as a business that is polling in production.

                Exactly the mistake SignIn.tsx already carries a warning about:
                it announced that email sending was off, which was true when
                written and false the day Resend was wired, and went on telling
                people their link was never coming. A frontend cannot state a
                fact about the backend's configuration. It can state a policy,
                which is true regardless. */}
            <div
              style={{
                background: "var(--accent-tint, #eef4f0)",
                borderRadius: "var(--radius-inner, 8px)",
                padding: "12px 14px",
                fontSize: 14,
                lineHeight: 1.6,
                marginBottom: 16,
              }}
            >
              While we're in early access we start each bot by hand, so there
              may be a wait between saving a token and the bot replying — we'll
              email you when yours is on. Everything else works meanwhile: add
              knowledge, review it, and try it on the Test screen.
            </div>

            {state.owner_linked ? (
              <p style={{ margin: 0, fontSize: 14, color: "var(--text-faint)" }}>
                Your Telegram account is linked, so you can use owner commands
                in the chat.
              </p>
            ) : state.claim_link ? (
              <>
                <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>
                  One more step: tell it who you are
                </div>
                <p style={{ margin: "0 0 12px", fontSize: 14, lineHeight: 1.6, color: "var(--text-secondary)" }}>
                  {/* Says WHY, because "press Start" with no reason reads as
                      busywork. Telegram genuinely will not reveal a user id
                      from a bot token -- the owner has to speak first. */}
                  Telegram won't tell us who owns a bot, so open yours and press
                  Start. That links your account and nobody else's — the link
                  below is signed and works once.
                </p>
                <a
                  className="control control-primary"
                  href={state.claim_link}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open @{state.bot_username} and press Start
                </a>
                <button
                  className="control control-quiet"
                  onClick={() => void load()}
                  style={{ marginLeft: 8 }}
                >
                  I've done it
                </button>
              </>
            ) : null}

            <details style={{ marginTop: 18 }}>
              <summary style={{ cursor: "pointer", fontSize: 14, color: "var(--text-faint)" }}>
                Replace the token
              </summary>
              <form onSubmit={connect} style={{ marginTop: 10 }}>
                <input
                  className="field"
                  type="password"
                  autoComplete="off"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="123456789:AAE…"
                  style={{ width: "100%", marginBottom: 10 }}
                />
                <button className="control control-secondary" type="submit" disabled={busy || !token.trim()}>
                  {busy ? "Checking…" : "Replace"}
                </button>
              </form>
            </details>
          </>
        )}

        {justConnected && (
          <p style={{ margin: "12px 0 0", fontSize: 14 }}>
            Saved — Telegram says that token belongs to{" "}
            <strong>@{justConnected}</strong>.{" "}
            {/* The username is echoed back for the same reason onboard.py
                prints it: a well-formed token for the WRONG bot is invisible,
                and that is exactly how seven landing-page links once pointed at
                a stranger's bot. */}
            If that isn't your bot, paste the right token.
          </p>
        )}

        {error && (
          <p style={{ margin: "12px 0 0", fontSize: 13, color: "var(--danger, #a4362f)" }}>
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

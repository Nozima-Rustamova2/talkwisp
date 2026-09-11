import { useEffect, useState } from "react";
import AddKnowledge from "./AddKnowledge";
import Review from "./Review";
import Settings from "./Settings";
import SignIn from "./SignIn";
import TestConsole from "./TestConsole";
import { getMe, logout, setSpendingAllowed, type Me } from "./api";

/* The shell: header, the two screens that exist, and the route between them.
 *
 * Routing is the HASH, not the path, and that is not a stylistic choice. The
 * app is served by StaticFiles mounted at /app, which serves index.html for the
 * mount root and 404s for anything below it, so a path route like /app/review
 * would work while clicking and 404 on reload or on a shared link. A hash never
 * reaches the server. When there is a reason for real paths, the server needs a
 * catch-all that returns index.html -- until then this is the honest option
 * rather than the one that breaks on refresh.
 *
 * No router library for two screens. */

type Route = "add" | "review" | "test" | "settings";

/* FOUR ITEMS, NOT SEVEN.
 *
 * The design draws a seven-item left rail. Three of those still have nothing
 * behind them: Gaps and Conversations have no prototype and no endpoint
 * (gaps.jsonl is a file, and app/console.py:33 says to promote it "when it
 * earns a table"), and Templates has neither. They are not here.
 *
 * Settings is what changed. Add, Review and Test are a SEQUENCE -- you do them
 * in that order -- and a header says an order better than a rail does. Settings
 * is the first thing that is not part of that flow: you go to it when something
 * needs changing, not because it comes next. That is the point at which people
 * jump rather than progress, and jumping is what a rail is for. */
function routeFromHash(): Route {
  const hash = window.location.hash.replace(/^#\/?/, "");
  if (hash === "review") return "review";
  if (hash === "test") return "test";
  if (hash === "settings") return "settings";
  return "add";
}

export default function App() {
  const [route, setRoute] = useState<Route>(routeFromHash);

  /* THREE states, not two. "Not signed in" and "we have not asked yet" are
   * different, and collapsing them flashes the sign-in form for a moment on
   * every load for someone who is already signed in. */
  const [me, setMe] = useState<Me | "asking">("asking");

  useEffect(() => {
    /* The screens are served by StaticFiles, which has no session check on it
     * -- a mount cannot carry a dependency, and the sign-in page has to load
     * for a logged-out visitor anyway. So the HTML always arrives and this is
     * what decides what to draw. Nothing sensitive is in the bundle; every
     * piece of business data comes from an endpoint that refuses without a
     * session. */
    getMe()
      .then((who) => {
        /* Before setMe, so no screen can render against a stale value. The
           screens read it out of the module rather than from props -- see
           spendingAllowed in api.ts for why. */
        setSpendingAllowed(who.approved);
        setMe(who);
      })
      .catch(() => setMe({ business: null, email: null, approved: false }));
  }, []);

  useEffect(() => {
    const onChange = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  // The title is the only part of the page a hash route does not update by
  // itself. Left alone it says "Add knowledge" while the Review screen is on
  // screen, which is wrong in the browser tab, in history and in a bookmark.
  //
  // It has to know about the signed-out state too, now that this runs before
  // the returns below: without that arm, the sign-in page is titled "Add
  // knowledge" in the tab, in history and in a bookmark.
  useEffect(() => {
    document.title =
      me === "asking" || !me.business
        ? "Sign in — Talkwisp"
        : route === "review"
          ? "Review — Talkwisp"
          : route === "test"
            ? "Test — Talkwisp"
            : route === "settings"
              ? "Settings — Talkwisp"
              : "Add knowledge — Talkwisp";
  }, [route, me]);

  /* EVERY HOOK ABOVE THIS LINE, EVERY RETURN BELOW IT.
   *
   * These two returns were above the two effects until 2026-09-09, which made
   * those effects conditional, and React counts hooks by position: render one
   * returned null after three hooks, render two ran past both guards and asked
   * for a fourth, and React threw "Rendered more hooks than during the previous
   * render" and unmounted the tree.
   *
   * It crashed ONLY for signed-in users. A logged-out visitor returns at the
   * second guard, so the hook count stays three on both renders and the sign-in
   * screen works perfectly -- the bug was invisible in every state except a
   * successful login, which is to say the moment the feature starts working.
   *
   * `tsc -b` cannot see it (it is not a type error) and check_auth.py cannot
   * either (42 checks, none of which render a component). `npm run lint` names
   * both lines exactly, and is now part of `npm run build` so it cannot be the
   * step someone skips. */
  if (me === "asking") return null;
  if (!me.business) return <SignIn />;

  /* THE WAITING STATE.
   *
   * Shown as a band above the app rather than instead of it, because everything
   * below it genuinely works: the screens are real, the review queue is real,
   * /ask answers from facts with no model involved. Replacing the product with
   * a "pending approval" page would be claiming less than is true, and would
   * also mean nobody could look at what they had signed up for.
   *
   * WHAT IT DOES NOT SAY, deliberately: any estimate of how long. There is no
   * queue, no SLA and no automation behind this -- it is me reading an email --
   * so "within 24 hours" would be a number invented to sound reassuring, and
   * the first time it slipped it would be a broken promise on the screen. It
   * says the mechanism instead: by hand, early access, we will email you.
   *
   * It names the address so a typo is visible. Someone who signed up as
   * malika@gmial.com will never get the mail, and this is the only place they
   * could ever find that out. */
  const waiting = !me.approved && (
    <div
      style={{
        background: "var(--accent-tint, #eef4f0)",
        borderBottom: "1px solid var(--rule)",
        padding: "14px 24px",
        fontSize: 14,
        lineHeight: 1.6,
        color: "var(--text-secondary)",
      }}
    >
      <strong style={{ color: "var(--text-primary, #1c2430)" }}>
        Your account is not approved yet.
      </strong>{" "}
      Look around as much as you like — the screens below are real. But adding
      knowledge and asking questions cost money to run, so those are switched
      off until we approve you. Talkwisp is in early access and we approve
      accounts by hand; we will email{" "}
      <span style={{ fontWeight: 600 }}>{me.email}</span> when yours is ready.
    </div>
  );

  const tab = (to: Route, label: string) => (
    <a
      href={to === "add" ? "#/" : `#/${to}`}
      style={{
        minHeight: 44,
        display: "inline-flex",
        alignItems: "center",
        padding: "0 12px",
        borderRadius: "var(--radius-inner)",
        fontSize: 15,
        fontWeight: route === to ? 700 : 600,
        color: route === to ? "var(--accent-pressed)" : "var(--text-secondary)",
        background: route === to ? "var(--accent-tint)" : "transparent",
        textDecoration: "none",
      }}
    >
      {label}
    </a>
  );

  return (
    <div style={{ minHeight: "100vh", background: "var(--ground)" }}>
      <header
        style={{
          background: "var(--surface)",
          boxShadow: "0 1px 0 var(--rule)",
          padding: "12px 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 18,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
          <span style={{ fontSize: 18, fontWeight: 800, letterSpacing: "-0.01em" }}>
            Talkwisp
          </span>
          <span style={{ fontSize: 13, color: "var(--text-faint)" }}>Knowledge base</span>
        </div>
        {/* Two links, because there are two screens. No business name, no user,
            no language pills, and no links to screens that do not exist: the
            rail in the prototype points at five of those, and an inert nav is
            the dishonesty the design docs argue against. */}
        <nav style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
          {tab("add", "Add knowledge")}
          {tab("review", "Review")}
          {tab("test", "Test")}
          {tab("settings", "Settings")}
          {/* The signed-in address, and a way out. Shown because a session that
              cannot be seen or ended is the one part of auth a person cannot
              verify for themselves -- and on a shared machine that matters more
              than the space it costs. */}
          <span
            style={{
              fontSize: 13,
              color: "var(--text-faint)",
              marginLeft: 8,
              maxWidth: 200,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {me.email}
          </span>
          <button
            className="control control-quiet"
            onClick={async () => {
              await logout().catch(() => undefined);
              /* A full reload rather than setting state: it throws away every
                 screen's in-memory copy of the previous session's data, which
                 setting a flag would leave sitting in a closure. */
              window.location.reload();
            }}
          >
            Sign out
          </button>
        </nav>
      </header>

      {waiting}
      {route === "review" ? (
        <Review />
      ) : route === "test" ? (
        <TestConsole />
      ) : route === "settings" ? (
        <Settings />
      ) : (
        <AddKnowledge />
      )}
    </div>
  );
}

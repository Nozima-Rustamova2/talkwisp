import { useEffect, useState } from "react";
import AddKnowledge from "./AddKnowledge";
import Dashboard from "./Dashboard";
import Gaps from "./Gaps";
import Dialogs from "./Dialogs";
import Payment from "./Payment";
import Knowledge from "./Knowledge";
import Review from "./Review";
import Settings from "./Settings";
import SignIn from "./SignIn";
import TestConsole from "./TestConsole";
import { getMe, getPayment, logout, setSpendingAllowed, type Me } from "./api";

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

type Route =
  | "dashboard"
  | "gaps"
  | "add"
  | "review"
  | "knowledge"
  | "dialogs"
  | "payment"
  | "test"
  | "settings";

/* FOUR ITEMS, NOT SEVEN.
 *
 * The design draws a seven-item left rail. Templates still has nothing behind
 * it and is not here. (Historical: this header predates the rail.)
 *
 * Settings is what changed. Add, Review and Test are a SEQUENCE -- you do them
 * in that order -- and a header says an order better than a rail does. Settings
 * is the first thing that is not part of that flow: you go to it when something
 * needs changing, not because it comes next. That is the point at which people
 * jump rather than progress, and jumping is what a rail is for. */
function routeFromHash(): Route {
  const hash = window.location.hash.replace(/^#\/?/, "");
  if (hash === "review") return "review";
  if (hash === "knowledge") return "knowledge";
  if (hash === "dashboard") return "dashboard";
  if (hash === "gaps") return "gaps";
  if (hash === "dialogs") return "dialogs";
  if (hash === "payment") return "payment";
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
        : route === "dashboard"
          ? "Dashboard — Talkwisp"
          : route === "gaps"
          ? "Gaps — Talkwisp"
          : route === "review"
          ? "Review — Talkwisp"
          : route === "knowledge"
            ? "All facts — Talkwisp"
            : route === "dialogs"
            ? "Dialogs — Talkwisp"
            : route === "payment"
              ? "Payment — Talkwisp"
            : route === "test"
              ? "Test — Talkwisp"
              : route === "settings"
                ? "Settings — Talkwisp"
                : "Knowledge base — Talkwisp";
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

  /* `owns` rather than a single route, because one rail item can be the home
     of several. Knowledge base lands on Add and stays lit while the owner is
     in Review or browsing the list -- those are stages of one task, not three
     places, which is the whole reason they stopped being three tabs. */
  const tab = (to: Route, label: string, owns: Route[] = []) => (
    <a
      href={to === "add" ? "#/" : `#/${to}`}
      style={{
        minHeight: 44,
        display: "flex",
        alignItems: "center",
        padding: "0 14px",
        borderRadius: "var(--radius-inner)",
        fontSize: 15,
        fontWeight: route === to || owns.includes(route) ? 700 : 600,
        color:
          route === to || owns.includes(route)
            ? "var(--accent-pressed)"
            : "var(--text-secondary)",
        background:
          route === to || owns.includes(route) ? "var(--accent-tint)" : "transparent",
        textDecoration: "none",
        /* The rail's width is set by its longest string and never truncated --
           design-dashboard.md calls that out because the longest one is
           Russian, and an ellipsis in a nav item is a label you cannot read. */
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </a>
  );

  /* PRICES BUT NO WAY TO TAKE PAYMENT.
   *
   * This belongs in the dashboard's live check, which does not exist yet --
   * Dashboard.dc.html is a prototype and design-dashboard.md is a design. The
   * approval band is the only always-visible surface in the built app, so it
   * carries this too.
   *
   * IT IS NOT A SETUP NAG. Payment is optional and most businesses will never
   * sell in chat, so this appears ONLY when the buy flow can actually fire and
   * cannot complete -- confirmed exact prices, and no language ready. Every
   * other combination shows nothing at all.
   *
   * It exists because the bot fix made the symptom invisible. Before it, the
   * customer hit a dead end and at least something visibly broke. Now they get
   * their price question answered properly, which is right, and the owner sees
   * nothing -- just silence where a sale would have been. This and the
   * once-a-day Telegram message are the only two signals left. */
  const payment = !me.approved ? null : <PaymentGap />;

  return (
    /* THE LEFT RAIL, from design-dashboard.md, and it is only now honest.
     *
     * It was a header until today for a stated reason: four of the seven
     * designed rail items had nothing behind them, and an inert nav pointing at
     * screens that do not exist is the dishonesty the design docs argue
     * against. All seven are built now, so the reason expired.
     *
     * NOTHING IN IT IS GREYED. A screen joined the rail only once it was
     * whole -- Dashboard and Gaps last -- because a greyed item reads as an
     * unfinished product every time the owner opens the app.
     *
     * On a narrow screen it wraps above the content rather than pinning to the
     * side. The bottom bar in the design is a better answer and is not this. */
    <div
      style={{
        minHeight: "100vh",
        background: "var(--ground)",
        display: "flex",
        alignItems: "flex-start",
        flexWrap: "wrap",
      }}
    >
      <nav
        style={{
          flex: "0 0 auto",
          width: 210,
          minHeight: "100vh",
          background: "var(--surface)",
          boxShadow: "1px 0 0 var(--rule)",
          padding: "18px 12px",
          display: "flex",
          flexDirection: "column",
          gap: 2,
          boxSizing: "border-box",
        }}
      >
        <div style={{ padding: "0 14px 16px" }}>
          <div style={{ fontSize: 18, fontWeight: 800, letterSpacing: "-0.01em" }}>
            Talkwisp
          </div>
        </div>

        {/* FIRST, AND ONLY NOW THAT IT IS WHOLE. It was kept out of the
            rail while it was a design, because this is where the eye lands and
            a greyed or half-built first item reads as an unfinished product
            every time the app opens. */}
        {tab("dashboard", "Dashboard")}
        {/* ONE ITEM FOR THREE ROUTES. Add, Review and the browsable list are
            three stages of a single task -- put something in, check what was
            read, look at what is there -- and three top-level tabs made an
            owner navigate between the steps of one job.

            The counter band on the landing screen is the router, which is what
            the design always showed: "6 waiting for review" was already a link
            to Review before this merge, and "214 facts" is now a link to the
            list. Its doors disappear when there is nothing behind them, which
            a persistent "Review (0)" tab could never do. */}
        {tab("add", "Knowledge base", ["review", "knowledge"])}
        {/* Where the design puts it: after the knowledge base, because a gap
            is closed by adding knowledge. */}
        {tab("gaps", "Gaps")}
        {/* "Dialogs", not "Customers". Customers was the framing we wanted --
            a contacts list -- and the data does not support it: names exist
            only from the moment the bot started capturing them, so older rows
            are chat ids. What the screen shows is conversations. */}
        {tab("dialogs", "Dialogs")}
        {tab("payment", "Payment")}
        {tab("test", "Test")}
        {tab("settings", "Settings")}

        <div style={{ marginTop: "auto", padding: "16px 14px 0" }}>
          {/* The signed-in address and a way out. Shown because a session that
              cannot be seen or ended is the one part of auth a person cannot
              verify for themselves, and on a shared machine that matters more
              than the space it costs. */}
          <div
            style={{
              fontSize: 12,
              color: "var(--text-faint)",
              marginBottom: 8,
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {me.email}
          </div>
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
        </div>
      </nav>

      <main style={{ flex: "1 1 520px", minWidth: 0 }}>
      {waiting}
      {payment}
      {route === "dashboard" ? (
        <Dashboard />
      ) : route === "gaps" ? (
        <Gaps />
      ) : route === "review" ? (
        <Review />
      ) : route === "knowledge" ? (
        <Knowledge />
      ) : route === "dialogs" ? (
        <Dialogs />
      ) : route === "payment" ? (
        <Payment />
      ) : route === "test" ? (
        <TestConsole />
      ) : route === "settings" ? (
        <Settings />
      ) : (
        <AddKnowledge />
      )}
      </main>
    </div>
  );
}


/* Its own component so the fetch does not re-run on every route change, and so
   a failure here cannot take the app down with it -- a band that cannot load
   should be absent, never an error screen over a working product. */
function PaymentGap() {
  const [gap, setGap] = useState(false);

  useEffect(() => {
    getPayment()
      .then((p) => setGap(p.has_prices && p.ready.length === 0))
      .catch(() => setGap(false));
  }, []);

  if (!gap) return null;
  return (
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
        Customers can't pay through your agent yet.
      </strong>{" "}
      You have prices in your knowledge base, so people do ask to buy — but with
      no card number saved, the agent answers the price question and stops
      there. <a href="#/payment">Add your card number</a> if you want it to take
      payment.
    </div>
  );
}

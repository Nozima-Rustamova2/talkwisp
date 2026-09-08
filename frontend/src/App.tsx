import { useEffect, useState } from "react";
import AddKnowledge from "./AddKnowledge";
import Review from "./Review";
import SignIn from "./SignIn";
import { getMe, logout, type Me } from "./api";

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

type Route = "add" | "review";

function routeFromHash(): Route {
  return window.location.hash.replace(/^#\/?/, "") === "review" ? "review" : "add";
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
      .then(setMe)
      .catch(() => setMe({ business: null, email: null }));
  }, []);

  if (me === "asking") return null;
  if (!me.business) return <SignIn />;

  useEffect(() => {
    const onChange = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  // The title is the only part of the page a hash route does not update by
  // itself. Left alone it says "Add knowledge" while the Review screen is on
  // screen, which is wrong in the browser tab, in history and in a bookmark.
  useEffect(() => {
    document.title =
      route === "review" ? "Review — Talkwisp" : "Add knowledge — Talkwisp";
  }, [route]);

  const tab = (to: Route, label: string) => (
    <a
      href={to === "add" ? "#/" : "#/review"}
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

      {route === "review" ? <Review /> : <AddKnowledge />}
    </div>
  );
}

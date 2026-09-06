import { useEffect, useState } from "react";
import AddKnowledge from "./AddKnowledge";
import Review from "./Review";

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
        </nav>
      </header>

      {route === "review" ? <Review /> : <AddKnowledge />}
    </div>
  );
}

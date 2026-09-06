import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  confirmFact,
  editFact,
  listConflicts,
  listProposals,
  rejectFact,
  type Conflict,
  type ConflictValue,
  type Proposal,
} from "./api";

/* Screen D — Review. Ported from prototype/Onboarding Review.dc.html, at ledger
 * density rather than the onboarding card, because this is the standalone
 * screen reached from "N waiting for review".
 *
 * Three departures from the prototype, all forced by the backend and all of
 * them the backend being right:
 *
 * 1. THE PROTOTYPE MAKES A CONFLICT A CHOICE -- "which is current?" with two
 *    buttons. The backend refuses to resolve conflicts at all: /conflicts
 *    "surfaces, never resolves", because two opening-hours values may both be
 *    true. So a contradiction is shown beside the proposal as information, and
 *    keeping this one does not delete the other. Turning it into a choice would
 *    invent a resolution the system does not have.
 *
 * 2. THERE IS NO UNDO, on Keep or on Remove. Confirming has no inverse endpoint
 *    and DELETE /review/{id} refuses confirmed facts on purpose. The prototype
 *    offers Undo on both. Rather than a button that 404s, Remove asks once and
 *    Keep says plainly that it is done.
 *
 * 3. "ACCEPTED BY DEFAULT" IS NOT ONE TAP. There is no bulk confirm endpoint,
 *    so Keep all is N requests, reports progress, and on failure says how many
 *    were actually kept. A single button that silently half-succeeded would be
 *    worse than the honest count.
 */

function pct(confidence: number | null): string | null {
  return confidence === null ? null : `${Math.round(confidence * 100)}%`;
}

/* Highlight the value inside the source excerpt when it appears there
 * literally. The API returns the excerpt and nothing about WHERE the fact came
 * from inside it, so this is a plain substring search and it fails silently: an
 * extractor that normalised the text -- and it often does -- gets a plain
 * excerpt with nothing highlighted. That is the correct outcome. Inventing a
 * fuzzy match would let the panel highlight a passage the fact did not come
 * from, which is worse than highlighting nothing on a screen whose whole job is
 * showing the owner where a fact came from. */
function Excerpt({ text, value }: { text: string; value: string }) {
  const at = text.indexOf(value);
  const body =
    at === -1 ? (
      text
    ) : (
      <>
        {text.slice(0, at)}
        <span style={{ fontWeight: 700, color: "var(--text)", background: "var(--caution-bg)" }}>
          {value}
        </span>
        {text.slice(at + value.length)}
      </>
    );
  return (
    <span
      style={{
        fontSize: 13,
        lineHeight: 1.55,
        color: "var(--text-faint)",
        whiteSpace: "pre-wrap",
        overflowWrap: "anywhere",
      }}
    >
      {body}
    </span>
  );
}

type RowState =
  | { kind: "open" }
  | { kind: "fixing"; subject: string; attribute: string; value: string }
  | { kind: "confirming" }
  | { kind: "kept"; alsoConflicts: ConflictValue[] }
  | { kind: "removing" }
  | { kind: "removed" }
  | { kind: "failed"; message: string };

export default function Review() {
  const [proposals, setProposals] = useState<Proposal[] | null>(null);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [rows, setRows] = useState<Record<string, RowState>>({});
  const [bulk, setBulk] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [q, c] = await Promise.all([listProposals(), listConflicts()]);
      setProposals(q);
      setConflicts(c);
      setLoadError(null);
    } catch (exc) {
      setLoadError(exc instanceof ApiError ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const state = (id: string): RowState => rows[id] ?? { kind: "open" };
  const set = (id: string, s: RowState) => setRows((p) => ({ ...p, [id]: s }));

  /* What a proposal already disagrees with. /conflicts groups by subject and
   * attribute and lists every value including this one, so the proposal itself
   * is filtered out -- otherwise every conflicted fact would appear to
   * contradict itself. */
  function contradicts(p: Proposal): ConflictValue[] {
    const group = conflicts.find((c) => c.values.some((v) => v.id === p.id));
    return group ? group.values.filter((v) => v.id !== p.id) : [];
  }

  async function keep(p: Proposal) {
    set(p.id, { kind: "confirming" });
    try {
      const result = await confirmFact(p.id);
      set(p.id, { kind: "kept", alsoConflicts: result.now_conflicts_with ?? [] });
    } catch (exc) {
      set(p.id, {
        kind: "failed",
        message: exc instanceof ApiError ? exc.message : String(exc),
      });
    }
  }

  async function saveFix(p: Proposal, s: Extract<RowState, { kind: "fixing" }>) {
    set(p.id, { kind: "confirming" });
    try {
      // Edit first, confirm second. If the confirm fails the correction is
      // still saved, which is the right way round: the owner's typing survives.
      await editFact(p.id, {
        subject: s.subject,
        attribute: s.attribute,
        value: s.value,
      });
      const result = await confirmFact(p.id);
      set(p.id, { kind: "kept", alsoConflicts: result.now_conflicts_with ?? [] });
    } catch (exc) {
      set(p.id, {
        kind: "failed",
        message: exc instanceof ApiError ? exc.message : String(exc),
      });
    }
  }

  async function remove(p: Proposal) {
    set(p.id, { kind: "confirming" });
    try {
      await rejectFact(p.id);
      set(p.id, { kind: "removed" });
    } catch (exc) {
      set(p.id, {
        kind: "failed",
        message: exc instanceof ApiError ? exc.message : String(exc),
      });
    }
  }

  /* No bulk endpoint exists, so this is N requests in sequence. It stops at the
   * first failure and says how many were kept, because "Keep all 12" that
   * actually kept 5 and said nothing is the failure this screen is for. */
  async function keepAll(open: Proposal[]) {
    let done = 0;
    for (const p of open) {
      setBulk(`Keeping ${done + 1} of ${open.length}…`);
      try {
        const result = await confirmFact(p.id);
        set(p.id, { kind: "kept", alsoConflicts: result.now_conflicts_with ?? [] });
        done += 1;
      } catch (exc) {
        setBulk(
          `Kept ${done} of ${open.length}, then stopped: ${
            exc instanceof ApiError ? exc.message : String(exc)
          }`,
        );
        return;
      }
    }
    setBulk(null);
  }

  const open = (proposals ?? []).filter((p) => state(p.id).kind === "open");

  return (
    <div
      style={{
        padding: 24,
        display: "flex",
        flexDirection: "column",
        gap: 20,
        maxWidth: 1360,
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <h1
          style={{
            margin: 0,
            fontSize: "clamp(26px, 3.2vw, 30px)",
            fontWeight: 800,
            lineHeight: 1.12,
            letterSpacing: "-0.02em",
          }}
        >
          Check what it read
        </h1>
        <p style={{ margin: 0, fontSize: 15, lineHeight: 1.55, color: "var(--text-muted)" }}>
          {proposals === null
            ? "Loading what's waiting…"
            : proposals.length === 0
              ? "Nothing is waiting. Facts you typed yourself never appear here — you wrote them, so there was never anything to check."
              : `${proposals.length} ${
                  proposals.length === 1 ? "fact" : "facts"
                } came out of your files. Facts you typed yourself aren't listed — you wrote them, so there was nothing to check.`}
        </p>
      </div>

      {loadError && (
        <section
          className="card"
          style={{ padding: "16px 18px", fontSize: 15, color: "var(--caution-text)" }}
        >
          {loadError}
        </section>
      )}

      {proposals !== null && proposals.length === 0 && !loadError && (
        <section className="card" style={{ padding: "16px 18px" }}>
          <a href="#/" style={{ fontSize: 15, fontWeight: 600 }}>
            Add more knowledge →
          </a>
        </section>
      )}

      {proposals !== null && proposals.length > 0 && (
        <section
          className="card"
          style={{ padding: 20, display: "flex", flexDirection: "column", gap: 12 }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              justifyContent: "space-between",
              gap: 12,
              flexWrap: "wrap",
            }}
          >
            <h2 style={{ margin: 0, fontSize: 21, fontWeight: 700, letterSpacing: "-0.01em" }}>
              {open.length === 0
                ? "All sorted."
                : open.length === 1
                  ? "One thing needs you"
                  : `${open.length} things need you`}
            </h2>
            {open.length > 1 && (
              <button
                type="button"
                className="control control-quiet"
                style={{ fontSize: 14 }}
                disabled={bulk !== null}
                onClick={() => void keepAll(open)}
              >
                {bulk ?? `Keep all ${open.length}`}
              </button>
            )}
          </div>

          <div style={{ display: "flex", flexDirection: "column" }}>
            {proposals.map((p) => {
              const s = state(p.id);
              const settled = s.kind === "kept" || s.kind === "removed";
              const against = contradicts(p);
              const confidence = pct(p.confidence);
              return (
                <div
                  key={p.id}
                  style={{
                    borderTop: "1px solid var(--rule)",
                    padding: "14px 0",
                    display: "flex",
                    flexDirection: "column",
                    gap: 10,
                  }}
                >
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
                      gap: "10px 20px",
                      alignItems: "start",
                    }}
                  >
                    <div
                      style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}
                    >
                      <span
                        style={{
                          fontSize: 16,
                          fontWeight: 600,
                          lineHeight: 1.45,
                          color: s.kind === "removed" ? "var(--text-faint)" : "var(--text)",
                          textDecoration: s.kind === "removed" ? "line-through" : "none",
                          overflowWrap: "anywhere",
                        }}
                      >
                        {p.subject} — {p.attribute}: {p.value}
                      </span>
                      <div
                        style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}
                      >
                        {confidence && (
                          // The threshold is a display choice with no meaning in
                          // the backend -- confidence is a number the extractor
                          // reported, not a category it assigned. The number is
                          // always shown so the styling never hides it.
                          <span
                            className={`tag ${
                              (p.confidence ?? 1) < 0.75 ? "tag-caution" : "tag-confirmed"
                            }`}
                          >
                            {(p.confidence ?? 1) < 0.75
                              ? `Not sure — ${confidence}`
                              : confidence}
                          </span>
                        )}
                        <span style={{ fontSize: 13, color: "var(--text-faint)" }}>
                          {p.source?.kind === "file" ? "Read from a file" : "Read from a paste"}
                        </span>
                      </div>
                    </div>

                    {p.source?.excerpt ? (
                      <div
                        style={{
                          minWidth: 0,
                          background: "var(--surface-subtle)",
                          borderRadius: "var(--radius-inner)",
                          padding: "10px 12px",
                          display: "flex",
                          flexDirection: "column",
                          gap: 6,
                        }}
                      >
                        <Excerpt text={p.source.excerpt} value={p.value} />
                        <span style={{ fontSize: 12, color: "var(--text-faint)" }}>
                          {p.source.filename || p.source.label || "source"}
                          {/* File-level provenance only. The design docs are
                              explicit that sheet, row and page provenance is not
                              stored yet and must not be drawn. */}
                        </span>
                      </div>
                    ) : (
                      <span style={{ fontSize: 13, color: "var(--text-faint)" }}>
                        No source text stored for this one.
                      </span>
                    )}
                  </div>

                  {/* A contradiction is INFORMATION, not a question. Keeping
                      this fact does not remove the other one. */}
                  {against.length > 0 && !settled && (
                    <div
                      style={{
                        background: "var(--accent-tint)",
                        border: "1px solid var(--accent-tint-border)",
                        borderRadius: "var(--radius-control)",
                        padding: "12px 14px",
                        display: "flex",
                        flexDirection: "column",
                        gap: 4,
                      }}
                    >
                      <span style={{ fontSize: 14, fontWeight: 700, color: "var(--accent-pressed)" }}>
                        This answers something you already have, differently
                      </span>
                      {against.map((v) => (
                        <span
                          key={v.id}
                          style={{ fontSize: 14, lineHeight: 1.5, overflowWrap: "anywhere" }}
                        >
                          · {v.value}{" "}
                          <span style={{ color: "var(--text-faint)" }}>
                            ({v.typed ? "you typed this" : "read from a file"}
                            {v.confirmed ? ", confirmed" : ", also waiting"})
                          </span>
                        </span>
                      ))}
                      <span style={{ fontSize: 13, lineHeight: 1.5, color: "var(--text-faint)" }}>
                        Keeping this one does not remove the other. Both may be true — the agent
                        is told about both.
                      </span>
                    </div>
                  )}

                  {s.kind === "fixing" ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                      <div
                        style={{
                          display: "grid",
                          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
                          gap: 10,
                        }}
                      >
                        <input
                          className="field"
                          value={s.subject}
                          onChange={(e) => set(p.id, { ...s, subject: e.target.value })}
                        />
                        <input
                          className="field"
                          value={s.attribute}
                          onChange={(e) => set(p.id, { ...s, attribute: e.target.value })}
                        />
                        <input
                          className="field"
                          value={s.value}
                          onChange={(e) => set(p.id, { ...s, value: e.target.value })}
                        />
                      </div>
                      <div
                        style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}
                      >
                        <button
                          type="button"
                          className="control control-primary"
                          onClick={() => void saveFix(p, s)}
                        >
                          Save and keep
                        </button>
                        <button
                          type="button"
                          className="control control-quiet"
                          onClick={() => set(p.id, { kind: "open" })}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : s.kind === "removing" ? (
                    <div
                      style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}
                    >
                      {/* Asked once, because there is no undo. */}
                      <span style={{ fontSize: 14, color: "var(--caution-text)" }}>
                        Remove it? This can't be undone.
                      </span>
                      <button
                        type="button"
                        className="control control-secondary"
                        onClick={() => void remove(p)}
                      >
                        Remove
                      </button>
                      <button
                        type="button"
                        className="control control-quiet"
                        onClick={() => set(p.id, { kind: "open" })}
                      >
                        Keep it for now
                      </button>
                    </div>
                  ) : s.kind === "confirming" ? (
                    <span style={{ fontSize: 14, color: "var(--text-muted)" }}>Saving…</span>
                  ) : s.kind === "kept" ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <span style={{ fontSize: 14, color: "var(--text-faint)" }}>
                        Kept. It's in the knowledge base now — change it there, not here.
                      </span>
                      {s.alsoConflicts.length > 0 && (
                        <span
                          style={{ fontSize: 13, lineHeight: 1.5, color: "var(--caution-text)" }}
                        >
                          It now disagrees with{" "}
                          {s.alsoConflicts.map((v) => v.value).join("; ")} — both are kept.
                        </span>
                      )}
                    </div>
                  ) : s.kind === "removed" ? (
                    <span style={{ fontSize: 14, color: "var(--text-faint)" }}>
                      Removed. The extraction was wrong, so nothing was lost.
                    </span>
                  ) : s.kind === "failed" ? (
                    <div
                      style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}
                    >
                      <span style={{ fontSize: 14, color: "var(--caution-text)" }}>
                        {s.message}
                      </span>
                      <button
                        type="button"
                        className="control control-quiet"
                        onClick={() => set(p.id, { kind: "open" })}
                      >
                        Try again
                      </button>
                    </div>
                  ) : (
                    <div
                      style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}
                    >
                      <button
                        type="button"
                        className="control control-primary"
                        onClick={() => void keep(p)}
                      >
                        Keep
                      </button>
                      <button
                        type="button"
                        className="control control-secondary"
                        onClick={() =>
                          set(p.id, {
                            kind: "fixing",
                            subject: p.subject,
                            attribute: p.attribute,
                            value: p.value,
                          })
                        }
                      >
                        Fix
                      </button>
                      <button
                        type="button"
                        className="control control-quiet"
                        onClick={() => set(p.id, { kind: "removing" })}
                      >
                        Remove
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}

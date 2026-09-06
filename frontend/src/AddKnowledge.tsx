import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  PASTE_LIMIT,
  createPaste,
  extractSource,
  getStats,
  listSources,
  parseFact,
  uploadFile,
  writeFact,
  type FactResult,
  type Source,
  type Stats,
} from "./api";

/* Screen C — Add knowledge. Ported from prototype/Add Knowledge.dc.html and
 * wired to the real API. Where this diverges from the prototype it is because
 * the backend does not have the thing the prototype drew; each divergence is
 * commented where it happens rather than collected in a list nobody reads.
 *
 * The page chrome (header, nav) lives in App.tsx. This is the page body. */

// ---------------------------------------------------------------- small parts

function ago(iso: string | null): string {
  if (!iso) return "";
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

function size(bytes: number | null): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/* A row in the activity band. Three columns on a wide screen, stacked below
 * 260px per column -- the 360px Android case the direction doc calls out. */
function Row(props: {
  name: string;
  kind: string;
  state: string;
  stateColor?: string;
  note?: string;
  tag?: { text: string; caution?: boolean };
  actions?: React.ReactNode;
}) {
  return (
    <div
      style={{
        borderTop: "1px solid var(--rule)",
        padding: "14px 0",
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
        gap: "8px 20px",
        alignItems: "start",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
        <span
          style={{
            fontSize: 15,
            fontWeight: 600,
            lineHeight: 1.4,
            color: "var(--text)",
            overflowWrap: "anywhere",
          }}
        >
          {props.name}
        </span>
        <span style={{ fontSize: 13, color: "var(--text-faint)" }}>{props.kind}</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
        <span
          style={{
            fontSize: 15,
            lineHeight: 1.45,
            color: props.stateColor ?? "var(--text)",
          }}
        >
          {props.state}
        </span>
        {props.note && (
          <span style={{ fontSize: 13, lineHeight: 1.45, color: "var(--text-faint)" }}>
            {props.note}
          </span>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
        {props.tag && (
          <span className={`tag ${props.tag.caution ? "tag-caution" : "tag-confirmed"}`}>
            {props.tag.text}
          </span>
        )}
        {props.actions}
      </div>
    </div>
  );
}

// A fact the owner typed during THIS visit. Deliberately not persisted and not
// re-fetched: a typed fact is confirmed on write, and /review only lists
// unconfirmed ones, so there is no endpoint that could bring these back after a
// reload. They disappear on refresh, which is honest -- the fact is in the
// knowledge base either way, only this band forgets it.
type TypedRow = { id: string; line: string; parsed: FactResult["parsed"]; at: string };

export default function AddKnowledge() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Sources this visit put through extraction, with what came back. /source
  // does not report how many facts a source produced, so a count is only shown
  // for the ones we watched happen.
  const [freshCounts, setFreshCounts] = useState<Record<string, number>>({});
  const [reading, setReading] = useState<Record<string, true>>({});

  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [readBusy, setReadBusy] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const [typedText, setTypedText] = useState("");
  const [longPaste, setLongPaste] = useState(false);
  const [preview, setPreview] = useState<FactResult | null>(null);
  const [typedBusy, setTypedBusy] = useState(false);
  const [typedError, setTypedError] = useState<string | null>(null);
  const [typedRows, setTypedRows] = useState<TypedRow[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [s, list] = await Promise.all([getStats(), listSources()]);
      setStats(s);
      setSources(list);
      setLoadError(null);
    } catch (exc) {
      setLoadError(exc instanceof ApiError ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  /* Create the source, then read it. Two calls because the API is two calls,
   * and the second one is slow: transcription, extraction, prose chunking and
   * embedding all happen inside it. No spinner -- the row says "Reading". */
  async function ingest(create: () => Promise<Source>) {
    setReadBusy(true);
    setReadError(null);
    let source: Source;
    try {
      source = await create();
    } catch (exc) {
      setReadError(exc instanceof ApiError ? exc.message : String(exc));
      setReadBusy(false);
      return;
    }
    setSources((prev) => [source, ...prev]);
    setReading((prev) => ({ ...prev, [source.id]: true }));
    setPasteOpen(false);
    setPasteText("");
    setReadBusy(false);

    try {
      const result = await extractSource(source.id);
      if (result.status === "extracted") {
        setFreshCounts((prev) => ({ ...prev, [source.id]: result.facts.length }));
      }
    } catch (exc) {
      // The source row itself carries the failure once refreshed; this only
      // covers the request never arriving.
      setReadError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setReading((prev) => {
        const next = { ...prev };
        delete next[source.id];
        return next;
      });
      void refresh();
    }
  }

  /* Typed facts are TWO steps, and this is a deliberate divergence from the
   * prototype, which shows the read-back after writing. app/main.py is explicit
   * about why: "A mis-parse written blind becomes a confirmed fact, and
   * confirmed is precisely what nothing downstream questions." The first call
   * also returns anything already confirmed that answers this subject and
   * attribute differently, which the prototype's read-back had no way to show.
   * One extra tap on the only path that cannot fail; worth it. */
  async function showPreview() {
    const line = typedText.trim();
    if (!line) return;
    setTypedBusy(true);
    setTypedError(null);
    try {
      setPreview(await parseFact(line));
    } catch (exc) {
      setTypedError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setTypedBusy(false);
    }
  }

  async function commitFact() {
    const line = typedText.trim();
    setTypedBusy(true);
    setTypedError(null);
    try {
      const written = await writeFact(line);
      setTypedRows((prev) => [
        {
          id: written.id ?? line,
          line,
          parsed: written.parsed,
          at: new Date().toISOString(),
        },
        ...prev,
      ]);
      setTypedText("");
      setPreview(null);
      setLongPaste(false);
      void refresh();
    } catch (exc) {
      setTypedError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setTypedBusy(false);
    }
  }

  const first = stats !== null && stats.facts === 0;
  const overLimit = pasteText.length > PASTE_LIMIT;

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
            Add knowledge
          </h1>
          <p style={{ margin: 0, fontSize: 15, color: "var(--text-muted)" }}>
            One knowledge base. Everything your agent can answer from.
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

        {/* Status plate. Real counts or nothing -- there is no placeholder
            number here, because a plate that reads "214 facts" before it has
            loaded is the one thing on this screen nobody would think to doubt. */}
        <section className="card" style={{ padding: "16px 18px" }}>
          {stats === null ? (
            <span style={{ fontSize: 15, color: "var(--text-faint)" }}>
              Counting what the agent knows…
            </span>
          ) : first ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, maxWidth: "78ch" }}>
              <span
                style={{
                  fontSize: 22,
                  fontWeight: 800,
                  letterSpacing: "-0.02em",
                  lineHeight: 1.15,
                }}
              >
                Your agent doesn't know anything yet.
              </span>
              <span style={{ fontSize: 15, lineHeight: 1.55, color: "var(--text-muted)" }}>
                The fastest first move is a photo of your printed price list — one photo is
                usually thirty or forty facts. Or write a fact yourself on the right; that
                takes ten seconds and always works.
              </span>
            </div>
          ) : (
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: "10px 24px",
                flexWrap: "wrap",
              }}
            >
              <span style={{ fontSize: 16, color: "var(--text-secondary)" }}>
                The agent knows{" "}
                <strong style={{ fontWeight: 800, fontSize: 19, color: "var(--text)" }}>
                  {stats.facts} {stats.facts === 1 ? "fact" : "facts"}
                </strong>{" "}
                from{" "}
                <strong style={{ fontWeight: 800, fontSize: 19, color: "var(--text)" }}>
                  {stats.sources} {stats.sources === 1 ? "source" : "sources"}
                </strong>
              </span>
              {stats.last_added && (
                <span style={{ fontSize: 14, color: "var(--text-faint)" }}>
                  last added {ago(stats.last_added)}
                </span>
              )}
              {stats.facts_awaiting_review > 0 && (
                // Plain grey and no arrow, deliberately: the accented links on
                // this screen are the ones in the activity band, and a second
                // accent up here would compete with them.
                <a
                  href="#/review"
                  style={{
                    minHeight: 44,
                    display: "inline-flex",
                    alignItems: "center",
                    fontSize: 14,
                    fontWeight: 600,
                    color: "var(--text-muted)",
                  }}
                >
                  {stats.facts_awaiting_review} waiting for review
                </a>
              )}
            </div>
          )}
        </section>

        <section
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))",
            gap: 20,
            alignItems: "stretch",
          }}
        >
          {/* ------------------------------------------------- read zone */}
          <div
            className="card"
            style={{ padding: 20, display: "flex", flexDirection: "column", gap: 14 }}
          >
            <h2 style={{ margin: 0, fontSize: 21, fontWeight: 700, letterSpacing: "-0.01em" }}>
              Give it something to read
            </h2>

            {!pasteOpen ? (
              <div
                style={{
                  background: "var(--surface-subtle)",
                  border: "1px dashed #c8d5ea",
                  borderRadius: "var(--radius-control)",
                  padding: "30px 20px",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 14,
                  textAlign: "center",
                }}
              >
                <span style={{ fontSize: 17, fontWeight: 700, lineHeight: 1.35 }}>
                  Choose a file from your phone, or paste text
                </span>
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap", justifyContent: "center" }}>
                  <button
                    type="button"
                    className="control control-secondary"
                    disabled={readBusy}
                    onClick={() => fileInput.current?.click()}
                  >
                    {readBusy ? "Sending…" : "Choose file"}
                  </button>
                  <button
                    type="button"
                    className="control control-secondary"
                    onClick={() => setPasteOpen(true)}
                  >
                    Paste text
                  </button>
                </div>
                <input
                  ref={fileInput}
                  type="file"
                  style={{ display: "none" }}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = "";
                    if (file) void ingest(() => uploadFile(file));
                  }}
                />
                {/* No drag-and-drop: the zone is a click target only, exactly as
                    the prototype records. */}
                <span style={{ fontSize: 13, lineHeight: 1.5, color: "var(--text-faint)" }}>
                  Photo, PDF or text · a blurry photo is fine to retry
                </span>
              </div>
            ) : (
              <div
                style={{
                  background: "var(--accent-tint)",
                  border: "1px solid var(--accent-tint-border)",
                  borderRadius: "var(--radius-control)",
                  padding: 14,
                  display: "flex",
                  flexDirection: "column",
                  gap: 10,
                }}
              >
                <label
                  style={{
                    fontSize: 14,
                    fontWeight: 700,
                    color: overLimit ? "var(--caution-text)" : "var(--accent-pressed)",
                  }}
                >
                  Pasted text · {pasteText.length} characters
                  {overLimit && ` · ${PASTE_LIMIT} is the most that fits`}
                </label>
                <textarea
                  className="field"
                  rows={6}
                  autoFocus
                  value={pasteText}
                  onChange={(e) => setPasteText(e.target.value)}
                />
                <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="control control-primary"
                    disabled={readBusy || overLimit || !pasteText.trim()}
                    onClick={() => void ingest(() => createPaste(pasteText.trim()))}
                  >
                    {readBusy ? "Sending…" : "Read this text"}
                  </button>
                  <button
                    type="button"
                    className="control control-quiet"
                    onClick={() => {
                      setPasteOpen(false);
                      setPasteText("");
                    }}
                  >
                    Cancel
                  </button>
                  <span style={{ fontSize: 13, color: "var(--text-faint)" }}>
                    {overLimit
                      ? "Too long to send in one go — split it, or save it as a file and choose that instead."
                      : "You'll review what it reads."}
                  </span>
                </div>
              </div>
            )}

            {readError && (
              <span style={{ fontSize: 14, lineHeight: 1.45, color: "var(--caution-text)" }}>
                {readError}
              </span>
            )}

            {/* The Google Sheets line the prototype puts here has no backend at
                all, so it is not drawn. */}
          </div>

          {/* ------------------------------------------------ typed zone */}
          <div
            className="card"
            style={{ padding: 20, display: "flex", flexDirection: "column", gap: 14 }}
          >
            <h2 style={{ margin: 0, fontSize: 21, fontWeight: 700, letterSpacing: "-0.01em" }}>
              Write a fact yourself
            </h2>

            <textarea
              className="field"
              rows={3}
              placeholder={
                "Rasulova Nigora, kardiolog, dushanbadan jumagacha 09:00–14:00\nПриём — примерно 200 000–300 000 сум"
              }
              value={typedText}
              onChange={(e) => {
                setTypedText(e.target.value);
                if (preview) setPreview(null);
              }}
              onPaste={(e) => {
                const text = e.clipboardData.getData("text") || "";
                if (text.length > 180 || text.split("\n").length > 4) setLongPaste(true);
              }}
            />

            {longPaste && (
              <div
                style={{
                  background: "var(--accent-tint)",
                  border: "1px solid var(--accent-tint-border)",
                  borderRadius: "var(--radius-control)",
                  padding: "12px 14px",
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                }}
              >
                <span style={{ fontSize: 14, lineHeight: 1.5 }}>
                  That's a lot of text for facts you're writing yourself. Send it to be read
                  instead, and you'll get a list to check?
                </span>
                <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="control control-quiet"
                    style={{ color: "var(--accent-pressed)", fontWeight: 700, fontSize: 14 }}
                    onClick={() => {
                      setPasteText(typedText);
                      setTypedText("");
                      setPreview(null);
                      setLongPaste(false);
                      setPasteOpen(true);
                    }}
                  >
                    Read it instead →
                  </button>
                  <button
                    type="button"
                    className="control control-quiet"
                    style={{ fontSize: 14 }}
                    onClick={() => setLongPaste(false)}
                  >
                    No, these are my own words
                  </button>
                </div>
              </div>
            )}

            {/* The read-back, BEFORE the write. What the parser understood, and
                anything confirmed that already answers this differently. */}
            {preview && (
              <div
                style={{
                  background: preview.error ? "var(--caution-bg)" : "var(--accent-tint)",
                  border: `1px solid ${
                    preview.error ? "var(--caution-bg)" : "var(--accent-tint-border)"
                  }`,
                  borderRadius: "var(--radius-control)",
                  padding: 14,
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                }}
              >
                {preview.error ? (
                  <span
                    style={{ fontSize: 14, lineHeight: 1.5, color: "var(--caution-text)" }}
                  >
                    {preview.error}
                  </span>
                ) : (
                  <>
                    <span
                      style={{ fontSize: 13, fontWeight: 700, color: "var(--accent-pressed)" }}
                    >
                      This is what it understood — nothing is written yet
                    </span>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 16px" }}>
                      <span style={{ fontSize: 15 }}>
                        <span style={{ color: "var(--text-faint)" }}>Subject </span>
                        {preview.parsed?.subject}
                      </span>
                      <span style={{ fontSize: 15 }}>
                        <span style={{ color: "var(--text-faint)" }}>Attribute </span>
                        {preview.parsed?.attribute}
                      </span>
                      <span style={{ fontSize: 15 }}>
                        <span style={{ color: "var(--text-faint)" }}>Value </span>
                        {preview.parsed?.value}
                      </span>
                    </div>
                    {preview.conflicts?.length > 0 && (
                      <div
                        style={{
                          display: "flex",
                          flexDirection: "column",
                          gap: 4,
                          fontSize: 14,
                          lineHeight: 1.5,
                          color: "var(--caution-text)",
                        }}
                      >
                        <span>
                          {preview.conflicts.length === 1
                            ? "Something already confirmed answers this differently:"
                            : "Confirmed facts already answer this differently:"}
                        </span>
                        {/* Show the value, not just the fact of disagreement. A
                            warning that says "there is a conflict" makes the
                            owner go looking; the old number beside the new one
                            lets them decide here. */}
                        {preview.conflicts.map((c, i) => (
                          <span key={i} style={{ overflowWrap: "anywhere" }}>
                            · {c.value}
                          </span>
                        ))}
                        <span>Adding yours keeps both. Neither is deleted.</span>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {typedError && (
              <span style={{ fontSize: 14, lineHeight: 1.45, color: "var(--caution-text)" }}>
                {typedError}
              </span>
            )}

            <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              {preview && !preview.error ? (
                <>
                  <button
                    type="button"
                    className="control control-primary"
                    disabled={typedBusy}
                    onClick={() => void commitFact()}
                  >
                    {typedBusy ? "Adding…" : "Yes, add it"}
                  </button>
                  <button
                    type="button"
                    className="control control-quiet"
                    onClick={() => setPreview(null)}
                  >
                    Not right — let me edit
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="control control-primary"
                  disabled={typedBusy || !typedText.trim()}
                  onClick={() => void showPreview()}
                >
                  {typedBusy ? "Reading…" : "Add to knowledge base"}
                </button>
              )}
            </div>
            <p style={{ margin: 0, fontSize: 13, lineHeight: 1.5, color: "var(--text-faint)" }}>
              You wrote it, so it's confirmed straight away with no review — which is why you
              see what was understood before it's written.
            </p>
            {/* "Use fields instead" is not here: /fact takes one free-text line
                and there is no structured write endpoint, so the fields would
                only be assembled back into a line the parser re-reads. */}
          </div>
        </section>

        {/* ------------------------------------------------- activity band */}
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
              What you've added
            </h2>
            <span style={{ fontSize: 14, color: "var(--text-muted)" }}>
              {sources.filter((s) => s.status === "extracted").length} read ·{" "}
              {Object.keys(reading).length} reading ·{" "}
              {sources.filter((s) => s.status === "failed").length} couldn't be read
            </span>
          </div>

          <div style={{ display: "flex", flexDirection: "column" }}>
            {typedRows.map((row) => (
              <Row
                key={row.id}
                name={`«${row.line}»`}
                kind={`Typed by you · ${ago(row.at)}`}
                state={
                  row.parsed
                    ? `${row.parsed.subject} — ${row.parsed.attribute}: ${row.parsed.value}`
                    : "Added to the knowledge base"
                }
                tag={{ text: "Confirmed" }}
              />
            ))}

            {sources.map((source) => {
              const busy = reading[source.id];
              const count = freshCounts[source.id];
              return (
                <Row
                  key={source.id}
                  name={
                    source.filename ||
                    source.label ||
                    (source.content ?? "").slice(0, 70) ||
                    "Pasted text"
                  }
                  kind={[
                    source.kind === "file" ? source.media_type || "File" : "Pasted text",
                    size(source.size),
                    ago(source.created_at),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                  state={
                    busy
                      ? "Reading — this can take a minute"
                      : source.status === "failed"
                        ? "We couldn't read this."
                        : source.status === "extracted"
                          ? count !== undefined
                            ? `${count} ${count === 1 ? "fact" : "facts"} read`
                            : "Read"
                          : "Waiting to be read"
                  }
                  stateColor={
                    busy
                      ? "var(--accent-pressed)"
                      : source.status === "failed"
                        ? "var(--caution-text)"
                        : "var(--text)"
                  }
                  note={
                    busy
                      ? "You can add another file or type a fact while this runs."
                      : source.status === "failed"
                        ? `${source.error ?? ""} Nothing was added. Your other files kept going.`
                        : undefined
                  }
                  tag={
                    source.status === "failed"
                      ? { text: "Couldn't read", caution: true }
                      : undefined
                  }
                  actions={
                    !busy && source.status !== "extracted" ? (
                      <button
                        type="button"
                        className="control control-quiet"
                        style={{ fontSize: 14 }}
                        onClick={() => {
                          setReading((p) => ({ ...p, [source.id]: true }));
                          void extractSource(source.id)
                            .then((r) => {
                              if (r.status === "extracted")
                                setFreshCounts((p) => ({ ...p, [source.id]: r.facts.length }));
                            })
                            .catch(() => undefined)
                            .finally(() => {
                              setReading((p) => {
                                const n = { ...p };
                                delete n[source.id];
                                return n;
                              });
                              void refresh();
                            });
                        }}
                      >
                        {source.status === "failed" ? "Retry" : "Read it now"}
                      </button>
                    ) : undefined
                  }
                />
              );
            })}
          </div>

          {sources.length === 0 && typedRows.length === 0 && (
            <span style={{ fontSize: 13, lineHeight: 1.5, color: "var(--text-faint)" }}>
              Nothing added yet. Whatever you add appears here with what was read from it.
            </span>
          )}
        </section>
    </div>
  );
}

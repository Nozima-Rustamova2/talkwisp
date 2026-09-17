import { useState } from "react";
import {
  answerGap,
  ApiError,
  dismissGap,
  parseFact,
  writeFact,
  type FactResult,
  type Gap,
} from "./api";

/* The rows of "what it doesn't know yet", used by BOTH the Dashboard column and
 * the Gaps screen -- one component, so a row cannot behave differently
 * depending on where it is shown.
 *
 * "ADD ANSWER" OPENS A COMPOSER IN THE ROW. It never navigates. The design
 * marks this do-not-refactor: closing a gap in one place, in under a minute, on
 * a phone, is the whole reason the dashboard earns a return visit.
 *
 * TWO STEPS, like Add knowledge: preview the parse, then write. A mis-parse
 * written blind becomes a confirmed fact, and a gap closed by a wrong fact is
 * worse than an open one -- it stops being visible.
 *
 * After any close the parent RE-FETCHES. The row is never dropped locally,
 * because a list edited in the browser is a second definition of "open". */

function Row({ gap, onClosed }: { gap: Gap; onClosed: () => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<FactResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(step: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await step();
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  const check = () => run(async () => setPreview(await parseFact(text.trim())));

  const save = () =>
    run(async () => {
      const written = await writeFact(text.trim());
      if (written.error || !written.id) {
        // Nothing was written, so there is nothing to close the gap with.
        setPreview(written);
        return;
      }
      await answerGap(gap.question_key, written.id);
      onClosed();
    });

  const notForUs = () =>
    run(async () => {
      await dismissGap(gap.question_key);
      onClosed();
    });

  return (
    <li style={{ padding: "12px 0", borderTop: "1px solid var(--rule)" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        {/* The customer's own words and script, as they arrived. */}
        <span style={{ fontSize: 15, minWidth: 0, overflowWrap: "anywhere" }}>
          {gap.question ?? gap.question_key}
        </span>
        <span style={{ fontSize: 13, color: "var(--text-faint)", whiteSpace: "nowrap" }}>
          {gap.asked} {gap.asked === 1 ? "person" : "people"} asked
        </span>
      </div>

      {!open ? (
        <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
          <button className="control control-primary" onClick={() => setOpen(true)}>
            Add answer
          </button>
          <button className="control control-quiet" disabled={busy} onClick={notForUs}>
            Not for us
          </button>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
          <textarea
            className="control"
            rows={2}
            autoFocus
            value={text}
            placeholder="Write the answer as a fact, e.g. October Intensive is online"
            onChange={(e) => {
              setText(e.target.value);
              setPreview(null);
            }}
            style={{ width: "100%", boxSizing: "border-box", resize: "vertical" }}
          />

          {preview ? (
            <div style={{ fontSize: 14 }}>
              {preview.error ? (
                <span style={{ color: "var(--caution-text)" }}>{preview.error}</span>
              ) : preview.parsed ? (
                <>
                  <span style={{ color: "var(--text-muted)" }}>Will save: </span>
                  <strong>{preview.parsed.subject}</strong> — {preview.parsed.attribute}:{" "}
                  {preview.parsed.value}
                  {preview.conflicts.length > 0 ? (
                    <div style={{ color: "var(--caution-text)", marginTop: 4 }}>
                      Already confirmed differently:{" "}
                      {preview.conflicts.map((c) => c.value).join(", ")}
                    </div>
                  ) : null}
                </>
              ) : null}
            </div>
          ) : null}

          {error ? (
            <span style={{ fontSize: 14, color: "var(--caution-text)" }}>{error}</span>
          ) : null}

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {preview && !preview.error && preview.parsed ? (
              <button className="control control-primary" disabled={busy} onClick={save}>
                Save and close
              </button>
            ) : (
              <button
                className="control control-primary"
                disabled={busy || !text.trim()}
                onClick={check}
              >
                Check
              </button>
            )}
            <button
              className="control control-quiet"
              onClick={() => {
                setOpen(false);
                setPreview(null);
                setError(null);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

export default function GapRows({ gaps, onClosed }: { gaps: Gap[]; onClosed: () => void }) {
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
      {gaps.map((gap) => (
        <Row key={gap.question_key} gap={gap} onClosed={onClosed} />
      ))}
    </ul>
  );
}

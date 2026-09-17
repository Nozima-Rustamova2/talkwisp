import { useEffect, useState } from "react";
import GapRows from "./GapRows";
import { getBoard, type Board } from "./api";

/* The board: are you working, is it worth it, what to do next.
 *
 * THE PLATE IS THE HONESTY CLAIM. The reason this screen exists is the ManyChat
 * teardown in docs/competitors.md -- onboarding completed, every step green,
 * and the bot was dead. So "Agent is answering" is a three-part check the
 * server measures rather than a tick that means a row exists, and when it is
 * false the plate names the FIRST blocker with the fix beside it rather than
 * stacking three problems nobody reads.
 *
 * NEVER A DISABLED BUTTON. When a blocker has no action the owner can take --
 * approval is ours to do, not theirs -- the slot is empty rather than greyed.
 *
 * ONE ACTION, TWO VIEWS. The "what it doesn't know yet" column is the top of
 * the same server list the Gaps screen shows. Closing a gap here re-fetches the
 * board; nothing is removed in the browser, so the column can never show a list
 * the Gaps screen would not. */

function Plate({ plate }: { plate: Board["plate"] }) {
  const ok = plate.answering;
  return (
    <div className="card" style={{ padding: 24, marginBottom: 16 }}>
      <div style={{ fontSize: 24, fontWeight: 700, letterSpacing: "-0.01em" }}>
        {ok ? "Agent is answering" : plate.blocker?.says}
      </div>

      {!ok && plate.blocker?.fix ? (
        <a
          href={plate.blocker.href ?? "#/"}
          className="control control-primary"
          style={{
            display: "inline-flex",
            alignItems: "center",
            minHeight: 44,
            marginTop: 14,
            textDecoration: "none",
          }}
        >
          {plate.blocker.fix}
        </a>
      ) : null}

      {/* One row of small facts, under the line. Deliberately quiet: they are
          context for the sentence above, not four more things to read. */}
      <div
        style={{
          display: "flex",
          gap: "6px 20px",
          flexWrap: "wrap",
          marginTop: 16,
          fontSize: 13,
          color: "var(--text-faint)",
        }}
      >
        <span>{plate.channel_live ? "Telegram live" : "Telegram not running"}</span>
        <span>
          {plate.facts} confirmed {plate.facts === 1 ? "fact" : "facts"}
        </span>
        {plate.waiting_for_review > 0 ? (
          <a href="#/review" style={{ color: "var(--text-muted)" }}>
            {plate.waiting_for_review} waiting for review
          </a>
        ) : null}
        <span>{plate.plan}</span>
      </div>
    </div>
  );
}

function Figures({ week }: { week: Board["week"] }) {
  const peak = Math.max(1, ...week.days);
  const figure = (value: string | number, unit: string, big = false) => (
    <div style={{ flex: "1 1 140px", minWidth: 0 }}>
      <div style={{ fontSize: big ? 32 : 26, fontWeight: 700, lineHeight: 1.15 }}>
        {value}
      </div>
      <div style={{ fontSize: 13, color: "var(--text-faint)", marginTop: 2 }}>{unit}</div>
    </div>
  );

  return (
    <div className="card" style={{ padding: 24, marginBottom: 16 }}>
      <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
        {figure(week.answered, "answered")}
        {figure(week.unanswered, "couldn't answer")}
        {figure(week.handed_over, "handed to you")}
        {/* RIGHTMOST AND LARGEST, because it is the figure that says the
            product works -- and the one most easily made to lie, which is why
            the server returns null rather than a percentage below the floor.
            Three questions with one handoff reads as 67% and looks like
            failure when nothing is wrong. */}
        {week.share === null
          ? figure("—", `answered without you (needs ${week.floor}+ questions)`, true)
          : figure(`${week.share}%`, "answered without you", true)}
      </div>

      {/* Seven bars, plain divs. No chart library, nothing animated. They sum
          to the figures above by construction -- one function decides which
          questions count, and both read it. */}
      <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 40, marginTop: 20 }}>
        {week.days.map((n, i) => (
          <div
            key={i}
            title={`${n} ${n === 1 ? "question" : "questions"}`}
            style={{
              flex: 1,
              height: `${Math.max(2, (n / peak) * 40)}px`,
              background: n ? "var(--accent-tint)" : "var(--rule)",
              borderRadius: 2,
            }}
          />
        ))}
      </div>
      <div style={{ fontSize: 12, color: "var(--text-faint)", marginTop: 6 }}>
        Last 7 days · {week.counted} {week.counted === 1 ? "question" : "questions"}
      </div>
    </div>
  );
}

function Unknown({ unknown, onClosed }: { unknown: Board["unknown"]; onClosed: () => void }) {
  return (
    <div className="card" style={{ padding: 24, flex: "1 1 320px", minWidth: 0 }}>
      <h2 style={{ margin: "0 0 12px", fontSize: 16 }}>What it doesn't know yet</h2>
      {unknown.open === 0 ? (
        <p style={{ margin: 0, color: "var(--text-muted)" }}>
          Nothing so far. Every question customers asked, it could answer.
        </p>
      ) : (
        <>
          <GapRows gaps={unknown.gaps} onClosed={onClosed} />
          {/* The footer link to the full list. The composer above is NOT a
              route there -- this link is for the rest of the list only. */}
          {unknown.open > unknown.gaps.length ? (
            <a
              href="#/gaps"
              style={{ display: "inline-block", marginTop: 12, fontSize: 14, fontWeight: 600 }}
            >
              See all {unknown.open} open questions
            </a>
          ) : null}
        </>
      )}
    </div>
  );
}

function Attention({ items }: { items: Board["attention"] }) {
  return (
    <div className="card" style={{ padding: 24, flex: "1 1 320px", minWidth: 0 }}>
      <h2 style={{ margin: "0 0 12px", fontSize: 16 }}>Needs your attention</h2>
      {items.length === 0 ? (
        /* A GENUINE AND CELEBRATED STATE, not an apology and not a placeholder
           for a list that will appear later. */
        <p style={{ margin: 0, color: "var(--text-muted)" }}>Nothing needs you.</p>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {items.map((item) => (
            <li
              key={item.kind}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: 12,
                flexWrap: "wrap",
                padding: "10px 0",
                borderTop: "1px solid var(--rule)",
              }}
            >
              <span style={{ fontSize: 14 }}>{item.says}</span>
              {/* Each one line with a verb. An item with nowhere to go says
                  what it is and offers nothing, rather than linking somewhere
                  that cannot help. */}
              {item.href ? (
                <a href={item.href} style={{ fontSize: 14, fontWeight: 600 }}>
                  {item.fix}
                </a>
              ) : (
                <span style={{ fontSize: 13, color: "var(--text-faint)" }}>{item.fix}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function Dashboard() {
  const [board, setBoard] = useState<Board | "loading" | "error">("loading");

  const load = () =>
    getBoard()
      .then(setBoard)
      .catch(() => setBoard("error"));

  useEffect(() => {
    void load();
  }, []);

  const shell = { maxWidth: 860, margin: "0 auto", padding: "24px 20px 64px" };

  if (board === "loading")
    return (
      <div style={shell}>
        <p style={{ color: "var(--text-faint)" }}>Loading…</p>
      </div>
    );
  if (board === "error")
    return (
      <div style={shell}>
        <p>Could not load the dashboard.</p>
      </div>
    );

  return (
    <div style={shell}>
      <Plate plate={board.plate} />
      <Figures week={board.week} />
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
        <Unknown unknown={board.unknown} onClosed={() => void load()} />
        <Attention items={board.attention} />
      </div>
    </div>
  );
}

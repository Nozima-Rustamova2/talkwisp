import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  consoleAsk,
  consoleFeedback,
  consoleSuggestions,
  getStats,
  spendingAllowed,
  type ConsoleAnswer,
  type NextStep,
  type Nearest,
  type Provenance,
  type Suggestion,
} from "./api";

/* Talk to your agent, before anyone else can.
 *
 * WHY THIS SCREEN EXISTS. Until now an owner confirmed a fact and then opened
 * Telegram to find out whether it worked -- switching apps to check their own
 * work, against the live bot their customers can also reach. Everything here
 * was already answerable by the backend; there was just nowhere to ask from.
 *
 * IT IS NOT A CHAT. Each turn is independent: there is no conversation history
 * and no follow-up resolution, because `rewrite()` in app/followup.py runs off
 * the BOT's per-chat memory and this screen has none. A thread that looked like
 * Telegram but did not resolve "and on Sunday?" would be worse than one that
 * plainly does not try.
 *
 * WHAT IS DELIBERATELY NOT DRAWN. The prototype shows five provenance lines. A
 * live call returned TWENTY-EIGHT, one of them used -- and two of those were
 * contradicting opening-hours facts, one typed and one read off a price list.
 * Showing all of it first would bury the answer; hiding it would conceal a real
 * disagreement in the owner's own data. So: what was used, then a control that
 * says how many more there are. */

type Turn = {
  id: number;
  question: string;
  result: ConsoleAnswer;
  verdict?: "right" | "wrong";
  next?: NextStep;
  judging?: boolean;
};

const BLOCKED =
  "Your account is not approved yet, so this is switched off. " +
  "We will email you when it is ready.";

function label(p: Provenance): string {
  if (p.origin === "typed") return "you typed this";
  return p.source_label || p.source_filename || "a file you uploaded";
}

function body(p: Provenance): string {
  if (p.kind === "chunk") return `«${p.quote ?? ""}»`;
  return `${p.subject} · ${p.attribute} · ${p.value}`;
}

export default function TestConsole() {
  const [ready, setReady] = useState<"asking" | "empty" | "ok">("asking");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [celebrated, setCelebrated] = useState(false);
  const nextId = useRef(1);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    /* Confirmed facts, not sources and not proposals: `stats.facts` counts what
       the agent will actually answer with. A business with nine documents in
       the review queue and nothing confirmed has nothing to answer from, and
       telling it otherwise sends someone to ask questions that must all fail. */
    getStats()
      .then((s) => setReady(s.facts > 0 ? "ok" : "empty"))
      .catch(() => setReady("ok"));
  }, []);

  useEffect(() => {
    if (ready !== "ok") return;
    /* Free -- suggestions come from find(), the deterministic path, with no
       model call. Worth knowing before wiring a spinner to it. */
    consoleSuggestions(3)
      .then(setSuggestions)
      .catch(() => setSuggestions([]));
  }, [ready]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length]);

  /* EVERY HOOK ABOVE THIS LINE, EVERY RETURN BELOW IT. App.tsx carries the
     write-up: two early returns above two effects made the effects conditional,
     React counted hooks by position, and the tree unmounted -- for signed-in
     users only, which is to say the moment the feature started working. */

  async function ask(question: string, fromSuggestion = false) {
    const q = question.trim();
    if (!q || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await consoleAsk(q, fromSuggestion);
      setTurns((t) => [...t, { id: nextId.current++, question: q, result }]);
      setText("");
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.reason === "not_approved"
            ? BLOCKED
            : e.message
          : "Something went wrong.",
      );
    } finally {
      setBusy(false);
    }
  }

  /* `reason` is the second round trip. Marking something wrong can come back
     with a `choose` step -- "is one of these facts wrong, or did it use the
     wrong one?" -- which is the one distinction code cannot make, so the owner
     is asked instead of guessed at. Sending the answer calls this again, and
     that costs a second full generation. */
  async function judge(
    turn: Turn,
    verdict: "right" | "wrong",
    reason?: string,
  ) {
    if (turn.judging) return;
    setTurns((t) =>
      t.map((x) => (x.id === turn.id ? { ...x, judging: true } : x)),
    );
    try {
      const stored = await consoleFeedback(turn.question, verdict, reason);
      setTurns((t) =>
        t.map((x) =>
          x.id === turn.id
            ? { ...x, verdict, next: stored.next_step, judging: false }
            : x,
        ),
      );
      /* The celebration, once per session and only on a Right. It is the design's
         moment and it is cheap to get wrong: firing it on every Right would make
         it wallpaper, and firing it on a Wrong would be congratulating someone
         for finding a bug. */
      if (verdict === "right") setCelebrated(true);
    } catch (e) {
      setTurns((t) =>
        t.map((x) => (x.id === turn.id ? { ...x, judging: false } : x)),
      );
      setError(
        e instanceof ApiError && e.reason === "not_approved"
          ? BLOCKED
          : "Could not save that.",
      );
    }
  }

  if (ready === "asking") return null;

  const shell = {
    maxWidth: 760,
    margin: "0 auto",
    padding: "24px 20px 64px",
  } as const;

  if (ready === "empty") {
    return (
      <div style={shell}>
        <div className="card" style={{ padding: 28, textAlign: "center" }}>
          <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 6 }}>
            There's nothing for it to answer from yet.
          </div>
          <p
            style={{
              margin: "0 0 18px",
              fontSize: 14,
              lineHeight: 1.6,
              color: "var(--text-faint)",
            }}
          >
            The agent only answers from facts you have confirmed. Add something
            first, confirm it in Review, then come back and ask it anything.
          </p>
          <a className="control control-primary" href="#/">
            Add something first
          </a>
        </div>
      </div>
    );
  }

  return (
    <div style={shell}>
      <div style={{ marginBottom: 18 }}>
        <h1 style={{ fontSize: 20, fontWeight: 800, margin: "0 0 4px" }}>
          Talk to your agent
        </h1>
        <p style={{ margin: 0, fontSize: 14, color: "var(--text-faint)" }}>
          Exactly what a customer would get. Nothing you type here reaches
          anyone.
        </p>
      </div>

      {celebrated && (
        <div
          className="card"
          style={{
            padding: "12px 16px",
            marginBottom: 16,
            background: "var(--accent-tint, #eef4f0)",
            fontSize: 15,
            fontWeight: 600,
          }}
        >
          That's your agent working.
        </div>
      )}

      {turns.length === 0 && suggestions.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <div
            style={{
              fontSize: 13,
              color: "var(--text-faint)",
              marginBottom: 8,
            }}
          >
            {/* The claim is checkable, not decorative: each suggestion is
                generated from a confirmed fact and verified through the exact
                tier, so retrieval will reach it. The agent can still refuse,
                which the server counts rather than assumes away. */}
            Try one of these — they all have answers in what you added
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {suggestions.map((s) => (
              <button
                key={s.question}
                type="button"
                className="control control-secondary"
                disabled={busy || !spendingAllowed}
                title={spendingAllowed ? s.resolves_to : BLOCKED}
                onClick={() => void ask(s.question, true)}
                style={{ fontSize: 14 }}
              >
                {s.question}
              </button>
            ))}
          </div>
        </div>
      )}

      {turns.map((turn) => (
        <TurnView
          key={turn.id}
          turn={turn}
          expanded={!!expanded[turn.id]}
          onToggle={() =>
            setExpanded((e) => ({ ...e, [turn.id]: !e[turn.id] }))
          }
          onJudge={(v, reason) => void judge(turn, v, reason)}
        />
      ))}

      <div ref={bottom} />

      {error && (
        <p
          style={{
            margin: "12px 0",
            fontSize: 13,
            color: "var(--danger, #a4362f)",
          }}
        >
          {error}
        </p>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void ask(text);
        }}
        style={{ display: "flex", gap: 8, marginTop: 20 }}
      >
        <input
          className="field"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={!spendingAllowed}
          /* Uzbek, because that is what customers write and what the seeded
             data answers. An English placeholder on a screen whose content is
             Uzbek and Russian would be the shell pretending to be the product. */
          placeholder="Ish vaqtingiz qanday?"
          style={{ flex: 1 }}
        />
        <button
          type="submit"
          className="control control-primary"
          disabled={busy || !text.trim() || !spendingAllowed}
          title={spendingAllowed ? undefined : BLOCKED}
        >
          {busy ? "Asking…" : "Ask"}
        </button>
      </form>
    </div>
  );
}

function TurnView({
  turn,
  expanded,
  onToggle,
  onJudge,
}: {
  turn: Turn;
  expanded: boolean;
  onToggle: () => void;
  onJudge: (v: "right" | "wrong", reason?: string) => void;
}) {
  const { result } = turn;
  const used = result.provenance.filter((p) => p.used);
  const rest = result.provenance.filter((p) => !p.used);
  /* ON STATUS, NOT ON `answer === null`. A live call came back with
     status "unknown" AND a real Uzbek sentence -- the model was asked and
     declined politely. Branching on null would have hidden the agent's own
     words behind wording of mine, and the wording of mine would only ever have
     appeared in the rarer case where the model was never called at all. */
  const refused = result.status !== "ok";
  const silent = result.answer === null;

  return (
    <div style={{ marginBottom: 26 }}>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
        <div
          style={{
            background: "#1f6feb",
            color: "#fff",
            padding: "9px 13px",
            borderRadius: 14,
            borderBottomRightRadius: 6,
            maxWidth: "80%",
            fontSize: 15,
            lineHeight: 1.5,
          }}
        >
          {turn.question}
        </div>
      </div>

      <div style={{ display: "flex", marginBottom: 10 }}>
        <div
          style={{
            background: "var(--surface, #f4f6f9)",
            padding: "9px 13px",
            borderRadius: 14,
            borderBottomLeftRadius: 6,
            maxWidth: "80%",
            fontSize: 15,
            lineHeight: 1.55,
            border: "1px solid var(--rule)",
          }}
        >
          {/* A refusal is the product working, not an error, so it is styled as
              an answer and not as a failure. "The agent never invents an answer"
              is the whole argument; drawing its one visible consequence in red
              would teach an owner to treat correct behaviour as a fault. */}
          {silent ? (
            <span style={{ color: "var(--text-faint)" }}>
              It doesn't know, and said nothing rather than guessing. Nothing
              you have confirmed came close enough to answer this.
            </span>
          ) : (
            /* The agent's own words, including when it is refusing. Its
               refusals are written in the customer's language and are part of
               the product; replacing them with English of mine would show the
               owner something no customer will ever see. */
            result.answer
          )}
        </div>
      </div>

      {refused && !silent && (
        /* A caption, not a replacement. The bubble above holds what the
           customer would have received; this says why it reads the way it
           does, to an owner who might otherwise think it broke. */
        <div
          style={{
            fontSize: 13,
            color: "var(--text-faint)",
            marginBottom: 8,
          }}
        >
          {result.status === "triage"
            ? "Read as a health complaint, so it deliberately did not answer from your data."
            : result.status === "ambiguous"
              ? "More than one of your entries could be meant, so it asked rather than picking."
              : "It declined — nothing you have confirmed answers this."}
        </div>
      )}

      {result.wrong_script && (
        <div
          style={{
            fontSize: 13,
            color: "var(--danger, #a4362f)",
            marginBottom: 8,
          }}
        >
          The reply came back in a different alphabet from the question. That is
          a bug in the agent, not something you can fix here.
        </div>
      )}

      {result.provenance.length > 0 && (
        <div
          className="card"
          style={{ padding: "12px 14px", marginBottom: 10, fontSize: 13 }}
        >
          {result.used_detection === "unavailable_cross_script" ? (
            /* Not "0 sources used". Text matching cannot cross alphabets, so
               the honest report is that the check could not run -- an Uzbek
               policy answering a Russian customer is the normal case here. */
            <div style={{ color: "var(--text-faint)", marginBottom: 8 }}>
              We can't mark what ended up in the answer — the reply and your
              material are in different alphabets, so matching them is not
              possible. Everything it looked at is below.
            </div>
          ) : (
            <div style={{ fontWeight: 700, marginBottom: 8 }}>
              {used.length > 0
                ? `Appears in the answer (${used.length})`
                : "Nothing it looked at appears word-for-word in the answer"}
            </div>
          )}

          {used.map((p) => (
            <Line key={p.id} p={p} />
          ))}

          {rest.length > 0 && (
            <>
              <button
                type="button"
                className="control control-quiet"
                onClick={onToggle}
                style={{ fontSize: 13, marginTop: used.length ? 8 : 0 }}
              >
                {expanded
                  ? "Hide the rest"
                  : `It also looked at ${rest.length} more`}
              </button>
              {expanded && (
                <div style={{ marginTop: 8 }}>
                  <div
                    style={{
                      color: "var(--text-faint)",
                      marginBottom: 6,
                      lineHeight: 1.5,
                    }}
                  >
                    Considered but not used word-for-word. If two of these
                    disagree with each other, both are in your knowledge base
                    and one of them is probably wrong.
                  </div>
                  {rest.map((p) => (
                    <Line key={p.id} p={p} />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {turn.verdict ? (
        <div style={{ fontSize: 13, lineHeight: 1.55 }}>
          <span
            className={turn.verdict === "right" ? "tag tag-confirmed" : "tag tag-caution"}
          >
            {turn.verdict === "right" ? "Marked right" : "Marked wrong"}
          </span>
          {turn.next?.text && (
            <p style={{ margin: "8px 0 0", color: "var(--text-secondary)" }}>
              {/* The server's own sentence, for all five cases. The screen does
                  not compose advice: an owner sent to fix a fact when the real
                  problem is a missing alias will edit correct data. */}
              {turn.next.text}
            </p>
          )}
          {/* THE FIVE THINGS THAT ALMOST ANSWERED, with their scores. The
              server's sentence says "if one of the entries below should have,
              your wording and the customer's differ" -- which is only
              actionable if the entries are actually shown. The first version of
              this screen linked to Review instead and threw the list away. */}
          {turn.next?.action === "review_nearest" && turn.next.nearest?.length ? (
            <div style={{ marginTop: 8 }}>
              {turn.next.nearest.map((n: Nearest, i: number) => (
                <div key={i} style={{ marginBottom: 4, lineHeight: 1.5 }}>
                  <span>
                    {n.subject} · {n.attribute} · {n.value}
                  </span>{" "}
                  <span style={{ color: "var(--text-faint)" }}>
                    {n.similarity !== null
                      ? `· scored ${n.similarity.toFixed(2)}`
                      : ""}
                  </span>
                </div>
              ))}
              <a
                href="#/"
                style={{ display: "inline-block", marginTop: 6, fontSize: 13 }}
              >
                Add what's missing →
              </a>
            </div>
          ) : null}

          {/* THE ONE QUESTION CODE CANNOT ANSWER. A wrong answer built from
              retrieved facts is either a wrong fact or a right fact used
              wrongly, and those look identical from the server. Asking costs a
              second generation and is the most valuable record this product
              makes. */}
          {turn.next?.action === "choose" && !turn.next.chosen && (
            <div style={{ marginTop: 8 }}>
              {turn.next.facts?.map((f) => (
                <div key={f.id} style={{ marginBottom: 4, lineHeight: 1.5 }}>
                  {f.subject} · {f.attribute} · {f.value}
                </div>
              ))}
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                <button
                  type="button"
                  className="control control-quiet"
                  disabled={turn.judging || !spendingAllowed}
                  title={spendingAllowed ? undefined : BLOCKED}
                  onClick={() => onJudge("wrong", "fact_is_wrong")}
                  style={{ fontSize: 13 }}
                >
                  One of these is wrong
                </button>
                <button
                  type="button"
                  className="control control-quiet"
                  disabled={turn.judging || !spendingAllowed}
                  title={spendingAllowed ? undefined : BLOCKED}
                  onClick={() => onJudge("wrong", "used_the_wrong_fact")}
                  style={{ fontSize: 13 }}
                >
                  It used the wrong one
                </button>
              </div>
            </div>
          )}

          {turn.next?.action === "choose" && turn.next.chosen && (
            <p style={{ margin: "8px 0 0", color: "var(--text-faint)" }}>
              {turn.next.chosen === "fact_is_wrong"
                ? "Noted — fix it in Review, and the agent stops saying it."
                : "Noted. That is a retrieval problem, not a wrong fact; nothing for you to edit."}{" "}
              <a href="#/review">Open Review →</a>
            </p>
          )}
        </div>
      ) : (
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ fontSize: 13, color: "var(--text-faint)" }}>
            Is that right?
          </span>
          <button
            type="button"
            className="control control-quiet"
            disabled={turn.judging || !spendingAllowed}
            title={spendingAllowed ? undefined : BLOCKED}
            onClick={() => onJudge("right")}
            style={{ fontSize: 13 }}
          >
            Right
          </button>
          <button
            type="button"
            className="control control-quiet"
            disabled={turn.judging || !spendingAllowed}
            title={spendingAllowed ? undefined : BLOCKED}
            onClick={() => onJudge("wrong")}
            style={{ fontSize: 13 }}
          >
            Wrong
          </button>
          {turn.judging && (
            <span style={{ fontSize: 13, color: "var(--text-faint)" }}>
              Saving…
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function Line({ p }: { p: Provenance }) {
  return (
    <div style={{ marginBottom: 6, lineHeight: 1.5 }}>
      <span>{body(p)}</span>{" "}
      <span style={{ color: "var(--text-faint)" }}>· {label(p)}</span>
    </div>
  );
}

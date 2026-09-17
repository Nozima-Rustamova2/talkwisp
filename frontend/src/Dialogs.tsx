import { useEffect, useState } from "react";
import {
  getCustomers,
  getExchange,
  type Customer,
  type Customers,
  type Turn,
} from "./api";

/* Dialogs: who wrote to the agent, and what was said.
 *
 * IT WAS CALLED CUSTOMERS AND THAT WAS THE WRONG NAME. The framing was a
 * contacts list -- who they are, how to reach them -- and the data does not
 * support it. Names are only captured from the moment bot.py started recording
 * them, so every older row is a ten-digit chat id, and what the screen actually
 * shows is conversations. Naming it after the thing it shows is the honest
 * version; naming it after the thing we wanted is how a screen ends up
 * apologising for itself.
 *
 * TWO PANES, NOT STACKED. The first version put the exchange BELOW the table,
 * so opening a conversation pushed the list off screen and reading a second one
 * meant scrolling back up to find where you were. A list you lose when you use
 * it is not a list. On a narrow screen the panes wrap and stack, which is the
 * one place the old behaviour is right.
 *
 * NO REPLY BOX, still and deliberately. Takeover lives in Telegram: the owner is
 * not at a laptop at 9pm, the flow already works there, and the landing page
 * promises it in three languages. */

function when(iso: string | null): string {
  if (!iso) return "—";
  const at = new Date(iso);
  const days = Math.floor((Date.now() - at.getTime()) / 86400000);
  if (days === 0) return at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return at.toLocaleDateString();
}

/* A display name, or honestly nothing. Telegram gives a first name on nearly
   every account and a username on many, but neither is guaranteed and both are
   missing on every line written before the bot captured them. Inventing
   "Customer 4448" would make a stranger look like a record the owner has. */
function label(c: Customer): string {
  if (c.first_name) return c.first_name;
  if (c.username) return `@${c.username}`;
  return `Chat ${c.chat_id}`;
}

/* --- the transcript as a chat ------------------------------------------------
 *
 * TELEGRAM'S LAYOUT, OUR LOOK. The business is on the right, where "you" are in
 * Telegram, because the owner reading this is that side of the conversation.
 * The customer is on the left.
 *
 * The server sends one entry per customer message, carrying its answer. Here
 * each entry is split back into the two things that were said, so the screen
 * shows two parties rather than a list of question-answer records. */

type Side = "customer" | "agent";

type Message = {
  side: Side;
  text: string | null;
  at: string | null;
  /* What became of it -- "Sent to you". Metadata about the message, drawn under
     the bubble and never inside it: it was not said to anyone. */
  note: string | null;
};

// Consecutive messages from one side closer together than this share a group
// and one timestamp. Five minutes is roughly where Telegram stops grouping.
const GROUP_GAP_MS = 5 * 60 * 1000;

function messages(turns: Turn[]): Message[] {
  const out: Message[] = [];
  for (const t of turns) {
    if (t.question) out.push({ side: "customer", text: t.question, at: t.at, note: null });
    if (t.answer) {
      out.push({ side: "agent", text: t.answer, at: t.at, note: t.note });
    } else if (t.note) {
      // Something happened with no reply text -- a greeting short-circuit, a
      // screenshot, an order. The note is still information, so it attaches to
      // whatever came before it, or stands alone when nothing did.
      const last = out[out.length - 1];
      if (last && last.at === t.at && !last.note) last.note = t.note;
      else out.push({ side: "agent", text: null, at: t.at, note: t.note });
    }
  }
  return out;
}

function ms(iso: string | null): number | null {
  return iso ? new Date(iso).getTime() : null;
}

function sameGroup(a: Message, b: Message): boolean {
  if (a.side !== b.side || a.note) return false; // a note closes its group
  const x = ms(a.at);
  const y = ms(b.at);
  return x !== null && y !== null && y - x <= GROUP_GAP_MS;
}

function day(iso: string | null): string {
  if (!iso) return "";
  const at = new Date(iso);
  const today = new Date();
  const yesterday = new Date(Date.now() - 86400000);
  if (at.toDateString() === today.toDateString()) return "Today";
  if (at.toDateString() === yesterday.toDateString()) return "Yesterday";
  return at.toLocaleDateString([], { day: "numeric", month: "long", year: "numeric" });
}

function clock(iso: string | null): string {
  return iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
}

function Transcript({ turns }: { turns: Turn[] }) {
  const list = messages(turns);
  return (
    <div style={{ display: "flex", flexDirection: "column", marginTop: 16 }}>
      {list.map((m, i) => {
        const prev = list[i - 1];
        const next = list[i + 1];
        const startsGroup = !prev || !sameGroup(prev, m);
        const endsGroup = !next || !sameGroup(m, next);
        const newDay = !prev || day(prev.at) !== day(m.at);
        const right = m.side === "agent";
        return (
          <div key={i}>
            {newDay && m.at ? (
              <div style={{ textAlign: "center", margin: i === 0 ? "0 0 12px" : "16px 0 12px" }}>
                <span
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--text-muted)",
                    background: "var(--ground)",
                    borderRadius: 999,
                    padding: "3px 10px",
                  }}
                >
                  {day(m.at)}
                </span>
              </div>
            ) : null}

            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: right ? "flex-end" : "flex-start",
                marginTop: startsGroup && !newDay ? 12 : 2,
              }}
            >
              {m.text ? (
                <div
                  style={{
                    maxWidth: "min(78%, 560px)",
                    padding: "8px 12px",
                    background: right ? "var(--accent-tint-strong)" : "var(--ground)",
                    color: "var(--text)",
                    // The corner nearest its own side is tightened on the last
                    // bubble of a group only -- that is what makes a run of
                    // bubbles read as one speaker.
                    borderRadius: 16,
                    borderBottomRightRadius: right && endsGroup ? 6 : 16,
                    borderBottomLeftRadius: !right && endsGroup ? 6 : 16,
                    fontSize: 14,
                    lineHeight: 1.5,
                    whiteSpace: "pre-wrap",
                    overflowWrap: "anywhere",
                  }}
                >
                  {m.text}
                  {endsGroup && m.at ? (
                    // Floated so a short last line keeps the time beside it,
                    // the way Telegram does, and a long one pushes it below.
                    <span
                      style={{
                        float: "right",
                        fontSize: 11,
                        color: "var(--text-faint)",
                        margin: "6px 0 -2px 10px",
                        lineHeight: 1.2,
                      }}
                    >
                      {clock(m.at)}
                    </span>
                  ) : null}
                </div>
              ) : null}

              {m.note ? (
                <div
                  style={{
                    fontSize: 12,
                    color: "var(--text-faint)",
                    marginTop: 4,
                    padding: "0 4px",
                  }}
                >
                  {m.note}
                  {!m.text && m.at ? ` · ${clock(m.at)}` : ""}
                </div>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Exchange({ customer }: { customer: Customer }) {
  const [turns, setTurns] = useState<Turn[] | "loading">("loading");

  useEffect(() => {
    let live = true;
    setTurns("loading");
    getExchange(customer.chat_id)
      .then((rows) => live && setTurns(rows))
      .catch(() => live && setTurns([]));
    return () => {
      live = false;
    };
  }, [customer.chat_id]);

  return (
    <>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 16 }}>{label(customer)}</strong>
        {/* The one action, and it leaves for Telegram rather than pretending to
            be a chat client. Only when there is a username: t.me/<id> is not a
            thing, and a link that 404s is worse than no link. */}
        {customer.username ? (
          <a
            href={`https://t.me/${customer.username}`}
            target="_blank"
            rel="noreferrer"
            style={{ fontSize: 13 }}
          >
            Message on Telegram
          </a>
        ) : (
          <span style={{ fontSize: 13, color: "var(--text-faint)" }}>
            No Telegram username — reachable only when they write again
          </span>
        )}
      </div>

      {turns === "loading" ? (
        <p style={{ color: "var(--text-faint)", fontSize: 13 }}>Loading…</p>
      ) : turns.length === 0 ? (
        <p style={{ color: "var(--text-faint)", fontSize: 13 }}>Nothing recorded.</p>
      ) : (
        <Transcript turns={turns} />
      )}
    </>
  );
}

export default function Dialogs() {
  const [data, setData] = useState<Customers | "loading" | "error">("loading");
  const [picked, setPicked] = useState<Customer | null>(null);

  useEffect(() => {
    getCustomers()
      .then(setData)
      .catch(() => setData("error"));
  }, []);

  const shell = { maxWidth: 1100, margin: "0 auto", padding: "24px 20px 64px" };
  // THE MOST RECENT CONVERSATION IS OPEN BY DEFAULT. At one conversation an
  // empty right pane saying "pick someone" is a screen with nothing on it, and
  // the list is sorted newest first, so the first entry is the one to read.
  const open = picked ?? (data !== "loading" && data !== "error" ? data.customers[0] ?? null : null);

  if (data === "loading")
    return (
      <div style={shell}>
        <p style={{ color: "var(--text-faint)" }}>Loading…</p>
      </div>
    );
  if (data === "error")
    return (
      <div style={shell}>
        <p>Could not load dialogs.</p>
      </div>
    );

  return (
    <div style={shell}>
      {data.people === 0 ? (
        <div className="card" style={{ padding: 20 }}>
          <p style={{ margin: 0, color: "var(--text-muted)" }}>
            Nobody has written to your agent yet. When they do, they appear here.
          </p>
        </div>
      ) : (
        <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
          {/* Wrapping rather than a media query, because everything else on
              these screens is an inline style. Below roughly 760px the panes
              stack, which is the one place the old layout was right. */}
          {/* PROPORTIONS. The list keeps about the width of a Telegram chat
              list and the transcript takes everything else: the growth factors
              send spare width to the side that has something to read. When the
              panes wrap on a phone, the list fills the row on its own. */}
          <div className="card" style={{ flex: "1 1 260px", maxWidth: "100%", minWidth: 0, padding: 8 }}>
            {/* THE NUMBERS LIVE HERE NOW, not in a card of their own. At one
                conversation that card was a dashboard with nothing on it; as
                the list's heading they describe the list, which is true at one
                conversation and at five hundred. */}
            <div style={{ padding: "8px 12px 10px", borderBottom: "1px solid var(--rule)", marginBottom: 6 }}>
              <div style={{ fontSize: 16, fontWeight: 700 }}>Dialogs</div>
              <div style={{ fontSize: 12, color: "var(--text-faint)", marginTop: 2 }}>
                {data.people} {data.people === 1 ? "person" : "people"}
                {" · "}
                {data.conversations} {data.conversations === 1 ? "conversation" : "conversations"}
                {" · last "}
                {when(data.last_at)}
              </div>
            </div>
            {data.customers.map((c) => {
              const selected = open?.chat_id === c.chat_id;
              return (
                <button
                  key={c.chat_id}
                  onClick={() => setPicked(c)}
                  style={{
                    display: "block",
                    width: "100%",
                    textAlign: "left",
                    border: "none",
                    borderRadius: 10,
                    background: selected ? "var(--accent-tint, #eef4f0)" : "transparent",
                    padding: "10px 12px",
                    cursor: "pointer",
                    font: "inherit",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                    <strong style={{ fontSize: 14 }}>{label(c)}</strong>
                    <span style={{ fontSize: 12, color: "var(--text-faint)", whiteSpace: "nowrap" }}>
                      {when(c.last_at)}
                    </span>
                  </div>
                  <div
                    style={{
                      fontSize: 13,
                      color: "var(--text-muted)",
                      marginTop: 2,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {c.last_question ?? "—"}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text-faint)", marginTop: 2 }}>
                    {c.conversations} {c.conversations === 1 ? "conversation" : "conversations"}
                    {" · "}
                    {c.messages} {c.messages === 1 ? "message" : "messages"}
                  </div>
                </button>
              );
            })}
          </div>

          <div className="card" style={{ flex: "999 1 420px", minWidth: 0, padding: 20 }}>
            {open ? (
              <Exchange customer={open} />
            ) : (
              <p style={{ margin: 0, color: "var(--text-faint)" }}>
                Pick someone on the left to read the conversation.
              </p>
            )}
          </div>
        </div>
      )}

      {/* Said out loud rather than quietly dropped: these lines were written
          before the bot recorded which business they belonged to, and guessing
          would show one business another's customers. */}
      {data.unattributed > 0 ? (
        <p style={{ fontSize: 13, color: "var(--text-faint)", marginTop: 20 }}>
          {data.unattributed} earlier {data.unattributed === 1 ? "message is" : "messages are"} not
          shown — they were logged before messages recorded which business they belonged to.
        </p>
      ) : null}
    </div>
  );
}

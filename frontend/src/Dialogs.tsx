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

function Figure({ value, unit }: { value: string | number; unit: string }) {
  return (
    <div>
      <div style={{ fontSize: 26, fontWeight: 600, lineHeight: 1.2 }}>{value}</div>
      <div style={{ fontSize: 13, color: "var(--text-faint)" }}>{unit}</div>
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
        <ol style={{ listStyle: "none", padding: 0, margin: "16px 0 0" }}>
          {turns.map((t, i) => (
            <li
              key={i}
              style={{
                marginBottom: 16,
                paddingBottom: 16,
                borderBottom:
                  i === turns.length - 1 ? "none" : "1px solid var(--rule)",
              }}
            >
              <div style={{ fontSize: 12, color: "var(--text-faint)", marginBottom: 3 }}>
                {when(t.at)}
              </div>
              {/* An entry with no question is something that happened rather
                  than something said -- a button tap, an order, a screenshot.
                  It gets the note alone, with no empty quotation above it. */}
              {t.question ? (
                <div style={{ fontWeight: 500, lineHeight: 1.5 }}>{t.question}</div>
              ) : null}
              {t.answer ? (
                <div
                  style={{
                    color: "var(--text-muted)",
                    marginTop: 4,
                    lineHeight: 1.55,
                  }}
                >
                  {t.answer}
                </div>
              ) : null}
              {t.note ? (
                <div style={{ fontSize: 13, color: "var(--text-faint)", marginTop: 4 }}>
                  {t.note}
                </div>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </>
  );
}

export default function Dialogs() {
  const [data, setData] = useState<Customers | "loading" | "error">("loading");
  const [open, setOpen] = useState<Customer | null>(null);

  useEffect(() => {
    getCustomers()
      .then(setData)
      .catch(() => setData("error"));
  }, []);

  const shell = { maxWidth: 1100, margin: "0 auto", padding: "24px 20px 64px" };

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
      {/* THE HEADER IS THE SCREEN AT TWELVE CONVERSATIONS and the list is the
          screen at five hundred. These three numbers are true at both. */}
      <div
        className="card"
        style={{ padding: 20, marginBottom: 16, display: "flex", gap: 36, flexWrap: "wrap" }}
      >
        <Figure value={data.people} unit={data.people === 1 ? "person" : "people"} />
        <Figure
          value={data.conversations}
          unit={data.conversations === 1 ? "conversation" : "conversations"}
        />
        <Figure value={when(data.last_at)} unit="last message" />
      </div>

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
          <div className="card" style={{ flex: "1 1 300px", minWidth: 0, padding: 8 }}>
            {data.customers.map((c) => {
              const selected = open?.chat_id === c.chat_id;
              return (
                <button
                  key={c.chat_id}
                  onClick={() => setOpen(c)}
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

          <div className="card" style={{ flex: "2 1 400px", minWidth: 0, padding: 20 }}>
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

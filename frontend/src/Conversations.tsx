import { useEffect, useState } from "react";
import {
  getCustomers,
  getExchange,
  type Customer,
  type Customers,
  type Turn,
} from "./api";

/* Who talked to the bot.
 *
 * NOT A LOG VIEWER, and the distinction is the whole reason this screen is
 * worth a nav item. Reading what the agent said is already better served by the
 * Test console, which shows provenance and scores for a question you choose. A
 * third view of the same answers would add a screen and no capability. What
 * nothing covers is seeing your CUSTOMERS -- for a course seller that is closer
 * to a contacts list than a log.
 *
 * THE HEADER IS THE SCREEN AT TWELVE CONVERSATIONS. The table below it only
 * starts earning its place in the hundreds, and a grid of column headings above
 * two rows reads as a broken product rather than a quiet one. So three numbers
 * carry it -- people, conversations, last activity -- and they are true at both
 * scales. Nothing here says "coming soon" or promises volume that does not
 * exist.
 *
 * NO REPLY BOX, deliberately. Takeover lives in Telegram: the owner is not at a
 * laptop at 9pm, the flow already works there, and the landing page promises it
 * in three languages. A reply box here would duplicate a working feature and
 * contradict a live promise. */

function when(iso: string | null): string {
  if (!iso) return "—";
  const at = new Date(iso);
  const days = Math.floor((Date.now() - at.getTime()) / 86400000);
  if (days === 0) return at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return at.toLocaleDateString();
}

/* A DISPLAY NAME, OR HONESTLY NOTHING.
 *
 * Telegram gives a first name on nearly every account and a username on many,
 * but neither is guaranteed and both are missing on every line written before
 * the bot started capturing them. Inventing "Customer 4448" to fill the column
 * would make a stranger look like a record the owner already has. A chat id,
 * shown as a chat id, is the truth. */
function label(c: Customer): string {
  if (c.first_name) return c.first_name;
  if (c.username) return `@${c.username}`;
  return `Chat ${c.chat_id}`;
}

function Exchange({ customer, onClose }: { customer: Customer; onClose: () => void }) {
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
    <div style={{ marginTop: 16, borderTop: "1px solid var(--rule)", paddingTop: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <strong style={{ fontSize: 16 }}>{label(customer)}</strong>
        {/* THE ONE ACTION ON THIS SCREEN, and it leaves for Telegram rather
            than pretending to be a chat client. Only offered when there is a
            username: t.me/<id> is not a thing, and a link that 404s is worse
            than no link. */}
        {customer.username ? (
          <a
            href={`https://t.me/${customer.username}`}
            target="_blank"
            rel="noreferrer"
            style={{ fontSize: 13 }}
          >
            Message @{customer.username} on Telegram
          </a>
        ) : (
          <span style={{ fontSize: 13, color: "var(--text-faint)" }}>
            No Telegram username — they can only be reached when they write again
          </span>
        )}
        <button className="control control-quiet" onClick={onClose} style={{ marginLeft: "auto" }}>
          Close
        </button>
      </div>

      {turns === "loading" ? (
        <p style={{ color: "var(--text-faint)", fontSize: 13 }}>Loading…</p>
      ) : turns.length === 0 ? (
        <p style={{ color: "var(--text-faint)", fontSize: 13 }}>Nothing recorded.</p>
      ) : (
        <ol style={{ listStyle: "none", padding: 0, margin: "12px 0 0" }}>
          {turns.map((t, i) => (
            <li key={i} style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 12, color: "var(--text-faint)" }}>{when(t.at)}</div>
              {t.question ? <div style={{ fontWeight: 500 }}>{t.question}</div> : null}
              {t.answer ? (
                <div style={{ color: "var(--text-muted)", marginTop: 2 }}>{t.answer}</div>
              ) : (
                /* The answer is null on most non-question lines, and the
                   outcome is the only thing that says what happened instead --
                   escalated, throttled, an order, a refusal. */
                <div style={{ color: "var(--text-faint)", fontSize: 13, marginTop: 2 }}>
                  {t.outcome ?? t.status ?? "no reply recorded"}
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export default function Conversations() {
  const [data, setData] = useState<Customers | "loading" | "error">("loading");
  const [open, setOpen] = useState<Customer | null>(null);

  useEffect(() => {
    getCustomers()
      .then(setData)
      .catch(() => setData("error"));
  }, []);

  if (data === "loading") return <p style={{ color: "var(--text-faint)" }}>Loading…</p>;
  if (data === "error") return <p>Could not load customers.</p>;

  return (
    <section>
      {/* The three numbers. These ARE the screen until the table has enough
          rows to be one. */}
      <div style={{ display: "flex", gap: 32, flexWrap: "wrap", marginBottom: 20 }}>
        <div>
          <div style={{ fontSize: 28, fontWeight: 600 }}>{data.people}</div>
          <div style={{ fontSize: 13, color: "var(--text-faint)" }}>
            {data.people === 1 ? "person" : "people"}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 28, fontWeight: 600 }}>{data.conversations}</div>
          <div style={{ fontSize: 13, color: "var(--text-faint)" }}>
            {data.conversations === 1 ? "conversation" : "conversations"}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 28, fontWeight: 600 }}>{when(data.last_at)}</div>
          <div style={{ fontSize: 13, color: "var(--text-faint)" }}>last message</div>
        </div>
      </div>

      {data.people === 0 ? (
        <p style={{ color: "var(--text-muted)" }}>
          Nobody has written to your bot yet. When they do, they appear here.
        </p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 14 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--text-faint)", fontSize: 12 }}>
                <th style={{ padding: "6px 12px 6px 0" }}>Who</th>
                <th style={{ padding: "6px 12px 6px 0" }}>Conversations</th>
                <th style={{ padding: "6px 12px 6px 0" }}>Last wrote</th>
                <th style={{ padding: "6px 12px 6px 0" }}>Last asked</th>
              </tr>
            </thead>
            <tbody>
              {data.customers.map((c) => (
                <tr
                  key={c.chat_id}
                  onClick={() => setOpen(c)}
                  style={{ borderTop: "1px solid var(--rule)", cursor: "pointer" }}
                >
                  <td style={{ padding: "10px 12px 10px 0" }}>
                    {label(c)}
                    {c.username && c.first_name ? (
                      <span style={{ color: "var(--text-faint)" }}> @{c.username}</span>
                    ) : null}
                  </td>
                  <td style={{ padding: "10px 12px 10px 0" }}>
                    {c.conversations}
                    <span style={{ color: "var(--text-faint)" }}> ({c.messages} messages)</span>
                  </td>
                  <td style={{ padding: "10px 12px 10px 0" }}>{when(c.last_at)}</td>
                  {/* Verbatim, because what they asked ABOUT is not derivable:
                      the log records no topic, and clustering these would cost
                      a model call per customer to fill one column. */}
                  <td
                    style={{
                      padding: "10px 12px 10px 0",
                      color: "var(--text-muted)",
                      maxWidth: 320,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {c.last_question ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {open ? <Exchange customer={open} onClose={() => setOpen(null)} /> : null}

      {/* SAID OUT LOUD RATHER THAN QUIETLY DROPPED. These lines were written
          before the bot stamped which business they belonged to, and there is
          no way to attribute them now. Guessing would show one business another
          business's customers, silently -- so they are counted here and shown
          nowhere. */}
      {data.unattributed > 0 ? (
        <p style={{ fontSize: 13, color: "var(--text-faint)", marginTop: 20 }}>
          {data.unattributed} earlier {data.unattributed === 1 ? "message is" : "messages are"} not
          shown — they were logged before messages recorded which business they belonged to.
        </p>
      ) : null}
    </section>
  );
}

import { useEffect, useState } from "react";
import { ApiError, getOwners, removeOwner, type Owner } from "./api";

/* Who can answer for this business, and how they stop being able to.
 *
 * THERE IS NO "ADD" BUTTON, and its absence is the design rather than a gap. A
 * bot cannot message anyone by @username and there is no lookup from a username
 * to a chat id -- the signed /start link is the only way Telegram ever reveals
 * one. So this screen hands out the link and the claim happens in Telegram, the
 * same flow the first owner already used.
 *
 * REMOVAL IS HERE AND NOT IN TELEGRAM. The thing being removed IS a Telegram
 * identity, so letting one Telegram identity revoke another would mean a
 * borrowed or stolen phone can lock out the real owner. This screen is behind
 * the email session, which is the address that created the business and
 * receives the sign-in link.
 *
 * REMOVING THE LAST OWNER IS ALLOWED, and the copy says what happens rather
 * than leaving someone to find out. It returns the bot to unclaimed, which is
 * recoverable from the link -- a business whose only owner lost their phone
 * must be able to hand ownership on. Refusing would create a stuck state rather
 * than prevent one. */

function when(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString();
}

export default function Owners() {
  const [owners, setOwners] = useState<Owner[] | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getOwners()
      .then(setOwners)
      .catch(() => setOwners(null));
  }, []);

  if (owners === null) return null;

  async function drop(id: number) {
    setBusy(true);
    setError(null);
    try {
      await removeOwner(id);
      setOwners(await getOwners());
      setConfirming(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not remove them.");
    } finally {
      setBusy(false);
    }
  }

  const full = owners.length >= 3;

  return (
    <div className="card" style={{ padding: 22, marginTop: 16 }}>
      <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>
        Who can answer
      </div>
      <p style={{ margin: "0 0 14px", fontSize: 14, color: "var(--text-faint)", lineHeight: 1.6 }}>
        Up to three people. Everyone here gets the questions your agent can't
        answer, payment confirmations, and reminders — and can reply to
        customers through the bot.
      </p>

      {owners.length === 0 ? (
        <p style={{ margin: "0 0 12px", fontSize: 14, color: "var(--text-muted)", lineHeight: 1.6 }}>
          Nobody has claimed this bot yet. Open it in Telegram and press Start
          using the link on the Telegram card above.
        </p>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, margin: "0 0 12px" }}>
          {owners.map((o) => (
            <li
              key={o.telegram_id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                flexWrap: "wrap",
                padding: "10px 0",
                borderTop: "1px solid var(--rule)",
              }}
            >
              <span style={{ fontWeight: 600 }}>
                {/* The id when there is no name -- ugly and unambiguous beats
                    tidy and useless when the point is saying which person. */}
                {o.name ?? `Telegram ${o.telegram_id}`}
              </span>
              {o.claimed_at ? (
                <span style={{ fontSize: 13, color: "var(--text-faint)" }}>
                  since {when(o.claimed_at)}
                </span>
              ) : null}

              {confirming === o.telegram_id ? (
                <span style={{ marginLeft: "auto", display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {/* THE LAST-OWNER SENTENCE, said before the tap rather than
                      after it. Someone removing themselves should know the bot
                      becomes unclaimed, not broken. */}
                  <span style={{ fontSize: 13, color: "var(--text-muted)" }}>
                    {owners.length === 1
                      ? "They're the only owner — the bot becomes unclaimed, and can be claimed again from the link."
                      : "They stop getting messages and can no longer reply."}
                  </span>
                  <button className="control control-quiet" onClick={() => setConfirming(null)}>
                    Cancel
                  </button>
                  <button
                    className="control control-primary"
                    onClick={() => drop(o.telegram_id)}
                    disabled={busy}
                  >
                    {busy ? "Removing…" : "Remove"}
                  </button>
                </span>
              ) : (
                <button
                  className="control control-quiet"
                  style={{ marginLeft: "auto" }}
                  onClick={() => setConfirming(o.telegram_id)}
                >
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <p style={{ margin: 0, fontSize: 13, color: "var(--text-faint)", lineHeight: 1.6 }}>
        {full
          ? "That's three — remove someone before adding another."
          : `${3 - owners.length} more can join. They claim the bot themselves by opening it in Telegram and pressing Start — there's no way to add someone by username, because Telegram doesn't let a bot message a person who hasn't written to it first.`}
      </p>

      {error ? (
        <p style={{ margin: "8px 0 0", fontSize: 13, color: "var(--danger, #a4362f)" }}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

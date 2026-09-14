import { useEffect, useMemo, useState } from "react";
import {
  addAlias,
  ApiError,
  deleteAlias,
  deleteFact,
  editConfirmedFact,
  getAliases,
  getKnowledge,
  markExpectedMultiple,
  setExpiry,
  spendingAllowed,
  type Alias,
  type KnowledgeFact,
  type KnowledgeGroup,
} from "./api";

/* Everything the agent knows, and the way to change it.
 *
 * WHY THIS SCREEN EXISTS. Review shows `where not confirmed` -- the proposals
 * queue. The moment a fact was confirmed it left that queue and became
 * invisible to the whole product. An owner whose price changed or whose doctor
 * left had no route to fix it except typing a new fact in Telegram and living
 * with the old one contradicting it.
 *
 * CONTRADICTIONS COME FIRST. The server sorts subjects with a disagreement to
 * the top, because an owner opening this screen is usually here because
 * something is wrong, and making them scroll past 25 correct subjects to find
 * it would defeat the point.
 *
 * SEARCH IS IN THE BROWSER, not on the server. 140 facts across 25 subjects is
 * one modest response; filtering what is already loaded is instant and works
 * while typing. app/knowledge.py records the size at which that stops being
 * true, as a number, so whoever hits it knows it was a decision. */

/* MASKED HERE, AND DELIBERATELY NOT ON THE PAYMENT DETAILS SCREEN.
 *
 * That screen removed masking on purpose: the card number is broadcast to every
 * customer who asks to pay, so hiding it from the owner protects nothing, and a
 * reveal tap costs something every time on a screen you visit precisely to
 * check the number against your bank app.
 *
 * Both of those are still true. The difference is what this screen is for. You
 * come here scanning for something else -- one of a hundred and sixty-seven
 * facts -- and the card number appearing in that list is incidental. It ends up
 * in the DOM, in a screenshot of a support conversation, on a shared screen,
 * every time an owner looks for their opening hours.
 *
 * So: masked with a reveal, one tap, only on the row that is actually a card
 * number. Matched on the value's shape rather than the attribute name, because
 * the attribute is owner-written text -- "Karta raqami", "Card", "карта" -- and
 * a list of names to match would miss the one somebody typed differently. */
/* FOURTEEN DIGITS, not twelve, and the number matters. An Uzbek phone number
 * with its country code is exactly twelve -- +998901234567 -- so a twelve-digit
 * floor masked every clinic phone number on the screen, which are facts
 * customers ask for and the owner needs to read at a glance. Cards here are
 * sixteen (UZCARD, HUMO, Visa, Mastercard) or fifteen (Amex). Fourteen sits
 * cleanly between the two.
 *
 * The trade: a thirteen-digit Visa, which exists and is rare and nearly extinct
 * in Uzbekistan, would not be masked. Better than masking every phone number. */
const CARD_LIKE = /(?:\d[ -]?){14,19}/;

function maskCard(value: string): string {
  return value.replace(CARD_LIKE, (run) => {
    const digits = run.replace(/\D/g, "");
    if (digits.length < 14) return run;
    return `${digits.slice(0, 4)} •••• •••• ${digits.slice(-4)}`;
  });
}

const BLOCKED =
  "Your account is not approved yet, so this is switched off. " +
  "We will email you when it is ready.";

export default function Knowledge() {
  const [groups, setGroups] = useState<KnowledgeGroup[] | null>(null);
  const [aliases, setAliases] = useState<Alias[]>([]);
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState({ attribute: "", value: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [aliasFor, setAliasFor] = useState<string | null>(null);
  const [aliasText, setAliasText] = useState("");
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const [dating, setDating] = useState<string | null>(null);
  const [dateText, setDateText] = useState("");

  async function load() {
    try {
      const [g, a] = await Promise.all([getKnowledge(), getAliases()]);
      setGroups(g);
      setAliases(a);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't load.");
      setGroups([]);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  /* Matches subject, attribute, value and alias. Not a ranked search -- a
     substring match over a few hundred short strings, which is what someone
     scanning for "kardiolog" or "250 000" actually wants. */
  const shown = useMemo(() => {
    if (!groups) return [];
    const needle = filter.trim().toLowerCase();
    if (!needle) return groups;
    const aliasHit = new Set(
      aliases
        .filter((a) => a.alias.toLowerCase().includes(needle))
        .map((a) => a.subject_key),
    );
    return groups
      .map((g) => {
        if (
          g.subject.toLowerCase().includes(needle) ||
          aliasHit.has(g.subject_key)
        ) {
          return g;
        }
        const facts = g.facts.filter(
          (f) =>
            f.attribute.toLowerCase().includes(needle) ||
            f.value.toLowerCase().includes(needle),
        );
        return facts.length ? { ...g, facts } : null;
      })
      .filter(Boolean) as KnowledgeGroup[];
  }, [groups, filter, aliases]);

  const total = groups?.reduce((n, g) => n + g.facts.length, 0) ?? 0;
  const disputed = groups?.reduce((n, g) => n + g.disputes, 0) ?? 0;

  async function save(fact: KnowledgeFact) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const result = await editConfirmedFact(fact.id, {
        attribute: draft.attribute.trim(),
        value: draft.value.trim(),
      });
      setEditing(null);
      /* Says whether it re-embedded, because that is the difference between
         "the text changed" and "the agent will find it by the new wording".
         The owner has no other way to know the vector moved. */
      setNote(
        result.reembedded
          ? "Saved, and the agent now matches on the new wording."
          : "Saved. It will be embedded when you confirm it.",
      );
      await load();
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.reason === "not_approved"
            ? BLOCKED
            : e.message
          : "Couldn't save.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setBusy(true);
    setError(null);
    try {
      await deleteFact(id);
      setConfirmDelete(null);
      setNote("Deleted.");
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't delete.");
    } finally {
      setBusy(false);
    }
  }

  async function applyExpiry(id: string, value: string | null) {
    setBusy(true);
    setError(null);
    try {
      await setExpiry(id, value);
      setDating(null);
      setDateText("");
      setNote(
        value
          ? "Set. The agent stops saying it after that date."
          : "Expiry removed — the agent can use it again.",
      );
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't set that.");
    } finally {
      setBusy(false);
    }
  }

  async function markIntentional(subjectKey: string, attributeKey: string) {
    setBusy(true);
    setError(null);
    try {
      await markExpectedMultiple(subjectKey, attributeKey, true);
      setNote("Noted — we won't flag those again.");
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't save that.");
    } finally {
      setBusy(false);
    }
  }

  async function createAlias(subjectKey: string) {
    if (!aliasText.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await addAlias(subjectKey, aliasText.trim());
      setAliasText("");
      setAliasFor(null);
      setNote(result.already ? "That one already existed." : "Alias added.");
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't add that.");
    } finally {
      setBusy(false);
    }
  }

  /* EVERY HOOK ABOVE THIS LINE, EVERY RETURN BELOW IT. See App.tsx. */
  if (groups === null) return null;

  const shell = { maxWidth: 860, margin: "0 auto", padding: "24px 20px 64px" };

  if (groups.length === 0) {
    return (
      <div style={shell}>
        <div className="card" style={{ padding: 28, textAlign: "center" }}>
          <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 6 }}>
            The agent doesn't know anything yet.
          </div>
          <p
            style={{
              margin: "0 0 18px",
              fontSize: 14,
              lineHeight: 1.6,
              color: "var(--text-faint)",
            }}
          >
            Add a document or type a fact, confirm it in Review, and it appears
            here.
          </p>
          <a className="control control-primary" href="#/">
            Add knowledge
          </a>
        </div>
      </div>
    );
  }

  return (
    <div style={shell}>
      {/* THE WAY BACK. The counter band routes forward and cannot route back,
          and the browser's back button is no use to someone who arrived by
          bookmark -- #/review has been a real URL since before the merge and
          stays one, which is the reason the routes were not renamed. */}
      <a
        href="#/"
        style={{
          display: "inline-flex",
          alignItems: "center",
          minHeight: 44,
          fontSize: 14,
          fontWeight: 600,
          color: "var(--text-muted)",
          textDecoration: "none",
        }}
      >
        ← Knowledge base
      </a>
      <h1 style={{ fontSize: 20, fontWeight: 800, margin: "0 0 4px" }}>
        Knowledge
      </h1>
      <p style={{ margin: "0 0 16px", fontSize: 14, color: "var(--text-faint)" }}>
        {total} {total === 1 ? "fact" : "facts"} across {groups.length}{" "}
        {groups.length === 1 ? "subject" : "subjects"}
        {disputed > 0 && (
          <>
            {" · "}
            {/* "TWO VALUES RECORDED", NOT "CONFLICT".
                The check is "same subject and attribute, different values",
                which catches a stale price and a legitimate early-bird price
                identically -- and it cannot tell them apart. On the seeded
                clinic it flags both a genuinely stale opening-hours pair AND
                "1 200 000 so'm" beside "950 000 so'm (10 days before the group
                starts)", where both are true under different conditions.
                Wording it as a conflict would teach an owner to delete the
                second one. */}
            <span style={{ color: "var(--text-secondary)", fontWeight: 600 }}>
              {disputed} have more than one value
            </span>
          </>
        )}
      </p>

      <input
        className="field"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Search subjects, facts, aliases…"
        style={{ width: "100%", marginBottom: 18 }}
      />

      {note && (
        <p style={{ margin: "0 0 12px", fontSize: 13, color: "var(--text-secondary)" }}>
          {note}
        </p>
      )}
      {error && (
        <p style={{ margin: "0 0 12px", fontSize: 13, color: "var(--danger, #a4362f)" }}>
          {error}
        </p>
      )}

      {shown.length === 0 && (
        <p style={{ fontSize: 14, color: "var(--text-faint)" }}>
          Nothing matches “{filter}”.
        </p>
      )}

      {shown.map((group) => {
        const groupAliases = aliases.filter(
          (a) => a.subject_key === group.subject_key,
        );
        return (
          <div
            key={group.subject_key}
            className="card"
            style={{ padding: 18, marginBottom: 14 }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                gap: 10,
                marginBottom: 10,
                flexWrap: "wrap",
              }}
            >
              <span style={{ fontSize: 16, fontWeight: 700 }}>
                {group.subject}
              </span>
              {group.disputes > 0 && (
                <span
                  className="tag"
                  title={
                    "Two or more facts answer the same thing differently. " +
                    "That may be out of date, or deliberate — a price that " +
                    "depends on when you book, for instance."
                  }
                >
                  {group.disputes} values recorded
                </span>
              )}
              {group.disputes > 0 && (() => {
                /* One button per disputed ATTRIBUTE, because the dismissal is
                   about a subject+attribute pair rather than the subject. A
                   salon whose prices are conditional and whose hours are stale
                   should be able to say so about one and not the other. */
                const keys = [
                  ...new Set(
                    group.facts.filter((f) => f.disputed).map((f) => f.attribute_key),
                  ),
                ];
                return keys.map((key) => (
                  <button
                    key={key}
                    className="control control-quiet"
                    style={{ fontSize: 12 }}
                    disabled={busy}
                    title="Stop flagging this one — both values are meant to be here"
                    onClick={() => void markIntentional(group.subject_key, key)}
                  >
                    “{key}” is meant to have both
                  </button>
                ));
              })()}
            </div>

            {group.facts.map((fact) => (
              <div
                key={fact.id}
                style={{
                  padding: "8px 0",
                  borderTop: "1px solid var(--rule)",
                  /* The disagreement is marked on the row, not only counted on
                     the subject -- the count tells you there is a problem, the
                     stripe tells you which two rows it is between. */
                  /* A marker, not an alarm. Neutral rather than red, because
                     the screen does not know which of the two is wrong -- or
                     whether either is. */
                  borderLeft: fact.disputed
                    ? "3px solid var(--accent-pressed, #2f6f4f)"
                    : undefined,
                  paddingLeft: fact.disputed ? 10 : undefined,
                  marginLeft: fact.disputed ? -13 : undefined,
                }}
              >
                {editing === fact.id ? (
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <input
                      className="field"
                      value={draft.attribute}
                      onChange={(e) =>
                        setDraft({ ...draft, attribute: e.target.value })
                      }
                      style={{ flex: "1 1 180px" }}
                    />
                    <input
                      className="field"
                      value={draft.value}
                      onChange={(e) =>
                        setDraft({ ...draft, value: e.target.value })
                      }
                      style={{ flex: "2 1 260px" }}
                    />
                    <button
                      className="control control-primary"
                      disabled={busy || !spendingAllowed}
                      title={spendingAllowed ? undefined : BLOCKED}
                      onClick={() => void save(fact)}
                    >
                      {busy ? "Saving…" : "Save"}
                    </button>
                    <button
                      className="control control-quiet"
                      onClick={() => setEditing(null)}
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <div
                    style={{
                      display: "flex",
                      gap: 10,
                      alignItems: "baseline",
                      flexWrap: "wrap",
                    }}
                  >
                    <span style={{ fontSize: 14, flex: "1 1 auto" }}>
                      <strong style={{ fontWeight: 600 }}>
                        {fact.attribute}
                      </strong>{" "}
                      ·{" "}
                      {CARD_LIKE.test(fact.value) && !revealed[fact.id] ? (
                        <>
                          {maskCard(fact.value)}{" "}
                          <button
                            className="control control-quiet"
                            style={{ fontSize: 12, padding: "0 6px" }}
                            onClick={() =>
                              setRevealed((r) => ({ ...r, [fact.id]: true }))
                            }
                          >
                            show
                          </button>
                        </>
                      ) : (
                        fact.value
                      )}
                      {!fact.confirmed && (
                        <span className="tag" style={{ marginLeft: 8 }}>
                          awaiting review
                        </span>
                      )}
                    </span>
                    {/* EXPIRY, shown on the row it belongs to. An expired
                        fact is still listed -- that is the whole reason it is
                        not deleted -- so the row has to say plainly that the
                        agent has stopped using it, or the owner sees a fact
                        that looks live and wonders why customers are not
                        being told it. */}
                    {fact.expires_at && (
                      <span
                        className={fact.expired ? "tag tag-caution" : "tag"}
                        style={{ fontSize: 12 }}
                      >
                        {fact.expired ? "expired " : "until "}
                        {new Date(fact.expires_at).toLocaleDateString()}
                      </span>
                    )}
                    <span
                      style={{ fontSize: 13, color: "var(--text-faint)" }}
                      title={fact.source?.excerpt ?? undefined}
                    >
                      {/* The same vocabulary as Review and the test console. A
                          typed fact has no source by design; "you typed this"
                          is better provenance than a filename would be. */}
                      {fact.origin === "typed"
                        ? "you typed this"
                        : fact.source?.label ||
                          fact.source?.filename ||
                          "a file you uploaded"}
                    </span>
                    <button
                      className="control control-quiet"
                      style={{ fontSize: 13 }}
                      onClick={() => {
                        setEditing(fact.id);
                        setDraft({
                          attribute: fact.attribute,
                          value: fact.value,
                        });
                        setNote(null);
                      }}
                    >
                      Edit
                    </button>
                    {dating === fact.id ? (
                      <>
                        <input
                          className="field"
                          type="date"
                          autoFocus
                          value={dateText}
                          onChange={(e) => setDateText(e.target.value)}
                          style={{ fontSize: 13, width: 150 }}
                        />
                        <button
                          className="control control-secondary"
                          style={{ fontSize: 13 }}
                          disabled={busy || !dateText}
                          onClick={() => void applyExpiry(fact.id, dateText)}
                        >
                          Set
                        </button>
                        {fact.expires_at && (
                          <button
                            className="control control-quiet"
                            style={{ fontSize: 13 }}
                            disabled={busy}
                            /* Clearing is how an expired fact comes back. The
                               row was never deleted, so this is the whole of
                               "reactivate". */
                            onClick={() => void applyExpiry(fact.id, null)}
                          >
                            {fact.expired ? "Reactivate" : "No end date"}
                          </button>
                        )}
                        <button
                          className="control control-quiet"
                          style={{ fontSize: 13 }}
                          onClick={() => setDating(null)}
                        >
                          Cancel
                        </button>
                      </>
                    ) : (
                      <button
                        className="control control-quiet"
                        style={{ fontSize: 13 }}
                        onClick={() => {
                          setDating(fact.id);
                          setDateText(fact.expires_at?.slice(0, 10) ?? "");
                          setNote(null);
                        }}
                      >
                        {fact.expires_at ? "Change end date" : "Set end date"}
                      </button>
                    )}
                    {confirmDelete === fact.id ? (
                      <>
                        <button
                          className="control control-quiet"
                          style={{ fontSize: 13, color: "var(--danger, #a4362f)" }}
                          disabled={busy}
                          onClick={() => void remove(fact.id)}
                        >
                          Really delete
                        </button>
                        <button
                          className="control control-quiet"
                          style={{ fontSize: 13 }}
                          onClick={() => setConfirmDelete(null)}
                        >
                          Keep
                        </button>
                      </>
                    ) : (
                      <button
                        className="control control-quiet"
                        style={{ fontSize: 13 }}
                        /* Two taps, because there is no undo. review.reject()
                           refuses confirmed facts precisely because removing
                           something the business said is true is a different
                           act from rejecting a misread. */
                        onClick={() => setConfirmDelete(fact.id)}
                      >
                        Delete
                      </button>
                    )}
                  </div>
                )}
              </div>
            ))}

            {/* ALIASES, which nothing in the product could create until now.
                They are load-bearing for cross-script matching: "кардиолог"
                and "kardiolog" reach the same subject only because one says so.
                Shown per subject because that is the only place they mean
                anything. */}
            <div
              style={{
                borderTop: "1px solid var(--rule)",
                marginTop: 6,
                paddingTop: 8,
                fontSize: 13,
                color: "var(--text-faint)",
              }}
            >
              {groupAliases.length > 0 && (
                <span>
                  Also called:{" "}
                  {groupAliases.map((a) => (
                    <span key={a.id} style={{ marginRight: 8 }}>
                      {a.alias}{" "}
                      <button
                        className="control control-quiet"
                        style={{ fontSize: 12, padding: "0 4px" }}
                        onClick={() => void deleteAlias(a.id).then(load)}
                        title="Remove this alias"
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </span>
              )}
              {aliasFor === group.subject_key ? (
                <span style={{ display: "inline-flex", gap: 6, marginTop: 6 }}>
                  <input
                    className="field"
                    value={aliasText}
                    autoFocus
                    onChange={(e) => setAliasText(e.target.value)}
                    placeholder="another word customers use"
                    style={{ fontSize: 13 }}
                  />
                  <button
                    className="control control-secondary"
                    style={{ fontSize: 13 }}
                    disabled={busy || !aliasText.trim()}
                    onClick={() => void createAlias(group.subject_key)}
                  >
                    Add
                  </button>
                  <button
                    className="control control-quiet"
                    style={{ fontSize: 13 }}
                    onClick={() => {
                      setAliasFor(null);
                      setAliasText("");
                    }}
                  >
                    Cancel
                  </button>
                </span>
              ) : (
                <button
                  className="control control-quiet"
                  style={{ fontSize: 13 }}
                  onClick={() => {
                    setAliasFor(group.subject_key);
                    setAliasText("");
                  }}
                >
                  + another name
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

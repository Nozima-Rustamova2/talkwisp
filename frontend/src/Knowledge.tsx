import { useEffect, useMemo, useState } from "react";
import {
  addAlias,
  ApiError,
  deleteAlias,
  deleteFact,
  editConfirmedFact,
  getAliases,
  getKnowledge,
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
      <h1 style={{ fontSize: 20, fontWeight: 800, margin: "0 0 4px" }}>
        Knowledge
      </h1>
      <p style={{ margin: "0 0 16px", fontSize: 14, color: "var(--text-faint)" }}>
        {total} {total === 1 ? "fact" : "facts"} across {groups.length}{" "}
        {groups.length === 1 ? "subject" : "subjects"}
        {disputed > 0 && (
          <>
            {" · "}
            <span style={{ color: "var(--danger, #a4362f)", fontWeight: 600 }}>
              {disputed} contradict each other
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
                <span className="tag tag-caution">
                  {group.disputes} disagree
                </span>
              )}
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
                  borderLeft: fact.disputed
                    ? "3px solid var(--danger, #a4362f)"
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
                      · {fact.value}
                      {!fact.confirmed && (
                        <span className="tag" style={{ marginLeft: 8 }}>
                          awaiting review
                        </span>
                      )}
                    </span>
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

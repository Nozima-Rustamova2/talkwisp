import { useEffect, useState } from "react";
import { ApiError, getStyle, saveStyle, type Style } from "./api";

/* What the agent is called and how it sounds.
 *
 * THERE IS NO PERSONA TEXT BOX HERE, and its absence is the design rather than
 * an omission. A free-text field describing the assistant would sit in the same
 * prompt as the rules that make the product's claim true, and an owner writing
 * something entirely reasonable would undo one without knowing:
 *
 *     "always try to be helpful"       weakens the refusal
 *     "you know everything about us"   invites invention
 *     "answer in Russian"              overrides matching the customer
 *     "never say you don't know"       removes the core claim
 *
 * None of those is malicious, and the failure is invisible -- a slightly more
 * confident answer, not an error. Fixed options are also the only version we
 * can test: the harness runs every combination and cannot run free text.
 *
 * NO "DETAILED" LENGTH either. Rule 7 asks for one or two sentences and is
 * load-bearing; the doctors-ru case showed the model summarising twelve doctors
 * to five as context grew. Concise and normal is the whole range, so the
 * direction that caused that is not reachable from this screen.
 *
 * LEAVING EVERYTHING ALONE IS A REAL CHOICE. A business that sets nothing gets
 * a prompt byte-identical to the one before this screen existed, which is why
 * "Normal" and "No emoji" are the defaults and add nothing rather than adding a
 * neutral-sounding sentence. */

const LANGUAGES: { key: keyof Style; label: string; sample: string }[] = [
  {
    key: "greeting_uz_latn",
    label: "Uzbek (Latin)",
    sample: "Assalomu alaykum! … haqidagi savolingizni yozing.",
  },
  {
    key: "greeting_uz_cyrl",
    label: "Uzbek (Cyrillic)",
    sample: "Ассалому алайкум! … ҳақидаги саволингизни ёзинг.",
  },
  { key: "greeting_ru", label: "Russian", sample: "Здравствуйте! Задайте свой вопрос о …" },
];

function Choice({
  label,
  hint,
  options,
  value,
  onPick,
}: {
  label: string;
  hint: string;
  options: { value: string; label: string }[];
  value: string;
  onPick: (v: string) => void;
}) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 2 }}>{label}</div>
      <p style={{ margin: "0 0 8px", fontSize: 13, color: "var(--text-faint)" }}>{hint}</p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {options.map((o) => {
          const on = value === o.value;
          return (
            <button
              key={o.value}
              onClick={() => onPick(o.value)}
              className="control"
              style={{
                background: on ? "var(--accent-tint)" : "transparent",
                fontWeight: on ? 700 : 600,
                color: on ? "var(--accent-pressed)" : "var(--text-secondary)",
              }}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function Personality() {
  const [style, setStyle] = useState<Style | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getStyle()
      .then((s) => {
        setStyle(s);
        setDraft({
          agent_name: s.agent_name ?? "",
          tone_register: s.tone_register ?? "formal",
          tone_length: s.tone_length ?? "normal",
          tone_emoji: s.tone_emoji ?? "off",
          greeting_uz_latn: s.greeting_uz_latn ?? "",
          greeting_uz_cyrl: s.greeting_uz_cyrl ?? "",
          greeting_ru: s.greeting_ru ?? "",
        });
      })
      .catch(() => setStyle(null));
  }, []);

  if (!style) return null;

  const set = (k: string, v: string) => {
    setDraft((d) => ({ ...d, [k]: v }));
    setSaved(false);
  };

  async function save() {
    setBusy(true);
    setError(null);
    try {
      setStyle(await saveStyle(draft));
      setSaved(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ padding: 22, marginTop: 16 }}>
      <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>
        Agent personality
      </div>
      <p style={{ margin: "0 0 18px", fontSize: 14, color: "var(--text-faint)", lineHeight: 1.6 }}>
        Optional. Everything here has a sensible default — leaving it alone
        changes nothing about how your agent answers.
      </p>

      <div style={{ marginBottom: 18 }}>
        <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 2 }}>
          Agent name
        </label>
        <p style={{ margin: "0 0 8px", fontSize: 13, color: "var(--text-faint)" }}>
          What it calls itself. Leave empty and it just says it's your assistant.
        </p>
        <input
          className="field"
          value={draft.agent_name ?? ""}
          onChange={(e) => set("agent_name", e.target.value)}
          placeholder="Sam, Nigora, Yordamchi…"
          style={{ maxWidth: 280 }}
        />
      </div>

      <Choice
        label="How it addresses people"
        hint="Uzbek siz/sen, Russian вы/ты."
        options={[
          { value: "formal", label: "Formal (siz / вы)" },
          { value: "informal", label: "Informal (sen / ты)" },
        ]}
        value={draft.tone_register ?? "formal"}
        onPick={(v) => set("tone_register", v)}
      />

      <Choice
        label="Answer length"
        hint="Answers are short either way — this only makes them shorter."
        options={[
          { value: "normal", label: "Normal" },
          { value: "concise", label: "Concise" },
        ]}
        value={draft.tone_length ?? "normal"}
        onPick={(v) => set("tone_length", v)}
      />

      <Choice
        label="Emoji"
        hint="Never next to a price, a time or a number."
        options={[
          { value: "off", label: "No emoji" },
          { value: "light", label: "Occasional" },
        ]}
        value={draft.tone_emoji ?? "off"}
        onPick={(v) => set("tone_emoji", v)}
      />

      <div style={{ fontSize: 13, fontWeight: 600, margin: "22px 0 2px" }}>
        First message
      </div>
      <p style={{ margin: "0 0 10px", fontSize: 13, color: "var(--text-faint)", lineHeight: 1.6 }}>
        What someone sees when they open the chat. Leave a language empty and we
        write it for you, using your business name.
      </p>
      {LANGUAGES.map((l) => (
        <div key={l.key} style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 12, color: "var(--text-faint)", marginBottom: 3 }}>
            {l.label}
          </div>
          <input
            className="field"
            value={draft[l.key] ?? ""}
            onChange={(e) => set(l.key, e.target.value)}
            placeholder={l.sample}
          />
        </div>
      ))}

      <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 16, flexWrap: "wrap" }}>
        <button className="control control-primary" onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
        {saved ? (
          /* IMMEDIATELY, unlike the rename. style.block() reads these columns
             on every answer rather than caching them at startup, so the next
             customer message already uses them. The rename says "within a
             minute" because BUSINESS_NAME genuinely is cached in the process.
             Two screens, two true sentences -- copying the other one here
             would have been wrong in the reassuring direction. */
          <span style={{ fontSize: 13, color: "var(--text-faint)" }}>
            Saved. The next message your agent answers uses it.
          </span>
        ) : null}
        {error ? (
          <span style={{ fontSize: 13, color: "var(--danger, #a4362f)" }}>{error}</span>
        ) : null}
      </div>
    </div>
  );
}

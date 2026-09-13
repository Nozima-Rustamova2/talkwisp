import { useEffect, useState } from "react";
import {
  getPayment,
  setPaymentDetail,
  type Payment as PaymentState,
  type PaymentLanguage,
} from "./api";

/* Payment details: its own section, and optional on purpose.
 *
 * WHY IT IS A SECTION AND NOT A SETTING. Until this screen there was no route
 * for an owner to set payment details at all -- the reserved subject was
 * refused on /fact, on Add knowledge, and everywhere else an owner could reach,
 * and the only writer was seed.py. A business connected a bot, a customer
 * tapped buy, an order was created, and the flow discovered at the last step
 * that it had no card number. The customer was told "contact us" and the owner
 * was told nothing.
 *
 * IT MUST READ AS OPTIONAL. Plenty of businesses never sell in chat and an
 * agent with no payment details answers every question perfectly well. So this
 * screen states what filling it in ENABLES, and there is no red incomplete
 * badge, no progress bar, and no implication that setup is unfinished. The one
 * thing it does say plainly is the state that actually costs money: prices in
 * the knowledge base and no way to take payment for them.
 *
 * NOT MASKED, unlike the knowledge base. The card number is broadcast to every
 * customer who asks to pay, so hiding it from the owner protects nothing -- and
 * a reveal tap costs something every time on the one screen you open precisely
 * to check the number against your bank app. The trade is different here than
 * on Knowledge, where the number appears incidentally while you scan for
 * something else.
 *
 * PER LANGUAGE, NOT DONE/NOT-DONE. The card is shared, but the instruction and
 * the exact-amount line are the owner's own words in each language, so a
 * business can be complete for Uzbek and dead-end every Russian speaker. A
 * single tick would say "set up" and be wrong for a third of the country. */

function Field({
  label,
  hint,
  attribute,
  value,
  onSaved,
}: {
  label: string;
  hint?: string;
  attribute: string;
  value: string | null;
  onSaved: (next: PaymentState) => void;
}) {
  const [draft, setDraft] = useState(value ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /* The server is the authority on what is stored. Without this, saving one
     field re-renders the others from a response they were not part of and a
     half-typed draft elsewhere jumps back. */
  useEffect(() => {
    setDraft(value ?? "");
  }, [value]);

  const dirty = draft.trim() !== (value ?? "");

  async function save() {
    setBusy(true);
    setError(null);
    try {
      onSaved(await setPaymentDetail(attribute, draft.trim()));
    } catch {
      setError("Could not save. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ marginBottom: 18 }}>
      <label style={{ display: "block", fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
        {label}
      </label>
      {hint ? (
        <p style={{ margin: "0 0 6px", fontSize: 13, color: "var(--text-faint)" }}>{hint}</p>
      ) : null}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {/* defaultValue is wrong here: this IS a controlled input with an
            onChange, which is the shape that works. The rule in the design
            notes is about `value` with NO onChange. */}
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          style={{ flex: "1 1 260px", minWidth: 0 }}
        />
        <button
          className="control control-primary"
          onClick={save}
          disabled={busy || !dirty}
        >
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
      {error ? (
        <p style={{ margin: "6px 0 0", fontSize: 13, color: "var(--danger, #a4362f)" }}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

function Language({
  lang,
  onSaved,
}: {
  lang: PaymentLanguage;
  onSaved: (next: PaymentState) => void;
}) {
  return (
    <div
      style={{
        border: "1px solid var(--rule)",
        borderRadius: 8,
        padding: 16,
        marginBottom: 14,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 10 }}>
        <strong>{lang.label}</strong>
        <span style={{ fontSize: 13, color: lang.ready ? "var(--text-muted)" : "var(--text-faint)" }}>
          {lang.ready
            ? "customers writing in this language can pay"
            : "not set up — customers writing in this language get an answer, not a payment option"}
        </span>
      </div>
      <Field
        label="How to pay"
        hint="Your own words. Shown above the card number."
        attribute={lang.instruction_attribute}
        value={lang.instruction}
        onSaved={onSaved}
      />
      <Field
        label="Send the exact amount"
        hint="Each order gets a unique amount. This line asks them not to round it."
        attribute={lang.exact_attribute}
        value={lang.exact}
        onSaved={onSaved}
      />
    </div>
  );
}

export default function Payment() {
  const [state, setState] = useState<PaymentState | "loading" | "error">("loading");

  useEffect(() => {
    getPayment()
      .then(setState)
      .catch(() => setState("error"));
  }, []);

  if (state === "loading") return <p style={{ color: "var(--text-faint)" }}>Loading…</p>;
  if (state === "error") return <p>Could not load payment details.</p>;

  const nothingReady = state.ready.length === 0;

  return (
    <section style={{ maxWidth: 720 }}>
      <h2 style={{ margin: "0 0 6px", fontSize: 20 }}>Payment</h2>
      {/* WHAT IT ENABLES, not what is missing. */}
      <p style={{ margin: "0 0 20px", color: "var(--text-muted)", lineHeight: 1.6 }}>
        Optional. Set this up if you want customers to pay through the agent —
        it shows them your card number and a unique amount, and you confirm the
        payment yourself in Telegram. Leave it empty and the agent answers
        questions as usual, including questions about price.
      </p>

      {/* THE ONE STATE WORTH SAYING PLAINLY. Not "payment is unset" -- that is
          fine and common. This is: the buy flow can fire for this business and
          cannot complete, which costs real sales silently. */}
      {state.has_prices && nothingReady ? (
        <div
          style={{
            border: "1px solid var(--rule)",
            background: "var(--accent-tint, #eef4f0)",
            borderRadius: 8,
            padding: "12px 14px",
            marginBottom: 20,
            fontSize: 14,
            lineHeight: 1.6,
          }}
        >
          You have prices in your knowledge base, so customers do ask to buy.
          Until there's a card number here, the agent answers their price
          question and doesn't offer to take payment.
        </div>
      ) : null}

      <Field
        label="Card number"
        hint="Shown to customers who choose to pay. Not hidden here — it's the number you check against your bank app."
        attribute={state.card_attribute}
        value={state.card}
        onSaved={setState}
      />
      {state.optional.map((o) => (
        <Field
          key={o.attribute}
          label={o.attribute === "karta egasi" ? "Card holder (optional)" : "Bank (optional)"}
          attribute={o.attribute}
          value={o.value}
          onSaved={setState}
        />
      ))}

      <h3 style={{ margin: "28px 0 4px", fontSize: 16 }}>Per language</h3>
      <p style={{ margin: "0 0 14px", fontSize: 14, color: "var(--text-muted)", lineHeight: 1.6 }}>
        The card number is the same for everyone, but these two lines are your
        own words and are needed in each language you want to take payment in.
        A language without them still gets answers — just not a payment option.
      </p>
      {state.languages.map((lang) => (
        <Language key={lang.key} lang={lang} onSaved={setState} />
      ))}
    </section>
  );
}

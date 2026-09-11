/* Thin wrappers over app/main.py. No client, no cache, no retry.
 *
 * Note the shape these are wrapping: the POST endpoints take QUERY PARAMETERS,
 * not JSON bodies -- `add_fact(line: str)` in FastAPI with a bare `str` is a
 * query param. So a pasted document travels in the URL, and a URL has a length
 * ceiling the server enforces long before the database would. PASTE_LIMIT below
 * is measured, not guessed; see the comment on it.
 */

export type Source = {
  id: string;
  kind: "file" | "paste";
  label: string | null;
  filename: string | null;
  media_type: string | null;
  content: string | null;
  status: "pending" | "extracted" | "failed";
  error: string | null;
  size: number | null;
  created_at: string | null;
  extracted_at: string | null;
};

export type ParsedFact = {
  subject: string;
  attribute: string;
  value: string;
};

/* The key is `conflicts`, PLURAL, and it is a list. It was written here as
 * `conflict?: unknown` first, which type-checked, built clean, and made the
 * conflict warning permanently invisible -- the code asked about a key the
 * server never sends. Verified against a live response, not against the
 * docstring:
 *   {"parsed": {...}, "conflicts": [{"subject": "Avisena Med",
 *    "attribute": "qabulxona telefoni", "value": "+998901234567"}], ...} */
export type FactResult = {
  error: string | null;
  parsed: ParsedFact | null;
  conflicts: ParsedFact[];
  candidates: unknown[];
  written: boolean;
  id?: string;
};

/* Every type below is written from a PRINTED response, and the shape that
 * surprised me is quoted where it matters. The one bug this file has already
 * produced came from writing a plausible key name instead. */

export type Proposal = {
  id: string;
  subject: string;
  attribute: string;
  value: string;
  confidence: number | null;
  source: {
    id: string;
    label: string | null;
    filename: string | null;
    kind: string;
    excerpt: string | null;
  } | null;
  created_at: string;
};

export type ConflictValue = {
  id: string;
  subject: string;
  attribute: string;
  value: string;
  confirmed: boolean;
  confidence: number | null;
  typed: boolean;
  source_id: string | null;
};

export type Conflict = {
  subject_key: string;
  attribute_key: string;
  values: ConflictValue[];
};

/* The key is `now_conflicts_with`, and it is what confirming this fact would
 * put it into disagreement with -- not a refusal. The backend surfaces
 * conflicts and never resolves them; two opening-hours values may both be true.
 * Observed:
 *   {"id": "...", "confirmed": true, "now_conflicts_with": [
 *     {"id": "...", "value": "Dushanba-Juma 08:00 - 20:00, ...",
 *      "confirmed": true}]} */
export type Confirmed = {
  id: string;
  subject: string;
  attribute: string;
  value: string;
  confirmed: boolean;
  now_conflicts_with: ConflictValue[];
};

export type Stats = {
  facts: number;
  facts_awaiting_review: number;
  sources: number;
  last_added: string | null;
};

export class ApiError extends Error {
  status: number;
  /* The server's own word for what went wrong, when it has one. Only
   * "not_approved" exists today. It is here because a 403 is otherwise
   * indistinguishable from any other refusal, and the one thing every screen
   * must not do is render "not approved yet" as a generic failure -- that is
   * software that looks broken when it is working exactly as designed. */
  reason?: string;
  constructor(status: number, message: string, reason?: string) {
    super(message);
    this.status = status;
    this.reason = reason;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    // A dead backend and a rejected request must not read the same. This is
    // the one the owner sees when uvicorn is not running at all.
    throw new ApiError(0, "Couldn't reach the server. Is it running?");
  }
  if (!response.ok) {
    let detail = `${response.status}`;
    let reason: string | undefined;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
      if (typeof body?.reason === "string") reason = body.reason;
    } catch {
      /* a 413 or 431 has no JSON body at all */
    }
    throw new ApiError(response.status, detail, reason);
  }
  return (await response.json()) as T;
}

const q = (params: Record<string, string | boolean>) =>
  new URLSearchParams(
    Object.entries(params).map(([k, v]) => [k, String(v)]),
  ).toString();

export const getStats = () => request<Stats>("/stats");

export const listSources = () => request<Source[]>("/source");

/* Parse only -- writes nothing, and returns any confirmed fact that already
 * answers the same subject and attribute differently. The two-step exists
 * because the backend says so in its own docstring: "A mis-parse written blind
 * becomes a confirmed fact, and confirmed is precisely what nothing downstream
 * questions." */
export const parseFact = (line: string) =>
  request<FactResult>(`/fact?${q({ line })}`, { method: "POST" });

export const writeFact = (line: string) =>
  request<FactResult>(`/fact?${q({ line, confirm: true })}`, {
    method: "POST",
  });

/* MEASURED, 6 Sep 2026, by binary search with a raw socket against this server:
 * uvicorn accepts a request line up to ~65 468 URL characters and returns a
 * bare 400 from ~65 625. Two earlier attempts to measure this were wrong in
 * opposite directions and neither was measuring the server -- PowerShell's
 * `Invoke-WebRequest` refuses to bind a URI over ~65 536, and httpx raises
 * `InvalidURL: URL component 'query' too long` at the same size. Both look
 * exactly like a server limit and are limits of the tool doing the asking.
 *
 * Cyrillic is the binding case: every letter percent-encodes to six URL
 * characters (%D0%BA), so the ceiling is ~10 900 characters of Russian or
 * Uzbek Cyrillic, against ~65 000 of plain Latin. The limit below is set for
 * the worst case with room to spare, and the UI stops before the server does
 * because a 400 at this layer arrives with no JSON body and would surface to
 * the owner as a bare number. */
export const PASTE_LIMIT = 8000;

export const createPaste = (content: string, label?: string) =>
  request<Source>(
    `/source/paste?${q(label ? { content, label } : { content })}`,
    { method: "POST" },
  );

export const uploadFile = (file: File) => {
  const body = new FormData();
  body.append("file", file);
  return request<Source>("/source/upload", { method: "POST", body });
};

/* Synchronous on the server: transcription, extraction, prose chunking and
 * embedding all happen inside this one request. Tens of seconds is normal, and
 * there is no progress to report -- which is why the row says "Reading" and
 * nothing pretends to know how far along it is. */
// --- review -----------------------------------------------------------------

export const listProposals = () => request<Proposal[]>("/review");

export const listConflicts = () => request<Conflict[]>("/conflicts");

export const confirmFact = (id: string) =>
  request<Confirmed>(`/review/${id}/confirm`, { method: "POST" });

/* Corrects a proposal WITHOUT confirming it -- the backend is explicit that
 * these are two steps. "Fix" on the screen is therefore edit-then-confirm, two
 * requests, and if the first succeeds and the second fails the correction is
 * still saved. That is the right way round. */
export const editFact = (id: string, fields: Partial<ParsedFact>) =>
  request<{ id: string; edited: boolean; confirmed: boolean }>(
    `/review/${id}?${q(fields as Record<string, string>)}`,
    { method: "PATCH" },
  );

/* Unconfirmed facts only. There is no undo and no un-confirm endpoint: the
 * backend's own 404 says confirmed facts are removed elsewhere, because
 * rejecting means the extraction was wrong, not that the thing stopped being
 * true. The screen must not offer an Undo it cannot honour. */
export const rejectFact = (id: string) =>
  request<{ id: string; rejected: boolean }>(`/review/${id}`, {
    method: "DELETE",
  });

export const extractSource = (id: string) =>
  request<{ status: string; error: string | null; facts: unknown[] }>(
    `/source/${id}/extract`,
    { method: "POST" },
  );

/* --- the test console ----------------------------------------------------
 *
 * EVERY TYPE BELOW WAS READ OFF A LIVE RESPONSE, not off the docstrings. The
 * one bug this file has already produced came from writing a plausible key name
 * instead -- `conflict` for `conflicts` -- which type-checked, built clean, and
 * made the warning permanently invisible.
 *
 * What the live call actually returned, and what the mockup did not predict:
 * ONE question produced 28 provenance items with used_count 1. The prototype
 * draws five hand-written lines, which is the most misleading kind of fake
 * data -- right shape, wrong order of magnitude. Two of the 28 were
 * contradicting opening-hours facts, one typed and one extracted from a price
 * list. The screen shows what was used and collapses the rest. */

export type Suggestion = {
  question: string;
  language: string;
  /* "Avisena Med / ish vaqti" -- the subject and attribute the question
   * resolves back to. The screen does not render it today; it is here because
   * it is what makes the "all three have answers" claim checkable. */
  resolves_to: string;
};

export type Provenance = {
  kind: "fact" | "chunk";
  id: string;
  origin: "typed" | "extracted";
  /* Demonstrably present in the reply. Facts are matched on exact value,
   * chunks on word overlap -- a chunk is not unused merely because the reply
   * paraphrased it, which happens whenever the customer's language differs
   * from the document's. */
  used: boolean;
  source_label: string | null;
  source_filename: string | null;
  similarity: number | null;
  /* kind === "fact" */
  subject?: string;
  attribute?: string;
  value?: string;
  created_at?: string | null;
  /* kind === "chunk" */
  quote?: string;
  quoted?: boolean;
};

export type ConsoleAnswer = {
  question: string;
  /* CAN BE NULL, AND CAN ALSO BE A POLITE REFUSAL -- both under status
   * "unknown". app/answer.py sets `answer = reply` when the model was called
   * and declined, and `answer = None` at line 628 when nothing cleared the
   * similarity floor and the model was never called at all. A screen that
   * treated null as "the refusal case" would render its own wording over the
   * agent's on the first, and nothing on the second. Branch on `status`. */
  answer: string | null;
  /* FIVE, not four. "unknown" was missing from the first draft of this type --
   * it is the most common refusal there is, and it came back from a live call
   * as `status: "unknown"` with a real Uzbek sentence in `answer`. Read off
   * the wire, like everything else here. */
  status: "ok" | "triage" | "not_found" | "ambiguous" | "unknown";
  route: string | null;
  language: string;
  wrong_script: boolean;
  provenance: Provenance[];
  used_count: number;
  /* "unavailable_cross_script" means text matching could not run at all,
   * because the reply and its sources are in different alphabets. Rendering
   * "0 sources used" there would report a limitation as a finding. */
  used_detection: "text_match" | "unavailable_cross_script";
};

/* A near-miss: something that scored but did not answer. */
export type Nearest = {
  subject: string;
  attribute: string;
  value: string;
  similarity: number | null;
};

/* THREE SHAPES, NOT ONE, and the first version of this type had only the
 * common fields -- which would have thrown away the most useful half of the
 * response without any error to say so.
 *
 * `review_nearest` carries the five things that ALMOST answered, with scores.
 * That list is the whole point: "your wording and the customer's differ" is
 * only actionable if you can see which entry should have matched.
 *
 * `choose` carries the five facts the answer came from, and asks the one
 * question code cannot decide -- whether a retrieved fact is factually wrong,
 * or a correct fact was used wrongly. The answer goes back as the `reason`
 * argument to consoleFeedback, which is why that parameter exists. */
export type NextStep = {
  action: "none" | "report_bug" | "review_nearest" | "choose";
  reason: string;
  /* Prewritten by the server, for all five cases. The screen renders this
   * string; it does not compose its own advice. An owner sent to fix a fact
   * when the real problem is a missing alias will edit correct data. */
  text: string | null;
  /* action === "review_nearest" */
  nearest_score?: number | null;
  nearest?: Nearest[];
  /* action === "choose" */
  choices?: ("fact_is_wrong" | "used_the_wrong_fact")[];
  chosen?: string | null;
  facts?: { id: string; subject: string; attribute: string; value: string }[];
};

export type VerdictResult = {
  stored: boolean;
  next_step: NextStep;
  wrong_script: boolean;
};

export const consoleSuggestions = (limit = 3) =>
  request<Suggestion[]>(`/console/suggestions?${q({ limit: String(limit) })}`);

/* SPENDS: one embedding and at least one generation. */
export const consoleAsk = (question: string, fromSuggestion = false) =>
  request<ConsoleAnswer>(
    `/console/ask?${q({ q: question, from_suggestion: fromSuggestion })}`,
    { method: "POST" },
  );

/* SPENDS A FULL ANSWER, deliberately. The server re-derives the reply rather
 * than accepting one from the browser, because a verdict stored against a
 * client-supplied answer records what the browser claimed, not what the agent
 * did. Clicking Right or Wrong therefore costs a generation.
 *
 * AND SO DOES ANSWERING THE FOLLOW-UP. Marking something wrong can return a
 * `choose` step, and sending the chosen reason back calls this again -- a
 * second full answer for the same question. Two clicks, two generations. That
 * is the price of the most valuable record this product makes, and it is worth
 * knowing rather than discovering. */
export const consoleFeedback = (
  question: string,
  verdict: "right" | "wrong",
  reason?: string,
) =>
  request<VerdictResult>(
    `/console/feedback?${q(
      reason ? { q: question, verdict, reason } : { q: question, verdict },
    )}`,
    { method: "POST" },
  );

/* --- signing in ----------------------------------------------------------
 *
 * No credentials option on any of these: the cookie is same-origin, and fetch
 * sends same-origin cookies by default. `credentials: "include"` would only be
 * needed if the API lived on another host, which is exactly the arrangement
 * one-process deployment exists to avoid.
 *
 * The session cookie is httpOnly, so nothing here can read it. That is the
 * point -- the only way to ask "am I signed in" is to ask the server. */

export type Me = {
  business: string | null;
  email: string | null;
  /* Whether this business may spend money -- see app/approval.py. It is NOT
   * what stops the spending; the server does that, in the two functions that
   * hold the API credentials, and it would still stop it with this whole file
   * deleted. This exists so a screen can say what is switched off BEFORE
   * someone clicks it, instead of letting them pick a document, wait, and read
   * a 403. The browser copy can only ever be a courtesy. */
  approved: boolean;
};

/* The same value, where a screen deep in the tree can read it without four
 * components passing it down. Written once by App on load, before either screen
 * renders -- App returns null until getMe() resolves, so there is no window in
 * which a screen reads the default.
 *
 * A plain module variable rather than a context, because it is read in a
 * handful of `disabled=` expressions and never rendered, so nothing needs to
 * re-render when it changes: it changes on reload and not otherwise. A context
 * would be the same information with a provider around it.
 *
 * It defaults to TRUE on purpose. If this were somehow read before it is set,
 * the failure would be an enabled button that the server then refuses -- one
 * wasted click. Defaulting to false would grey out the product for an approved
 * business because of a race in the browser, which is the same information and
 * a much worse mistake. */
export let spendingAllowed = true;

export function setSpendingAllowed(value: boolean): void {
  spendingAllowed = value;
}

/* Answers 200 whether or not you are signed in. A 401 here would be the
 * ordinary logged-out case reported as a failure, which makes every browser
 * console look like something is broken and makes this call impossible to
 * distinguish from a real error. */
export function getMe(): Promise<Me> {
  return request<Me>("/auth/me");
}

/* Form-encoded, not JSON: the endpoint takes Form(...) so the browser's own
 * encoding is what it expects. */
export function requestLink(email: string): Promise<{ message: string }> {
  return request<{ message: string }>("/auth/request", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ email }).toString(),
  });
}

/* Creates an UNAPPROVED business and mails a sign-in link.
 *
 * It answers exactly what requestLink() answers, including when the address is
 * already taken -- the endpoint deliberately will not say which happened, so
 * there is nothing here to branch on and nothing for a screen to leak. If this
 * ever starts returning "already registered", that is a bug in the server and
 * not a feature to render. */
export function signUp(email: string, name: string): Promise<{ message: string }> {
  return request<{ message: string }>("/auth/signup", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ email, name }).toString(),
  });
}

export function logout(): Promise<{ signed_out: boolean }> {
  return request<{ signed_out: boolean }>("/auth/logout", { method: "POST" });
}

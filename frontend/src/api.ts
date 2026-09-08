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
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
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
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* a 413 or 431 has no JSON body at all */
    }
    throw new ApiError(response.status, detail);
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

/* --- signing in ----------------------------------------------------------
 *
 * No credentials option on any of these: the cookie is same-origin, and fetch
 * sends same-origin cookies by default. `credentials: "include"` would only be
 * needed if the API lived on another host, which is exactly the arrangement
 * one-process deployment exists to avoid.
 *
 * The session cookie is httpOnly, so nothing here can read it. That is the
 * point -- the only way to ask "am I signed in" is to ask the server. */

export type Me = { business: string | null; email: string | null };

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

export function logout(): Promise<{ signed_out: boolean }> {
  return request<{ signed_out: boolean }>("/auth/logout", { method: "POST" });
}

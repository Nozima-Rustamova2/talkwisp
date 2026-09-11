"""Resolve a follow-up into a standalone question. Nothing else.

    "Koʻproq maʼlumot ber"  + previous turn about the cardiologist
        -> "Kardiolog qabuli haqida koʻproq maʼlumot"
        -> that goes through the existing pipeline, unchanged

HISTORY REWRITES THE QUESTION. IT NEVER ANSWERS IT.

That split is the whole design. The refusal guarantee is a property of the
code, not of the model behaving well: `answer()` refuses when retrieval returns
nothing, and the model never sees text that was not retrieved. Putting
conversation history into the ANSWERING prompt would break that by
construction, because the bot's own earlier replies are unretrieved and
unverified -- so one wrong answer would become source material for the next,
and a single mistake would become a persistent one.

Here the output is a QUESTION. It still has to survive retrieval, the
similarity floor, NO_ANSWER and the gap log, all unchanged. A bad rewrite
produces a bad search, not a fabricated answer.
"""

from app.approval import NotApproved
from app.llm import complete
from app.normalize import normalize

# Words that only mean something relative to an earlier turn. Deliberately
# short: a longer list makes the trigger fire on more self-contained questions,
# and rewriting a question that already works is the failure mode to avoid.
_REFERENCE_WORDS = {
    # Uzbek
    "shu", "shuni", "shunga", "shundan", "uni", "unga", "undan", "u",
    "yana", "koproq", "kop", "esa", "ham", "bunga", "buni", "bu",
    # Russian
    "esho", "eshe", "ego", "etogo", "etot", "bolshe", "a", "tam", "on",
}

# Two, not four. Uzbek questions are compact: "Yakshanba kuni ishlaysizmi?" and
# "Kardiolog qabuli qancha turadi?" are three and four words and completely
# self-contained -- a looser threshold sent both to the rewriter, which is the
# false positive this whole gate exists to avoid. Genuine follow-ups are either
# very short ("nech pul") or carry a reference word ("koʻproq maʼlumot ber").
MAX_WORDS_WITHOUT_REFERENCE = 2

_SYSTEM = """You rewrite a customer's latest message into a question that stands
on its own, using the conversation so far only to resolve what it refers to.

Return ONLY the rewritten question. No answer, no explanation, no quotes.

Rules:
- If the message ALREADY stands on its own, return it EXACTLY as it is,
  character for character. Most messages need no change, and changing one that
  worked is worse than leaving one unresolved.
- Only resolve references: "more information" -> more information about what,
  "how much" -> how much for what, "and Saturday?" -> and is it open Saturday.
- Take the subject from the conversation. Never add a fact, a price, a time or
  a name that does not appear in the conversation.
- NEVER answer the question. Your output is a question, always.
- Keep the customer's language and script.
- If the message refers to something not in the conversation at all, return it
  unchanged. A bad guess sends the search somewhere wrong."""


def needs_rewrite(question: str, history: list) -> bool:
    """Cheap gate, so most messages never cost an extra model call.

    Deliberately lets some self-contained questions through -- "Kardiolog narxi
    qancha?" is three words and will trip this. The prompt's instruction to
    return such a question unchanged is the second line of defence, and logging
    both texts is what makes a corruption visible instead of mysterious.
    """
    if not history:
        return False
    words = normalize(question).split()
    if not words:
        return False
    if len(words) <= MAX_WORDS_WITHOUT_REFERENCE:
        return True
    return any(w in _REFERENCE_WORDS for w in words)


def rewrite(history: list, question: str) -> tuple[str, bool]:
    """Returns (question_for_retrieval, was_rewritten).

    `history` is a list of (customer_message, bot_answer), oldest first.
    """
    if not needs_rewrite(question, history):
        return question, False

    lines = []
    for asked, answered in history:
        lines.append(f"Customer: {asked}")
        # Truncated: the rewrite needs the SUBJECT of the previous turn, not
        # its full wording, and a long history is a long prompt on every
        # follow-up.
        lines.append(f"Assistant: {(answered or '(no answer)')[:200]}")

    try:
        rewritten = complete(
            _SYSTEM,
            "Conversation so far:\n" + "\n".join(lines)
            + f"\n\nLatest message: {question}\n\nRewritten question:",
        ).strip().strip('"')
    except NotApproved:
        # Falling back here would be wrong twice over: it hides the first thing
        # the gate stopped, and it carries on to embed_query() which stops it
        # again -- so the refusal still happens, just with one silent step in
        # front of it. A gate that is caught and ignored somewhere is a gate you
        # cannot reason about from its call sites.
        raise
    except Exception:  # noqa: BLE001
        # A failed rewrite must not cost the customer an answer. Fall back to
        # the question they actually typed.
        return question, False

    # A rewrite that produces nothing, or something implausibly long, is a
    # model going off the rails -- an explanation or an answer rather than a
    # question. Use the original.
    if not rewritten or len(rewritten) > max(200, len(question) * 8):
        return question, False

    return rewritten, normalize(rewritten) != normalize(question)

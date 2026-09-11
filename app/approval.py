"""The spending gate. One question: may this business cost us money right now.

WHY THIS IS NOT ON THE ROUTES
-----------------------------
The obvious design is a dependency on the endpoints that spend. It was tried on
paper and the list was wrong twice before a line of it was written.

Traced rather than remembered, eight of the twenty-five endpoints spend, and two
of them do not look like it: POST /console/feedback re-answers the question to
attach the route and the scores to a verdict, and POST /review/{id}/confirm
re-embeds the fact it confirms. Meanwhile the two that sound expensive --
/source/paste and /source/upload -- store bytes and spend nothing; the money
goes at extract. A decorator list assembled by reading the route names would
have blocked two free endpoints and missed six paid ones.

That is not a mistake to be more careful about next time. It is the shape of the
problem: what an endpoint costs is a property of what it calls, three modules
down, and route names do not carry it.

So the check sits where the credentials are used instead -- llm.complete() and
embeddings._embed(), which every generation and every embedding in the codebase
goes through. A new endpoint inherits the gate by calling a model AT ALL, not by
remembering anything. Same property as current_business(): the safe thing is the
only thing.

It also covers the two callers that are not endpoints and never would have been
touched by a route decorator: bot.py answers Telegram messages without going
near FastAPI, and onboard.py ingests an entire business from the command line.

WHAT IS DELIBERATELY NOT GATED. Reading is free: /ask answers from facts with no
model at all, listing sources, the review queue, the suggestions endpoint (it
uses find(), not answer()), signing in, and looking around a console with
nothing in it. An unapproved business gets a real, working, empty product. The
gate is on spending, and only on spending.

NOT GATED EITHER, and this one matters: llm.check_reachable() calls the provider
directly rather than through complete(), so the boot probe still runs. That is
platform money, spent once per process with no tenant bound -- gating it would
mean the app could not start.
"""

from app.db import current_approved, current_business_id


class NotApproved(Exception):
    """Raised instead of spending. Carries what was refused, for the message.

    A distinct type because the three callers want three different things from
    it: the API turns it into 403, the bot says something a customer can read,
    and a script prints it and stops. A ValueError would have been caught by
    handlers meant for other failures -- app/extract.py catches Exception around
    its model calls and records it as a failed source, which would turn "not
    approved yet" into a permanent extraction error on the source row.
    """

    def __init__(self, action: str) -> None:
        self.action = action
        self.business_id = current_business_id()
        super().__init__(
            f"This business is not approved yet, so {action} is switched off. "
            f"Approve it with:  onboard.py --approve <name>")


def assert_approved(action: str) -> None:
    """Called from the two functions that hold the credentials. Nowhere else.

    Resist adding a second call site. Two gates that can disagree are worse than
    one, and the value of this one is that its coverage is a fact about the
    import graph rather than a list someone maintains.
    """
    if not current_approved():
        raise NotApproved(action)

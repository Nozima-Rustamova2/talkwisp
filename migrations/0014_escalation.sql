-- Takeover: a question the agent could not answer, passed to the owner.
--
-- THE SIXTH TABLE, AND THE RULE THAT PERMITS IT. From design-decisions:
--
--   Files hold append-only observations. Tables hold mutable state with a
--   lifecycle that someone is waiting on.
--
-- gaps.jsonl stays a file -- it is written once and never changed, and losing a
-- line costs a number in a report. An escalation is mutated (waiting ->
-- answering -> answered), queried by state, swept on a timer, and if one is
-- lost A REAL PERSON IS LEFT WAITING FOR AN ANSWER THEY WERE PROMISED. The
-- four-table rule still binds, because it binds the knowledge model, and this
-- is not knowledge -- the knowledge is the `fact` this eventually writes.
--
-- The landing page has promised this in three languages since the site went up:
-- "it asks the customer whether to pass the question to you, then sends it to
-- your Telegram. You reply as yourself, and your answer is saved so it knows
-- next time." Both halves matter: without the fact-write it is a relay, and the
-- owner answers the same question again next month.

create table escalation (
    id            uuid primary key default uuidv7(),
    business_id   uuid not null references business(id)
                       default app_current_business(),

    -- EVERYONE WAITING ON THIS ANSWER, not one customer.
    --
    -- If three people ask the same unanswerable thing before the owner replies,
    -- the owner should be pinged ONCE and all three should get the answer. A
    -- row per customer would ping three times for one question, which is how a
    -- useful feature becomes one people mute -- and it would leave two of them
    -- waiting on an answer that was already given.
    chat_ids      bigint[] not null,

    question      text not null,
    -- Normalized, so "bolalarga chegirma bormi" and "Bolalarga chegirma bormi?"
    -- join one escalation rather than opening two.
    question_key  text not null,

    -- THE SNAPSHOT, and the reason it is stored rather than looked up later.
    --
    -- The bot's conversation history is in-memory, per process, and idles out --
    -- and the supervisor restarts bots routinely now. Resolving context when the
    -- owner opens the message half an hour later would find nothing and deliver
    -- exactly the bare question that makes an escalation unanswerable. Captured
    -- at the moment of escalation, when it is still true.
    --
    -- Holds: the previous turns, the language, and WHY retrieval stopped. That
    -- last one distinguishes "you never told me this" from "you told me and I
    -- could not find it" -- a 0.41 miss and a 0.02 miss are different problems,
    -- and app/answer.py's _log_gap already computes exactly those numbers.
    context       jsonb not null default '{}'::jsonb,

    status        text not null default 'waiting'
                  check (status in ('waiting', 'answering', 'answered',
                                    'expired')),
    answer        text,
    -- What the reply became, if the owner let it be written. Nullable because
    -- "Skip -- not for us" is a real outcome that should still close the row
    -- and still tell the customer something.
    --
    -- ON DELETE SET NULL, not cascade: rejecting the fact in the review queue
    -- must not delete the record that a customer once asked and was answered.
    fact_id       uuid references fact(id) on delete set null,

    created_at    timestamptz not null default now(),
    answered_at   timestamptz
);

-- The owner's queue: what is still waiting, oldest first.
create index escalation_waiting_idx
    on escalation (business_id, created_at)
    where status in ('waiting', 'answering');

-- The join-an-existing-one lookup, on the hot path of every refusal.
create index escalation_question_idx
    on escalation (business_id, question_key)
    where status = 'waiting';

-- Tenancy, exactly as 0007 does it for every other tenant table. ENABLE and
-- FORCE, because without FORCE the owner of the table is exempt from its own
-- policy and this file would be decorative.
alter table escalation enable row level security;
alter table escalation force row level security;
create policy tenant_isolation on escalation
    using (business_id = app_current_business())
    with check (business_id = app_current_business());

grant select, insert, update, delete on escalation to talkwisp_app;

-- Deliberately absent: a delete path for the customer, and any way to reach an
-- escalation without a tenant. The bot reads its own through the policy above,
-- which is as wide as it needs to be.

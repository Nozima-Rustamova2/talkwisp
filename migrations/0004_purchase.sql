-- A fifth table, approved. The rule that permits it:
--
--   Files hold append-only observations. Tables hold mutable state with a
--   lifecycle that someone is waiting on.
--
-- gaps.jsonl and feedback.jsonl are written once and never changed; losing a
-- line costs a number in a report. An order is mutated, queried by state, and
-- if one is lost a real person who paid money is stranded. That is the
-- difference, and it is why this is a table and those are not.
--
-- This table holds no knowledge about the business. The four-table rule binds
-- the knowledge model, and it still does: the PRICE lives in fact, and an order
-- only records which fact it was read from and what happened next.

create table purchase (
    id           uuid primary key default uuidv7(),

    -- Telegram chat, so the customer can be reached again. bigint, not int:
    -- Telegram ids passed 2^31 years ago.
    chat_id      bigint not null,

    -- What was ordered, in the words the customer will see. Denormalised on
    -- purpose: if the owner renames or deletes the fact tomorrow, an order
    -- placed today must still say what it was for.
    item         text not null,
    subject_key  text not null,
    attribute    text not null,

    -- The price as it was read from the fact table, and the amount actually
    -- asked for. Stored separately so a mismatch is visible rather than
    -- reconstructed by subtraction.
    base_amount  bigint not null check (base_amount > 0),
    suffix       smallint not null check (suffix between 1 and 50),
    amount       bigint not null check (amount = base_amount + suffix),

    -- Named for what the system is waiting on, not for what is true.
    --
    -- owner_confirmed, NOT paid. We cannot verify a payment: the state records
    -- that the owner asserted it, and nothing more. A state called `paid` would
    -- be this system claiming knowledge it does not have, every time anyone
    -- reads the table.
    state        text not null default 'awaiting_payment'
                   check (state in ('awaiting_payment', 'awaiting_owner',
                                    'owner_confirmed', 'owner_rejected',
                                    'expired', 'cancelled')),

    -- Telegram's own handle for the photo. No bytes: forwarding by file_id
    -- costs nothing and cannot fail halfway. The trade is that the evidence is
    -- Telegram's to keep; add a bytea column the day a dispute needs one.
    screenshot_file_id text,

    -- Structural, not advisory: "reject needs a reason" is a rule the database
    -- enforces, because the two reasons lead to different next steps and a null
    -- one leads nowhere.
    reject_reason text,
    check (state <> 'owner_rejected' or reject_reason is not null),

    created_at         timestamptz not null default now(),
    expires_at         timestamptz not null,
    screenshot_at      timestamptz,
    owner_confirmed_at timestamptz,
    owner_rejected_at  timestamptz
);

-- The backstop for the unique-amount trick. Two open orders sharing an amount
-- makes the seller unable to tell which payment is which, which is the one
-- thing this whole feature exists to prevent.
--
-- This covers OPEN orders only. The wider seven-day reuse window lives in
-- app/orders.py because an index predicate has to be immutable and now() is
-- not. The index is the guarantee; the code is the policy.
create unique index purchase_open_amount_idx on purchase (amount)
    where state in ('awaiting_payment', 'awaiting_owner');

-- The expiry sweep, which runs on every poll of the bot loop.
create index purchase_due_idx on purchase (expires_at)
    where state in ('awaiting_payment', 'awaiting_owner');

-- One customer's orders, newest first.
create index purchase_chat_idx on purchase (chat_id, created_at desc);

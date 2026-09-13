-- When the owner was last told that a customer tried to pay and could not.
--
-- WHY THIS EXISTS. A self-serve business connects a bot, a customer taps buy,
-- an order is created, and order_message() returns None at the last step
-- because no payment details were ever set. The customer was told "contact us"
-- and the owner was told nothing at all. It happened to a real business on
-- 2026-09-13 and it would have happened to every self-serve business, because
-- until now there was no route for an owner to set payment details at all --
-- check_subject()'s `allow_reserved` had no caller outside seed.py.
--
-- The guard in bot.py now stops the dead end: the buy button is not offered
-- unless the payment message can actually be assembled, and the customer gets
-- their price question answered normally instead. THAT MAKES THE LOST SALE
-- INVISIBLE, which is why this column matters. Once the dead end is gone, the
-- owner has no symptom to notice -- only silence where a sale would have been.
-- This and the dashboard row are the only two signals left.
alter table business add column payment_gap_notified_at timestamptz;

-- ONCE PER BUSINESS PER DAY, CLAIMED ATOMICALLY, NOT ONCE PER ORDER.
--
-- A notification per attempt would fire on every customer who asks about a
-- price all afternoon, and the owner would mute the bot -- which costs them the
-- escalation pings too, the feature the landing page actually promises. The
-- same argument the escalation ping makes for pinging once and answering
-- everyone.
--
-- The dedupe is the UPDATE's own WHERE clause rather than a read-then-write in
-- Python, because the supervisor runs up to fourteen bots and a business can be
-- handled by one process while another is restarting. Two processes reading
-- "not notified today" and both sending is exactly the race a claim avoids:
-- the row is returned only to the caller whose UPDATE actually changed it.
--
-- SECURITY DEFINER for the reason every writer of this table is: talkwisp_app
-- has SELECT on business and nothing else (0007), and the bot runs as that
-- role. now() is written here rather than taken as an argument so a caller
-- cannot claim to have notified at a time it chooses.
create function app_business_claim_payment_gap(biz uuid) returns boolean
language sql volatile security definer set search_path = pg_catalog, public as $fn$
    with claimed as (
        update business set payment_gap_notified_at = now()
        where id = biz
          and (payment_gap_notified_at is null
               or payment_gap_notified_at < now() - interval '1 day')
        returning id
    )
    select exists (select 1 from claimed)
$fn$;

grant execute on function app_business_claim_payment_gap(uuid) to talkwisp_app;

-- Facts that stop being true on a date, and the agent stops saying them.
--
-- A promotion, a temporary schedule, a doctor covering for two weeks. Today
-- those sit in the knowledge base forever and the agent quotes a discount that
-- ended in March.
--
-- DEFAULT IS NO EXPIRY. Facts last until changed, as now. Expiry is opt-in per
-- fact, so every existing row and every future one written without a date
-- behaves exactly as it does today. That is why the column is nullable rather
-- than defaulted -- a default here would silently put a clock on 140 facts
-- nobody asked to expire.
alter table fact add column expires_at timestamptz;

-- When the owner was warned it is about to lapse. NULL means not yet told.
--
-- Without this the sweep would warn every five minutes for two days, which is
-- how a useful notification becomes one people mute -- the same argument as
-- pinging the owner once per escalation rather than once per customer.
alter table fact add column expiry_notified_at timestamptz;

-- Marks a subject+attribute that is SUPPOSED to have several values.
--
-- The knowledge base flags "same subject and attribute, different values",
-- which catches a stale price and a legitimate conditional one identically --
-- it cannot tell "1 200 000 so'm" beside "950 000 so'm (10 days before the
-- group starts)" from a price that simply went out of date. Wording it as a
-- conflict would teach an owner to delete the second one, so the flag is
-- neutral and this is how an owner says "both of these are meant to be here".
--
-- Set on every row of the pair, because the property belongs to the pair. A
-- separate table would be the tidier model and is not worth a sixth exception
-- to the four-table rule for one boolean.
alter table fact add column expected_multiple boolean not null default false;

-- The owner's queue: what lapses soon and has not been mentioned yet.
create index fact_expiring_idx on fact (business_id, expires_at)
    where expires_at is not null and expiry_notified_at is null;

-- ------------------------------------------------------- the retrieval gate --
--
-- WHERE THE FILTER GOES, and it goes exactly where the payment exclusion goes.
-- Every retrieval path in the codebase already selects from this view:
-- app/answer.py (twice, plus the payment lookup), app/retrieval.py (four
-- times), app/buy.py (twice), app/orders.py. One clause covers all of them, and
-- a retrieval path added next year inherits it by selecting from the view
-- rather than by someone remembering a WHERE at the call site.
--
-- EXPIRED MEANS NOT RETRIEVED, NOT DELETED. The row stays. The knowledge base
-- still shows it, marked, with a way to extend or reactivate -- because if it
-- vanished the owner could not see what happened or why the agent stopped
-- saying it, and "my agent stopped mentioning the discount" with no visible
-- cause is a support message rather than a self-service fix.
--
-- security_invoker is carried over from 0007: without it the view would apply
-- RLS as its OWNER rather than as the querying tenant, and every business would
-- read every other business's facts through it.
drop view retrievable_fact;
create view retrievable_fact with (security_invoker = true) as
    select * from fact
     where subject_key <> 'tolov malumotlari'
       and (expires_at is null or expires_at > now());

-- RE-GRANTED, BECAUSE DROP VIEW DESTROYS THE GRANT.
--
-- A dropped view takes its privileges with it: the recreated one is a new
-- object that talkwisp_app cannot read. Without this line every retrieval path
-- in the product fails with "permission denied for view retrievable_fact" --
-- the bot answers nothing, the console answers nothing, and the migration that
-- caused it reports success.
--
-- 0007 does the same drop-and-recreate and re-grants sixty lines later, which
-- is easy to read past. Measured here rather than assumed: the first probe
-- after applying this file failed on exactly that.
grant select on retrievable_fact to talkwisp_app;

-- Note what is NOT here: `confirmed`. The view has never filtered on it and
-- must not start -- app/review.py reads unconfirmed rows through `fact`
-- directly, and retrieval.py applies `where confirmed` itself where it matters.
-- Adding it here would silently change what the review queue can see.

-- WHICH OWNER is answering this escalation, not merely that someone is.
--
-- A LIVE CORRECTNESS BUG, and it exists today with a single owner. The claim
-- was per BUSINESS: start_answering() released every other row in the
-- `answering` state, and answering() read `limit 1` with no idea who had
-- tapped. One person tapping Answer on a second escalation already releases
-- the first -- the code says so deliberately, because two half-answered
-- questions with no way to tell which the next message belongs to is worse
-- than losing a tap.
--
-- That reasoning holds for one person and collapses for two:
--
--   owner A taps Answer on X          X is answering
--   owner B taps Answer on Y          X is RELEASED, Y is answering
--   owner A types their answer        it is delivered to Y's customer
--
-- A's answer to one customer reaches a different customer, and the fact written
-- from it is attached to the wrong question. Not a degraded experience -- a
-- wrong answer sent to a real person, silently.
--
-- With one owner it is harmless, and that is luck rather than design. So this
-- lands on its own, before anything about multiple owners, because it is a bug
-- in what is deployed rather than a prerequisite for what is not.
alter table escalation add column answering_by bigint;

-- The claim and the release are now per owner. A row is `answering` for exactly
-- one person, and another owner tapping Answer on their own escalation cannot
-- release it.
--
-- Nothing here enforces that answering_by is an owner: authorisation is the
-- bot's job and the column only records whose claim it is. A constraint would
-- need to reference a table that does not exist yet, and would break the day an
-- owner is removed while holding a claim.
comment on column escalation.answering_by is
    'Telegram id of the owner who tapped Answer. Null when status is not '
    '''answering''. Scopes the claim so one owner cannot release another''s.';

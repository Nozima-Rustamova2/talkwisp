-- A fact that must never be retrieved.
--
-- Payment details -- card number, cardholder, bank -- are stored as facts so
-- the owner can edit them like anything else. But a fact is retrievable, and a
-- retrieved fact goes into the answering prompt as context. Storing the card
-- number as a fact therefore hands it to the model through the front door,
-- which is exactly what assembling the payment message in code was meant to
-- prevent. Storing it as a fact does not keep it away from the model.
-- Excluding it from retrieval does.
--
-- This is a category nothing else in the schema has. It is not a bug and it
-- must not be "fixed" -- see docs/design-decisions.md. Same reasoning as the
-- emergency numbers in triage.py: a model that paraphrases a card number is a
-- model that can get it wrong, and there is no acceptable failure there.
--
-- The exclusion lives HERE, below app/, and not as a WHERE clause repeated
-- across the eight query arms that can return a fact. Eight places is eight
-- chances to forget, and the ninth query written next year would inherit
-- nothing while nothing failed.

-- The literal must equal normalize("Toʻlov maʼlumotlari"), which is the
-- canonical PAYMENT_SUBJECT_KEY in app/payment.py. Kept in both places on
-- purpose: SQL cannot call normalize(), and check_payment.py asserts the two
-- agree rather than trusting this comment.
--                                            v-- keep in sync with app/payment.py

-- ---------------------------------------------------------------- the view --
--
-- The naming does the work: a query that reads `fact` is EDITING, a query that
-- reads `retrievable_fact` is ANSWERING. The console and the review queue must
-- keep reading `fact` -- the owner has to be able to change their own card
-- number -- and every retrieval path reads the view.
--
-- `select *` freezes the column list at creation time, so a column added to
-- `fact` later will not appear here. That fails loudly at the first query that
-- names it, which is the acceptable direction: a missing column is an error, a
-- missing WHERE clause is a leaked card number.
create view retrievable_fact as
    select * from fact where subject_key <> 'tolov malumotlari';

-- ---------------------------------------------------- no embedding, ever --
--
-- Both vector searches already say `where embedding is not null`. A payment
-- fact that CANNOT carry an embedding is therefore unreachable from either of
-- them by construction -- closed by the database rather than by the text of a
-- WHERE clause somebody may rewrite.
--
-- seed.py and typed.store() skip embedding for this subject. This constraint
-- is what catches it when one of them stops. It is the floor, not the policy.
alter table fact add constraint fact_payment_not_embedded
    check (subject_key <> 'tolov malumotlari' or embedding is null);

-- ------------------------------------------------------- the separator --
--
-- A subject containing " / " is indistinguishable from the subject/attribute
-- form it is embedded as, and it breaks grading's split on `want`.
--
-- This was already guarded -- by a bare `assert` over seed.py's own literal
-- lists, at import time. That guard covered one of the four doors a fact can
-- come through (seed, typed.store, review.confirm, extract), and `assert`
-- disappears under `python -O`, so in production it was a guard that was not
-- there. One constraint covers every door, including doors not yet written.
alter table fact add constraint fact_subject_no_separator
    check (subject not like '% / %');

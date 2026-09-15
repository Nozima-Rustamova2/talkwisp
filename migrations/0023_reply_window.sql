-- How long a customer should expect to wait for a forwarded answer.
--
-- TWO DIFFERENT THINGS SHARE THIS NUMBER, and they are deliberately not the
-- same thing. Read this before changing either.
--
--   the customer-facing sentence is A CLAIM ABOUT THE BUSINESS. "They usually
--   reply within 3 hours" describes what this business does. It is not a
--   promise the system makes, because nothing here makes an owner reply -- the
--   expiry apology exists precisely because often they will not, and a promise
--   that fails is worse than the refusal it replaced.
--
--   the sweep is A BACKSTOP. It decides when a customer who was told "I've
--   sent it" is told that no answer came, so silence does not last forever.
--
-- THE SWEEP TAKES max(stated, 24h), NOT the stated window. An owner who types
-- "2 hours" to sound responsive would otherwise make the system apologise after
-- two -- so a customer whose question is answered on hour three would get "no
-- reply yet, please contact us directly" and THEN the answer, which is worse
-- than today for that customer. The floor means a short window is honest in the
-- copy and cannot shorten anyone's patience.
--
-- A LONG WINDOW MOVES BOTH. Set 48 and the copy says two days and the sweep
-- waits two days, so a business that genuinely takes that long stops
-- apologising after one. That is the direction that matters, and it works.
--
-- Rejected: sweeping at the stated window times two. It does what is wanted and
-- hides a multiplier nobody can see -- in six months someone reads "apology
-- after the stated window", watches it fire at double, and cannot find why.
alter table business add column reply_window_hours smallint
    check (reply_window_hours is null
           or reply_window_hours between 1 and 168);

-- NULL IS THE DEFAULT AND MEANS TODAY. No time is stated to the customer at
-- all, and the sweep runs at the 24 hours it already ran at -- so a business
-- that never opens this field sees no change whatsoever. "Unset" is not "zero"
-- and not "immediately"; it is the absence of a claim, which is the honest
-- thing to say when nobody has told us anything.
comment on column business.reply_window_hours is
    'Hours the business usually takes to answer a forwarded question. NULL '
    'means no claim is made: the customer is told nothing about timing and '
    'the expiry sweep uses its own 24-hour default.';


-- A DEDICATED SETTER rather than a parameter on app_business_set_style().
--
-- Adding an argument there means DROPPING and recreating that function --
-- CREATE OR REPLACE cannot alter an argument list -- and dropping destroys the
-- grant. That is how 0015 lost the retrievable_fact grant and what 0022 walked
-- into on the claim function. Avoiding it a third time by writing a separate
-- two-argument function costs nothing.
create function app_business_set_reply_window(biz uuid, hours smallint)
returns void
language sql volatile security definer set search_path = pg_catalog, public as $fn$
    update business set reply_window_hours = hours where id = biz
$fn$;

grant execute on function app_business_set_reply_window(uuid, smallint)
    to talkwisp_app;

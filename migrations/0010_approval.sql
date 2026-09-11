-- The spending gate: a business may look around before it may cost money.
--
-- WHAT THIS IS FOR. Signup is about to become self-serve, so a stranger can
-- create a business row without anyone approving it first. Every model call
-- costs real money on a billing-enabled key, and nothing in the codebase counts
-- or caps it. So the row exists, the console works, the knowledge base is real
-- and empty -- and nothing that reaches a model runs until someone says yes.
--
-- THE GATE IS ON SPENDING, NOT ON THE BOT and not on signing in. Reading,
-- listing sources, the deterministic /ask path, the review queue: all free, all
-- open. That distinction is the whole design, and it is enforced one layer below
-- the endpoints, in app/approval.py, at the two functions that hold the
-- credentials. See that file for why it is not on the routes.
--
-- NOT a status enum, NOT a plan, NOT "paid". A boolean called `approved`,
-- because that is the only question being asked: has a human said yes to this
-- business spending our money. No billing, no usage counting, no trial limits.
-- If those ever arrive they are new columns, not new values in this one.

-- `default false` is the load-bearing half, and it is here rather than in the
-- signup path on purpose. A business row that appears by ANY route -- the new
-- signup endpoint, onboard.py, a psql session at 2am -- is unapproved unless
-- someone said otherwise. Same shape as default-deny on auth: the safe state is
-- what you get for free, and the unsafe one costs a deliberate act.
alter table business add column approved boolean not null default false;

-- Existing rows are approved, which is not a contradiction of the line above.
-- Every business that exists today was created by hand with onboard.py, by me,
-- on the box -- which is precisely what approval means. Backfilling false would
-- not be conservative, it would silently stop the live bot answering the moment
-- this migration ran, with no error anywhere to say why.
update business set approved = true;

-- --------------------------------------------------------------- signing up --
--
-- SECURITY DEFINER because the app role has `select` on business and nothing
-- else (0007's grants), which is what stops a bug in the web app from renaming
-- a tenant or reading another's bot_token. Signup needs an INSERT, so it gets
-- exactly one, through a function that decides the shape of the row itself.
--
-- NOTE WHAT IS NOT A PARAMETER. `approved` is written as false in the body and
-- cannot be passed in. So the web app can create a business -- and has no way
-- to express an approved one. There is no argument to get wrong, no boolean to
-- flip by accident, no injection that produces a spending account. Combined
-- with the absence of an `update` grant on business, the role that serves the
-- internet cannot approve anything, including itself. Approval lives with the
-- owner role, where the rest of the irreversible operations already live.
--
-- Returns NULL when the address is already taken, and the caller must treat
-- that the same as success. A signup form that says "that email is registered"
-- is the address checker that /auth/request was carefully written not to be;
-- adding a second endpoint that answers the same question would undo it.
create function app_business_signup(addr text, biz_name text) returns uuid
language plpgsql volatile security definer set search_path = pg_catalog, public as $fn$
declare
    new_id uuid;
begin
    insert into business (name, owner_email, approved)
    values (trim(biz_name), lower(trim(addr)), false)
    on conflict (owner_email) do nothing
    returning id into new_id;
    return new_id;
end;
$fn$;

grant execute on function app_business_signup(text, text) to talkwisp_app;

-- Deliberately absent: any grant that lets talkwisp_app write this column, and
-- any app_business_approve() function callable by it. Approval is
-- `onboard.py --approve`, which connects as the owner. The gate is only worth
-- having if the thing it protects cannot switch it off.

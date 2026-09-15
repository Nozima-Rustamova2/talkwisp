-- Up to three owners per business, each claimed through the existing signed
-- deep link.
--
-- NOT USERNAMES. A bot cannot message someone by @username and there is no
-- lookup from a username to a chat id; the claim flow is the only way Telegram
-- reveals an id at all. So this changes how many ids we store and nothing about
-- how they are obtained -- the HMAC-signed /start payload from app/channel.py
-- is still the only door, and it is verified by construction.
--
-- A TABLE, NOT THREE COLUMNS. Three columns means slot logic at every call
-- site ("which one is free?") and an awkward cross-column uniqueness check. A
-- table gets `unique (business_id, telegram_id)` for free, which is the
-- property that actually matters: one person cannot hold two slots and quietly
-- consume the cap.
--
-- It is the seventh table and it earns the slot by the stated rule: files hold
-- append-only observations, tables hold mutable state with a lifecycle someone
-- is waiting on. Owners are claimed, and now removed.
create table business_owner (
    business_id uuid not null references business(id),
    telegram_id bigint not null,
    claimed_at  timestamptz not null default now(),
    primary key (business_id, telegram_id)
);

alter table business_owner enable row level security;

create policy tenant_self on business_owner
    using (business_id = app_current_business())
    with check (business_id = app_current_business());

grant select on business_owner to talkwisp_app;


-- BACKFILL BEFORE ANYTHING READS THE NEW TABLE. Every business that has
-- already claimed an owner keeps them, with the original claim time lost but
-- the id intact -- which is the part that gates /fact and the owner buttons. A
-- migration that created an empty table and dropped the old column would sign
-- every live owner out of their own bot.
insert into business_owner (business_id, telegram_id, claimed_at)
select id, owner_telegram_id, created_at
  from business where owner_telegram_id is not null;


-- THE CLAIM, now capped rather than single.
--
-- Still "returns true only if THIS call did the claiming", so the caller can
-- tell a fresh claim from a repeat press without reading anything back. A
-- person who has already claimed gets false rather than an error: pressing the
-- link twice is not a failure, it is a person pressing a link twice.
--
-- THE CAP IS POLICY AND THE UNIQUE KEY IS THE FLOOR. A fourth owner is not a
-- security problem the way free text in a prompt column is, so it is enforced
-- here rather than by a trigger. What the database enforces is the thing that
-- would actually corrupt the model: one person holding two slots.
create or replace function app_business_claim_owner(biz uuid, tg_id bigint)
returns boolean
language plpgsql volatile security definer set search_path = pg_catalog, public as $fn$
declare
    existing int;
begin
    select count(*) into existing from business_owner where business_id = biz;
    if existing >= 3 then
        return false;
    end if;
    insert into business_owner (business_id, telegram_id)
    values (biz, tg_id)
    on conflict (business_id, telegram_id) do nothing;
    return found;
end;
$fn$;


-- REMOVAL, and deliberately not reachable from Telegram.
--
-- The thing being removed IS a Telegram identity. Letting one Telegram identity
-- revoke another means a borrowed or stolen phone can lock out the real owner.
-- The web session is authenticated by owner_email -- the address that created
-- the business and receives the sign-in link -- which is the account's root
-- identity and the right authority for this.
--
-- REMOVING THE LAST OWNER IS ALLOWED. It returns the business to its pre-claim
-- state, which is recoverable by claiming again. A guard against it would
-- create a stuck state rather than prevent one: a business whose only owner
-- lost their phone could never hand ownership anywhere.
create function app_business_remove_owner(biz uuid, tg_id bigint)
returns boolean
language plpgsql volatile security definer set search_path = pg_catalog, public as $fn$
declare
    removed int;
begin
    delete from business_owner
     where business_id = biz and telegram_id = tg_id;
    get diagnostics removed = row_count;
    return removed > 0;
end;
$fn$;


-- Web sign-in by Telegram Login now finds ANY owner, not the first one.
--
-- Left as it was, only the person whose id happened to sit in the old column
-- could sign in, and the second and third owners would be told their account
-- does not exist. CREATE OR REPLACE preserves the grant; DROP would destroy it,
-- which is how 0015 lost the retrievable_fact grant.
create or replace function app_business_for_telegram(tg_id bigint) returns uuid
language sql stable security definer set search_path = pg_catalog, public as $fn$
    select business_id from business_owner where telegram_id = tg_id limit 1
$fn$;

-- The list, for the bot's fan-out and for the Settings screen. Ordered by
-- claim time so "the first owner" means something stable wherever one is shown.
create function app_business_owners(biz uuid) returns setof bigint
language sql stable security definer set search_path = pg_catalog, public as $fn$
    select telegram_id from business_owner
     where business_id = biz order by claimed_at, telegram_id
$fn$;

grant execute on function app_business_claim_owner(uuid, bigint) to talkwisp_app;
grant execute on function app_business_remove_owner(uuid, bigint) to talkwisp_app;
grant execute on function app_business_owners(uuid) to talkwisp_app;


-- ONE SOURCE OF TRUTH. The old column is dropped rather than kept in step,
-- because two places holding "who owns this" is the drift this codebase keeps
-- producing -- and the failure would be silent: the bot reading one and web
-- sign-in reading the other, disagreeing only for the second and third owner.
alter table business drop column owner_telegram_id;


-- WHO resolved a payment, so the second owner to tap is told something useful.
--
-- With one owner "already resolved" is a complete sentence. With three, the
-- useful sentence is "Nigora already confirmed this" -- and the other owners'
-- copies of the message keep their live buttons, because we can only edit the
-- one belonging to the callback we received. So the second tap is the normal
-- case rather than an edge one.
alter table purchase add column resolved_by bigint;

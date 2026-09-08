-- Sessions and magic-link tokens. Two tables, approved.
--
-- Until now every endpoint was open. On a public URL that means DELETE
-- /review/{id}, the whole write surface, GET /review returning the entire
-- queue, and the extract calls that spend model credits -- a cost attack as
-- well as a data one.
--
-- These are PLATFORM tables, like `business`. They answer "who is this" and so
-- must be readable before a tenant is known, which is exactly what the tenancy
-- policies forbid. 0007 broke that chicken-and-egg with narrow SECURITY DEFINER
-- resolvers; this file does the same, and goes one step further:
--
--   THE APP ROLE IS GRANTED NOTHING ON THESE TWO TABLES.
--
-- Not SELECT, not INSERT. Every access goes through the functions at the bottom,
-- each of which does one thing and returns the least it can. So a query bug, an
-- injection, or a future `select * from session` cannot read them at all --
-- permission denied, not an empty result. RLS would give the same answer for
-- reads; no grant also covers writes, and is simpler to verify.
--
-- Both tables store a HASH of the secret, never the secret. A dump of this
-- database yields no usable cookie and no usable link. The raw value exists
-- only in the cookie jar and in the email.

-- One user per business, so the session points straight at the business and
-- there is no user table. "Logged in as this business" is the whole model.
-- unique: two businesses cannot claim one address, or a magic link would be
-- ambiguous about who it logs in.
alter table business add column owner_email text unique;

create table session (
    -- sha256 of the cookie value, hex. Primary key because lookup is by hash
    -- on every single request and there is nothing else to look up by.
    id_hash     text primary key,
    business_id uuid not null references business(id) on delete cascade,
    created_at  timestamptz not null default now(),
    expires_at  timestamptz not null
);

-- Expired rows are deleted lazily on lookup rather than by a sweep, so this
-- index serves the delete rather than a cron job that does not exist.
create index session_expiry_idx on session (expires_at);

create table login_token (
    -- sha256 of the token in the emailed URL, hex.
    token_hash  text primary key,
    business_id uuid not null references business(id) on delete cascade,
    created_at  timestamptz not null default now(),
    expires_at  timestamptz not null,
    -- Set the first time it is claimed. Single use is enforced by the claim
    -- function in one statement, not by read-then-write, because a link
    -- double-clicked in an email client arrives twice within milliseconds.
    used_at     timestamptz
);

-- --------------------------------------------------------- the resolvers --
--
-- All SECURITY DEFINER with a pinned search_path, same as 0007's. Each returns
-- an id or a word, never a row, never a list, never a hash.

-- Which business owns this address. Used only by the login-request path.
create function app_business_for_email(addr text) returns uuid
language sql stable security definer set search_path = pg_catalog, public as $fn$
    select id from business where lower(owner_email) = lower(trim(addr))
$fn$;

create function app_login_token_create(hash text, business uuid, ttl interval)
returns void
language sql security definer set search_path = pg_catalog, public as $fn$
    insert into login_token (token_hash, business_id, expires_at)
    values (hash, business, now() + ttl)
$fn$;

-- Claim a link, once. Returns why it failed rather than just failing, because
-- "this link has already been used" and "this link expired" are different
-- things to a person standing in front of a screen, and a generic error makes
-- both look like a broken product.
--
-- The update and the check are ONE statement. Read-then-write would let a
-- double-clicked link, or a mail client that prefetches URLs, mint two
-- sessions from one token.
create function app_login_token_claim(hash text)
returns table (business_id uuid, outcome text)
language plpgsql security definer set search_path = pg_catalog, public as $fn$
declare
    row_used   timestamptz;
    row_expiry timestamptz;
    row_biz    uuid;
begin
    update login_token t set used_at = now()
     where t.token_hash = hash and t.used_at is null and t.expires_at > now()
     returning t.business_id into row_biz;

    if found then
        return query select row_biz, 'ok'::text;
        return;
    end if;

    select t.used_at, t.expires_at into row_used, row_expiry
      from login_token t where t.token_hash = hash;

    if not found then
        return query select null::uuid, 'unknown'::text;
    elsif row_used is not null then
        return query select null::uuid, 'used'::text;
    else
        return query select null::uuid, 'expired'::text;
    end if;
end
$fn$;

create function app_session_create(hash text, business uuid, ttl interval)
returns void
language sql security definer set search_path = pg_catalog, public as $fn$
    insert into session (id_hash, business_id, expires_at)
    values (hash, business, now() + ttl)
$fn$;

-- The hot path: one lookup per request. Deletes the row when it has expired, so
-- the table cleans itself without a sweep -- and so an expired session cannot
-- come back if a clock moves.
create function app_session_business(hash text) returns uuid
language plpgsql security definer set search_path = pg_catalog, public as $fn$
declare
    result uuid;
    expiry timestamptz;
begin
    select s.business_id, s.expires_at into result, expiry
      from session s where s.id_hash = hash;
    if not found then
        return null;
    end if;
    if expiry <= now() then
        delete from session s where s.id_hash = hash;
        return null;
    end if;
    return result;
end
$fn$;

create function app_session_delete(hash text) returns void
language sql security definer set search_path = pg_catalog, public as $fn$
    delete from session where id_hash = hash
$fn$;

-- Logging in as a Telegram user, for step 4. Separate from the email lookup
-- because they are different identities that happen to reach the same row.
create function app_business_for_telegram(tg_id bigint) returns uuid
language sql stable security definer set search_path = pg_catalog, public as $fn$
    select id from business where owner_telegram_id = tg_id
$fn$;

-- ------------------------------------------------------------- the grants --
--
-- EXECUTE only. Note what is deliberately absent: no grant on `session` or
-- `login_token` themselves. check_auth.py asserts that a direct read is refused
-- rather than empty.
grant execute on function app_business_for_email(text) to talkwisp_app;
grant execute on function app_business_for_telegram(bigint) to talkwisp_app;
grant execute on function app_login_token_create(text, uuid, interval) to talkwisp_app;
grant execute on function app_login_token_claim(text) to talkwisp_app;
grant execute on function app_session_create(text, uuid, interval) to talkwisp_app;
grant execute on function app_session_business(text) to talkwisp_app;
grant execute on function app_session_delete(text) to talkwisp_app;

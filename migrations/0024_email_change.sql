-- Changing the address you sign in with.
--
-- THE THIRD CAPABILITY WITH NO ROUTE, after payment details and the business
-- name, and the only one whose failure is permanent. The sign-in link goes to
-- `owner_email`; lose access to that address and there is no way back in except
-- somebody running SQL.
--
-- The case is narrower than it first looks and worth stating precisely, because
-- it decides what this has to solve:
--
--   A TYPO AT SIGNUP IS NOT THE PROBLEM. owner_email is unique per address, so
--   a mistyped one is a different row -- the person simply signs up again and
--   the bad row is litter, not a lock. Nothing recovers it and nothing needs to.
--
--   LOSING A CORRECT ADDRESS LATER IS THE PROBLEM. An owner who used the
--   product for months and then leaves the company, or closes the mailbox,
--   cannot receive a link. If they claimed the bot in Telegram they can still
--   sign in through /auth/telegram -- and then there is nothing to change the
--   address with. That is the hole this closes.
--
-- VERIFIED AT THE NEW ADDRESS, not the old one. Sending the confirmation to the
-- address being replaced would be useless in exactly the case that matters: the
-- one where it no longer works. Confirming at the destination also catches a
-- second typo, which is the way this feature could otherwise recreate the
-- problem it exists to solve.
create table email_change (
    token_hash  text primary key,
    business_id uuid not null references business(id),
    new_email   text not null,
    created_at  timestamptz not null default now(),
    expires_at  timestamptz not null,
    used_at     timestamptz
);

-- No RLS and no grant, deliberately -- the same treatment as `session` and
-- `login_token`. talkwisp_app reaches this table only through the two functions
-- below, so a bug in the web app cannot read a pending change or mint one for
-- another tenant. check_tenancy asserts the app role cannot touch it directly.


create function app_email_change_create(hash text, business uuid,
                                        addr text, ttl interval)
returns boolean
language plpgsql security definer set search_path = pg_catalog, public as $fn$
begin
    -- Refused up front when the address already belongs to somebody. The
    -- alternative is a link that looks fine, waits in an inbox, and fails on
    -- the click -- a worse place to learn it.
    if exists (select 1 from business
                where lower(owner_email) = lower(btrim(addr))) then
        return false;
    end if;
    insert into email_change (token_hash, business_id, new_email, expires_at)
    values (hash, business, btrim(addr), now() + ttl);
    return true;
end;
$fn$;


-- Claim once, and do the change in the SAME statement that spends the token.
--
-- Read-then-write would let a double-clicked link, or a mail client that
-- prefetches URLs, act twice -- the same reasoning as app_login_token_claim,
-- and the reason that function is shaped the way it is.
--
-- Returns WHY it failed rather than just failing. "Already used", "expired" and
-- "somebody took that address while you were deciding" are three different
-- things to a person in front of a screen, and one generic error makes all
-- three look like broken software.
create function app_email_change_claim(hash text)
returns table (business_id uuid, old_email text, new_email text, outcome text)
language plpgsql security definer set search_path = pg_catalog, public as $fn$
declare
    row_biz   uuid;
    row_addr  text;
    row_old   text;
    row_used  timestamptz;
    row_expiry timestamptz;
begin
    update email_change c set used_at = now()
     where c.token_hash = hash and c.used_at is null and c.expires_at > now()
     returning c.business_id, c.new_email into row_biz, row_addr;

    if found then
        select b.owner_email into row_old from business b where b.id = row_biz;
        begin
            update business set owner_email = row_addr where id = row_biz;
        exception when unique_violation then
            -- Taken between the request and the click. The token is already
            -- spent, which is correct: it was for this address and this
            -- address is gone.
            return query select row_biz, row_old, row_addr, 'taken'::text;
            return;
        end;
        return query select row_biz, row_old, row_addr, 'ok'::text;
        return;
    end if;

    select c.used_at, c.expires_at into row_used, row_expiry
      from email_change c where c.token_hash = hash;
    if row_used is not null then
        return query select null::uuid, null::text, null::text, 'used'::text;
    elsif row_expiry is not null then
        return query select null::uuid, null::text, null::text, 'expired'::text;
    else
        return query select null::uuid, null::text, null::text, 'unknown'::text;
    end if;
end;
$fn$;

grant execute on function app_email_change_create(text, uuid, text, interval)
    to talkwisp_app;
grant execute on function app_email_change_claim(text) to talkwisp_app;

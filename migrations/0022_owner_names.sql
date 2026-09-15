-- An owner's display name, and who resolved a payment.
--
-- WHY A NAME AT ALL. With one owner "already resolved" is a complete sentence.
-- With three the useful sentence is "Nigora already confirmed this" -- and we
-- store a Telegram id, which would make it "444000444 already confirmed this".
-- The id is the identity; the name is what makes a message readable.
--
-- Captured at CLAIM TIME from the /start update's `from` object, which is the
-- same place the customer names now come from. It is a snapshot and will go
-- stale if they rename themselves in Telegram, and that is acceptable: this
-- labels a button press, it does not identify anyone. Refreshing it would mean
-- reading every owner's profile on a schedule for a cosmetic string.
alter table business_owner add column display_name text;


-- THE SIGNATURE CHANGES, so the old function must be DROPPED rather than
-- replaced -- CREATE OR REPLACE cannot alter an argument list, and adding a
-- defaulted third parameter alongside the two-argument version makes the call
-- ambiguous rather than overloaded.
--
-- DROPPING DESTROYS THE GRANT. The recreated function is a new object with no
-- privileges, which is exactly how 0015 lost the retrievable_fact grant and
-- why migrate.py now ends with a smoke test. The grant is re-issued below, and
-- check_owners would fail immediately without it.
drop function app_business_claim_owner(uuid, bigint);

create function app_business_claim_owner(biz uuid, tg_id bigint,
                                         who text default null)
returns boolean
language plpgsql volatile security definer set search_path = pg_catalog, public as $fn$
declare
    existing int;
begin
    select count(*) into existing from business_owner where business_id = biz;
    if existing >= 3 then
        return false;
    end if;
    insert into business_owner (business_id, telegram_id, display_name)
    values (biz, tg_id, nullif(btrim(coalesce(who, '')), ''))
    on conflict (business_id, telegram_id) do nothing;
    return found;
end;
$fn$;

grant execute on function app_business_claim_owner(uuid, bigint, text)
    to talkwisp_app;


-- The list with names, for the fan-out's logging and for the Settings screen.
-- Same ordering as app_business_owners(), so "the first owner" means the same
-- thing wherever it is read.
create function app_business_owner_rows(biz uuid)
returns table (telegram_id bigint, display_name text, claimed_at timestamptz)
language sql stable security definer set search_path = pg_catalog, public as $fn$
    select telegram_id, display_name, claimed_at from business_owner
     where business_id = biz order by claimed_at, telegram_id
$fn$;

grant execute on function app_business_owner_rows(uuid) to talkwisp_app;


-- WHO resolved this payment. Written by the transition that actually moved the
-- row, so it cannot disagree with the state it explains: the second owner to
-- tap loses the conditional UPDATE and therefore never writes here.
comment on column purchase.resolved_by is
    'Telegram id of the owner whose Confirm or Reject moved this row. Set by '
    'the same UPDATE that set the state, so it names the tap that won.';

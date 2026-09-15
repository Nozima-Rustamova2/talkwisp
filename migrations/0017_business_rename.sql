-- An owner can change their business name, and an ambiguous name stops
-- resolving silently.
--
-- WHY THIS WAS MISSING AND WHY IT MATTERED. A business name is typed once at
-- signup, never shown back, and until now could not be changed by anyone but a
-- person with admin SQL. A real self-serve business signed up on 12 September
-- and typed "Klinika" -- a placeholder, and wrong: they sell online English
-- courses. That string is what their customers are greeted with, what the
-- agent calls itself when asked who it is, and what every escalation to the
-- owner is headed with.
--
-- It surfaced only because a separate bug was fixed. The /start greeting had
-- never called .format(), so it read "Salom! {name} haqida..." -- the
-- placeholder, literally. That is obviously broken. "Salom! Klinika haqida..."
-- is not obviously anything: it renders as if it works. Fixing the visible bug
-- revealed the silent one it had been masking.
--
-- This is the SECOND capability found with no route, after payment details.
-- The systematic question -- what can a business row hold that no owner can
-- change? -- also turns up owner_email, which is the sign-in address and needs
-- a verification flow rather than a text field, so it is deliberately not here.

-- SECURITY DEFINER, and the reason is the property being protected. talkwisp_app
-- has SELECT on business and nothing else (0007); that absence is what stops a
-- bug in the web app from rewriting a tenant. So every owner-facing write to
-- this table goes through one narrow function that can only do one thing --
-- exactly as app_business_set_bot_token and app_business_claim_owner already do.
--
-- It takes the id rather than finding the business itself, because the caller
-- is inside connection() and already has it. A function that resolved the
-- tenant would be a second answer to "which business is this?", and the two
-- would disagree the day one of them changed.
create function app_business_rename(biz uuid, new_name text) returns text
language plpgsql volatile security definer set search_path = pg_catalog, public as $fn$
declare
    cleaned text := btrim(new_name);
begin
    -- Trimmed, then checked. " " is not a name, and a name with leading spaces
    -- is one that looks right everywhere and sorts wrong.
    if cleaned = '' then
        raise exception 'A business name cannot be empty.';
    end if;
    if length(cleaned) > 80 then
        raise exception 'A business name cannot be longer than 80 characters.';
    end if;
    update business set name = cleaned where id = biz;
    return cleaned;
end
$fn$;

grant execute on function app_business_rename(uuid, text) to talkwisp_app;


-- AND THE HAZARD THE RENAME WOULD OTHERWISE CREATE.
--
-- business.name is NOT unique -- bot_token and owner_email are, name is not --
-- and app_business_by_name() was `select id from business where name = wanted`
-- returning a scalar. With two matches Postgres returns the first row and says
-- nothing. `bot.py --business "Klinika"` would then start a poller for whichever
-- row came first, against another tenant's facts, and nothing would fail.
--
-- Today there are no duplicates, so it is latent. Letting owners choose their
-- own names makes it reachable by an ordinary action: "Klinika" is precisely
-- the generic string two different owners would both type.
--
-- The fix is NOT a unique constraint. A display name is not an identifier, and
-- forcing global uniqueness would mean the second clinic called "Klinika" is
-- told to pick another name for reasons that are ours, not theirs. The fix is
-- for the resolver to refuse rather than guess: an ambiguous name is a question
-- the caller has to answer, and the id is always available.
--
-- CREATE OR REPLACE, deliberately, not DROP then CREATE. Replacing a function
-- preserves its grants; dropping one destroys them, and the recreated object is
-- a new one with no privileges. Migration 0015 destroyed the retrievable_fact
-- grant exactly that way and it was caught by probing rather than by the green
-- migration -- which is why migrate.py now ends with a smoke test.
create or replace function app_business_by_name(wanted text) returns uuid
language plpgsql stable security definer set search_path = pg_catalog, public as $fn$
declare
    found uuid;
    matches int;
begin
    select count(*) into matches from business where name = wanted;
    if matches > 1 then
        raise exception
            'Business name % is ambiguous: % businesses share it. Pass the id instead.',
            wanted, matches;
    end if;
    select id into found from business where name = wanted;
    return found;   -- NULL when nothing matches, which callers already handle
end
$fn$;

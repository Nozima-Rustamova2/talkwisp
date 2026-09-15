-- What the agent is called and how it sounds.
--
-- THE THIRD THING IN A FAMILY: what the business is called (0017), what the
-- agent is called, how it speaks. All three were typed once or never, and none
-- could be changed by the person who owns them.
--
-- ONLY ONE OF THESE EVER REACHES A MODEL. agent_name is substituted into the
-- greeting and into the meta-request identity answer, both of which are canned
-- strings assembled in code -- no prompt, no generation, the same treatment
-- business name already gets. greeting_* are canned strings by definition. Only
-- the three tone columns become prompt text, and each maps to a sentence WE
-- wrote, chosen by an enum.
--
-- WHY TONE IS AN ENUM AND NOT A TEXT BOX. A free-text persona field lives in
-- the same prompt as the rules, and an owner writing something entirely
-- reasonable erodes a guarantee without knowing:
--
--     "always try to be helpful"       weakens the refusal
--     "you know everything about us"   invites invention
--     "answer in Russian"              overrides matching the customer
--     "never say you don't know"       removes the product's core claim
--
-- None of those is malicious; they are what a normal person types when asked to
-- describe their assistant. And the failure is invisible -- a slightly more
-- confident answer, not an error. Fixed options are also the only version that
-- can be tested: the harness can run every combination, and cannot run
-- arbitrary text.
--
-- THE CHECK CONSTRAINTS ARE THE POINT, not decoration. The endpoint validates
-- too, but a bug there would otherwise put free text into the one column that
-- reaches a prompt. The database is the floor under that, the same way
-- fact_payment_not_embedded is the floor under the payment exclusion.

alter table business
    add column agent_name     text,
    -- NULL means "not set", and not-set is what keeps the prompt byte-identical
    -- to today's for every business that never opens this screen. An empty
    -- string would be a second way to mean the same thing, and two ways to be
    -- unset is how a screen starts disagreeing with the bot.
    add column tone_register  text check (tone_register in ('formal', 'informal')),
    -- NO 'detailed'. Rule 7 asks for one or two sentences and is load-bearing:
    -- the doctors-ru case showed the model summarising twelve doctors down to
    -- five as context grew. 'normal' IS rule 7 and adds nothing to the prompt;
    -- 'concise' is stricter. The direction that caused that failure is not
    -- reachable through this control at all.
    add column tone_length    text check (tone_length in ('normal', 'concise')),
    -- 'off' adds NOTHING to the prompt rather than saying "never use emoji".
    -- That preserves byte-identity for the default, which is worth more than
    -- true suppression -- and if suppression is ever wanted it is a third
    -- option rather than a change to what everyone already has.
    add column tone_emoji     text check (tone_emoji in ('off', 'light')),
    -- The first message, per language, falling back to the built-in when NULL.
    -- Canned text: it is sent instead of a model call, never alongside one.
    add column greeting_uz_latn text,
    add column greeting_uz_cyrl text,
    add column greeting_ru      text;

-- Length caps, because these are shown to customers and a pathological value is
-- a broken screen rather than a security problem -- but a broken screen in front
-- of a stranger is still the worst place to find out.
alter table business
    add constraint business_agent_name_length
        check (agent_name is null or length(agent_name) between 1 and 40),
    add constraint business_greeting_length check (
        (greeting_uz_latn is null or length(greeting_uz_latn) between 1 and 400)
        and (greeting_uz_cyrl is null or length(greeting_uz_cyrl) between 1 and 400)
        and (greeting_ru is null or length(greeting_ru) between 1 and 400));


-- ONE WRITER, for the reason every owner-facing write to this table has one:
-- talkwisp_app has SELECT on business and nothing else (0007), and that absence
-- is what stops a bug in the web app rewriting a tenant. Same narrow shape as
-- app_business_set_bot_token and app_business_rename.
--
-- Every argument is nullable and NULL means "clear it", so the screen can
-- unset a field without a second function. Blank strings are normalised to
-- NULL here rather than in the endpoint, so the not-set state has exactly one
-- representation no matter who calls.
create function app_business_set_style(
        biz uuid,
        new_agent_name text,
        new_register text,
        new_length text,
        new_emoji text,
        new_greeting_uz_latn text,
        new_greeting_uz_cyrl text,
        new_greeting_ru text) returns void
language sql volatile security definer set search_path = pg_catalog, public as $fn$
    update business set
        agent_name       = nullif(btrim(coalesce(new_agent_name, '')), ''),
        tone_register    = nullif(btrim(coalesce(new_register, '')), ''),
        tone_length      = nullif(btrim(coalesce(new_length, '')), ''),
        tone_emoji       = nullif(btrim(coalesce(new_emoji, '')), ''),
        greeting_uz_latn = nullif(btrim(coalesce(new_greeting_uz_latn, '')), ''),
        greeting_uz_cyrl = nullif(btrim(coalesce(new_greeting_uz_cyrl, '')), ''),
        greeting_ru      = nullif(btrim(coalesce(new_greeting_ru, '')), '')
    where id = biz
$fn$;

grant execute on function app_business_set_style(
    uuid, text, text, text, text, text, text, text) to talkwisp_app;

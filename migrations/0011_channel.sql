-- Let an owner connect their own Telegram bot, from a browser.
--
-- Until now a bot token went in through onboard.py, over SSH, by me. That is
-- the last thing in the product that a customer cannot do for themselves, and
-- it is the difference between "sign up and use it" and "sign up and wait for
-- Nozima to be at a laptop".
--
-- TWO FUNCTIONS, BOTH SECURITY DEFINER, for the same reason as 0008's and
-- 0009's: `business` is granted SELECT and nothing else to talkwisp_app
-- (0007), which is what stops a bug in the web app renaming a tenant or
-- reading another's bot_token. These are the two narrow holes, and each does
-- one thing.

-- ------------------------------------------------------------ the bot token --
--
-- Returns the previous username-less state so the caller can tell "connected"
-- from "replaced", and 'taken' when another business already holds the token.
--
-- THE COLLISION CHECK IS HERE, NOT IN PYTHON, because bot_token is UNIQUE and
-- a bare violation would surface as a 500 with a constraint name in it. Worse,
-- checking in Python means SELECT-then-INSERT, and two owners pasting the same
-- token at once both pass the check. One statement, one answer.
--
-- It deliberately does NOT say WHICH business holds it. That would turn the
-- endpoint into an oracle: paste a token, learn whether a competitor is a
-- Talkwisp customer. onboard.py names the colliding business because it runs
-- as the owner on the box; this one cannot.
create function app_business_set_bot_token(biz uuid, token text)
returns text
language plpgsql volatile security definer set search_path = pg_catalog, public as $fn$
declare
    holder uuid;
begin
    select id into holder from business
     where bot_token = trim(token) and id <> biz;
    if holder is not null then
        return 'taken';
    end if;
    update business set bot_token = trim(token) where id = biz;
    if not found then
        return 'no_such_business';
    end if;
    return 'ok';
end;
$fn$;

-- --------------------------------------------------------------- the owner --
--
-- Who may press the owner buttons and use /fact in Telegram. It cannot come
-- from the token -- Telegram will not tell you a user id from a bot token --
-- so the owner has to message their own bot, and the bot has to recognise them.
--
-- ONLY FROM NULL. `where owner_telegram_id is null` is the whole safety
-- property: the first press wins and every later one is refused, so a claim
-- link that leaks after the fact is worthless. Combined with the signed code
-- app/channel.py puts in the /start payload, an ordinary customer who finds the
-- bot first cannot become its owner by pressing Start.
--
-- Returns true only if THIS call did the claiming.
create function app_business_claim_owner(biz uuid, tg_id bigint)
returns boolean
language plpgsql volatile security definer set search_path = pg_catalog, public as $fn$
declare
    claimed boolean := false;
begin
    update business set owner_telegram_id = tg_id
     where id = biz and owner_telegram_id is null;
    get diagnostics claimed = row_count;
    return claimed;
end;
$fn$;

grant execute on function app_business_set_bot_token(uuid, text) to talkwisp_app;
grant execute on function app_business_claim_owner(uuid, bigint) to talkwisp_app;

-- Deliberately absent: anything that READS bot_token across businesses. The
-- bot reads its own through the tenant_self policy, which is exactly as wide
-- as it needs to be.

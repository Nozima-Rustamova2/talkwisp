-- When a poller was last alive for a business.
--
-- WHY A COLUMN AND NOT AN ASSUMPTION. The Settings screen offered "open your
-- bot and press Start" the moment a token was saved -- and by that screen's own
-- stated policy the poller is NOT running yet at that moment, because we start
-- each one by hand. So the one step the owner was told to take was the one step
-- that could not work: Telegram queued the update, nothing read it, and the
-- "I've done it" button correctly reported that nothing had changed.
--
-- The screen was asserting something it could not check. This is the column
-- that lets it observe instead. Same move as everything else that has held in
-- this project: replace a guess with a measurement.
--
-- TOUCHED PERIODICALLY, NOT ONCE AT STARTUP. A value written only at boot goes
-- stale the moment the process dies, and a crashed poller would read "seen at
-- 09:14" forever -- which is a status plate that is confidently wrong, the
-- worst kind. The poll loop already wakes at least every POLL_TIMEOUT seconds,
-- so it costs one cheap UPDATE a minute to make this mean "alive now" rather
-- than "started once".
--
-- It is also the column a Dashboard needs for "is my agent on", so this is not
-- work that exists only for the Settings screen.
alter table business add column bot_last_seen_at timestamptz;

-- SECURITY DEFINER for the same reason as every other writer of this table:
-- talkwisp_app has SELECT on business and nothing else (0007), which is what
-- stops a bug in the web app rewriting a tenant. The bot runs as that role, so
-- it needs exactly this one narrow hole and no other.
--
-- It writes now() rather than taking a timestamp, so a caller cannot claim to
-- have been alive at a time it chooses.
create function app_business_touch_bot(biz uuid) returns void
language sql volatile security definer set search_path = pg_catalog, public as $fn$
    update business set bot_last_seen_at = now() where id = biz
$fn$;

grant execute on function app_business_touch_bot(uuid) to talkwisp_app;

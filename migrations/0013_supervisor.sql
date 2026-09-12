-- Which businesses should have a Telegram poller running.
--
-- THE ONE PLACE THE TENANCY MODEL IS BENT, and it is bent by exactly one
-- function. The supervisor has to see across tenants -- that is its whole job --
-- which is precisely what RLS forbids. The alternative was to run it on
-- ADMIN_DATABASE_URL, and that would undo the service-account hardening from
-- the other side: the process that spawns every bot would bypass RLS entirely
-- and could read every tenant's bot_token with a plain select.
--
-- So, the same shape as every other cross-tenant question here (0007's
-- app_business_for_token, 0008's app_business_for_email, 0009's
-- app_business_names): SECURITY DEFINER, pinned search_path, and it answers ONE
-- question rather than granting a table.
--
-- IT RETURNS NO TOKENS. Ids and names only. The child process reads its own
-- token once it is bound, through the tenant_self policy, which is exactly as
-- wide as it needs to be. The supervisor never holds a credential for a
-- business it is merely starting.
--
-- `approved` IS PART OF THE QUESTION, not a filter applied afterwards in
-- Python. A poller for an unapproved business would accept Telegram messages
-- and then fail at the model call -- a customer gets a broken-sounding apology
-- instead of silence, which is the worse of the two failures. Putting it in the
-- WHERE clause means no caller can forget it.
create function app_businesses_to_poll()
returns table (id uuid, name text)
language sql stable security definer set search_path = pg_catalog, public as $fn$
    select id, name from business
     where bot_token is not null and approved
     order by name
$fn$;

grant execute on function app_businesses_to_poll() to talkwisp_app;

-- Deliberately NOT here: a function that returns a token, or one that starts
-- anything. The supervisor's power is "know which ids to spawn", and nothing
-- about this file grants more than that.

-- Resolve a business by NAME, so scripts can say which tenant they mean.
--
-- The nine scripts that load or measure data called connection() with no
-- argument, which fell back to "the only business there is". That worked while
-- there was one and broke the moment there were two -- including seed.py, so a
-- second tenant could be created and then never given any knowledge.
--
-- The fallback itself is the deeper problem. It is the same shape as the
-- pre-auth fallback that would have served a logged-out visitor another
-- business's data: correct-looking for exactly as long as there is one row.
-- That one was closed on the web side in 0008 and left open in the script path.
-- With these two functions every caller names its tenant and the default can be
-- deleted, so there is no implicit tenant anywhere.
--
-- SECURITY DEFINER for the same reason as 0007's and 0008's resolvers: `business`
-- has RLS with a policy of `id = app_current_business()`, so a tenant cannot be
-- looked up by a connection that has no tenant yet. These are the narrow,
-- deliberate holes in that fence -- each returns a name or an id, never a row,
-- never a token.

create function app_business_by_name(wanted text) returns uuid
language sql stable security definer set search_path = pg_catalog, public as $fn$
    select id from business where name = wanted
$fn$;

-- For the error message. A script that cannot find its dataset should say what
-- does exist rather than just failing -- the alternative is someone guessing at
-- names against a database they cannot select from.
create function app_business_names() returns setof text
language sql stable security definer set search_path = pg_catalog, public as $fn$
    select name from business order by name
$fn$;

grant execute on function app_business_by_name(text) to talkwisp_app;
grant execute on function app_business_names() to talkwisp_app;

-- app_sole_business() is deliberately NOT dropped. It is what makes a second
-- business impossible to create by accident while anything still depends on
-- there being one, and check_tenancy.py still asserts on its refusal. Nothing in
-- app/ calls it after this migration; if that stays true for a release it can go.

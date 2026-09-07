-- business_id on everything, enforced BELOW the query layer.
--
-- The failure this prevents: a query missing its filter returns another
-- business's data. One clinic's price answering another clinic's customer,
-- silently, with nothing raising and nothing to look at. Forty-odd query sites
-- and a rule that everyone remembers the WHERE clause is discipline, and
-- discipline has no failure signal -- the same argument that turned the payment
-- exclusion into a view plus a check constraint in 0005.
--
-- So the filter lives here, in row-level security, and a forgotten WHERE clause
-- returns that tenant's rows rather than everyone's.
--
-- THREE WAYS RLS SILENTLY DOES NOTHING, all of which applied to this database
-- before this migration, and none of which raise:
--
--   1. A SUPERUSER bypasses RLS entirely. It is not overridable -- not by
--      FORCE, not by any policy. The app connected as `postgres` (measured:
--      usesuper = true), so `enable row level security` on all five tables
--      would have applied and changed nothing.
--   2. A table's OWNER bypasses its own RLS unless FORCE ROW LEVEL SECURITY is
--      also set. All five tables were owned by the connecting role.
--   3. A plain VIEW evaluates RLS as the VIEW'S OWNER, not the caller. That is
--      `retrievable_fact` -- the view every retrieval path reads. Postgres 15+
--      needs WITH (security_invoker = true) for the caller's policies to apply.
--
-- All three are addressed below. The first is addressed OUTSIDE this file as
-- well, by app/db.py refusing to start on a superuser connection, because a SQL
-- migration cannot stop someone pointing DATABASE_URL back at postgres later.

-- ------------------------------------------------------- the business table --
--
-- PLATFORM data, not tenant data. It is the table that answers "which tenant is
-- this", so it cannot itself require a tenant to be known -- that is a
-- chicken-and-egg, and the two resolver functions at the bottom of this file
-- are how it is broken.
--
-- bot_token lives HERE and never in `fact`. Same reasoning as the payment
-- exclusion in 0005: a fact is retrievable, a retrieved fact goes into the
-- answering prompt, and a model that can paraphrase a card number can
-- paraphrase a bot token. Credentials do not go in the knowledge model.
create table business (
    id                uuid primary key default uuidv7(),
    name              text not null,
    -- The business's OWN agent bot, created by them in BotFather. Not
    -- Talkwisp's platform bot: that one signs web logins and must never be a
    -- customer's. Unique because one token addresses exactly one bot, and two
    -- businesses claiming it would make an incoming update ambiguous.
    bot_token         text unique,
    -- Who may tap the owner buttons for THIS business. Replaces the single
    -- TELEGRAM_OWNER_ID env var, which could only ever describe one tenant.
    owner_telegram_id bigint,
    created_at        timestamptz not null default now()
);

-- One row, so the existing install keeps working. Rename it; migrate.py links
-- the bot token and owner id from .env on the next run.
insert into business (name) values ('Default business');

-- ------------------------------------------------------- the tenant column --
--
-- The DEFAULT is what makes writes safe without touching the insert
-- statements. Every existing INSERT in app/ omits business_id, so every one of
-- them now gets the connection's tenant automatically, and the WITH CHECK
-- clauses below make it impossible to name a different one on purpose.
--
-- Added nullable, backfilled, then made not-null: the column cannot be born
-- not-null on a table that already has rows.
alter table source   add column business_id uuid references business(id);
alter table fact     add column business_id uuid references business(id);
alter table alias    add column business_id uuid references business(id);
alter table chunk    add column business_id uuid references business(id);
alter table purchase add column business_id uuid references business(id);

update source   set business_id = (select id from business);
update fact     set business_id = (select id from business);
update alias    set business_id = (select id from business);
update chunk    set business_id = (select id from business);
update purchase set business_id = (select id from business);

alter table source   alter column business_id set not null;
alter table fact     alter column business_id set not null;
alter table alias    alter column business_id set not null;
alter table chunk    alter column business_id set not null;
alter table purchase alter column business_id set not null;

-- ------------------------------------------------------- reading the tenant --
--
-- The one-argument current_setting() raises on a parameter that was never set,
-- but only the FIRST time: once any SET has defined a custom GUC, the session
-- keeps it defined with an empty-string reset value. So after a SET LOCAL ends,
-- an untenanted query would get '' and fail with "invalid input syntax for type
-- uuid" -- loud, but confusing, and it reads like a data bug rather than a
-- missing tenant.
--
-- This function makes both cases say the same clear thing. STABLE, so the
-- planner may evaluate it once per query and still use an index on
-- business_id; not IMMUTABLE, because its value genuinely changes per session.
create function app_current_business() returns uuid
language plpgsql stable as $fn$
declare
    raw text := current_setting('app.business_id', true);
begin
    if raw is null or raw = '' then
        raise exception 'app.business_id is not set: this connection has no tenant. Use app.db.connection(business_id), never pool.connection().';
    end if;
    return raw::uuid;
end
$fn$;

alter table source   alter column business_id set default app_current_business();
alter table fact     alter column business_id set default app_current_business();
alter table alias    alter column business_id set default app_current_business();
alter table chunk    alter column business_id set default app_current_business();
alter table purchase alter column business_id set default app_current_business();

-- ------------------------------------- UNIQUE CONSTRAINTS ARE OUTSIDE RLS --
--
-- READ THIS BEFORE ADDING A TABLE.
--
-- Row-level security filters what a query SEES. A unique index checks what
-- EXISTS, across every tenant, and no policy narrows it. RLS will not catch a
-- missed one and nothing will say so -- this comment is the only warning the
-- next table gets.
--
-- Two consequences, both real:
--   * A write fails for a reason the writer cannot see, because the colliding
--     row belongs to a tenant they cannot read.
--   * The error itself is an inference channel: a uniqueness violation tells
--     you a value exists somewhere you have no access to.
--
-- Every unique constraint on a tenant table must therefore lead with
-- business_id. There were two, and one of them was load-bearing.

-- Two clinics may each have a "Rasulova". Global uniqueness made the second
-- one unwritable.
alter table alias drop constraint alias_alias_key_subject_key_key;
alter table alias add constraint alias_business_alias_subject_key
    unique (business_id, alias_key, subject_key);

-- The urgent one. The unique-amount trick exists so the seller can tell two
-- payments apart -- against ONE card. Two businesses have two cards, so the
-- amount only needs to be unique within a business. Left global, business B's
-- open order silently blocks business A from creating theirs, and the failure
-- surfaces as an unexplained "could not create order" for a real customer.
drop index purchase_open_amount_idx;
create unique index purchase_open_amount_idx
    on purchase (business_id, amount)
    where state in ('awaiting_payment', 'awaiting_owner');

-- chunk's unique (source_id, ordinal) needs nothing: source_id is already
-- scoped by its own row, so the pair cannot span two businesses.

-- ------------------------------------------------------------- the indexes --
--
-- Every one of these now answers a question that starts "for this business".
-- A leading business_id serves both the old query and the new filter; a
-- trailing one would not.
drop index fact_subject_attribute_idx;
create index fact_subject_attribute_idx
    on fact (business_id, subject_key, attribute_key);

drop index alias_key_idx;
create index alias_key_idx on alias (business_id, alias_key);

drop index fact_unconfirmed_idx;
create index fact_unconfirmed_idx
    on fact (business_id, created_at) where not confirmed;

drop index fact_value_key_idx;
create index fact_value_key_idx on fact (business_id, value_key)
    where value_key is not null and length(value_key) <= 40;

drop index purchase_due_idx;
create index purchase_due_idx on purchase (business_id, expires_at)
    where state in ('awaiting_payment', 'awaiting_owner');

drop index purchase_chat_idx;
create index purchase_chat_idx
    on purchase (business_id, chat_id, created_at desc);

-- ------------------------------------------------------------- the view --
--
-- Recreated for two separate reasons, either of which alone would require it:
--
--   1. `select *` froze the column list in 0005, so the view cannot see
--      business_id until it is rebuilt. Its own comment predicted this: "a
--      column added to `fact` later will not appear here ... a missing column
--      is an error, a missing WHERE clause is a leaked card number."
--   2. security_invoker. Without it the view runs RLS as ITS owner, and the
--      view's owner is not the app role, so every retrieval path would have
--      read every tenant's facts through a view that looked correctly filtered.
--
-- Note what is NOT here: any mention of business_id. The payment exclusion
-- needs no tenancy logic, because 'tolov malumotlari' is a Talkwisp convention
-- with the same normalized spelling for every business, and RLS on `fact`
-- scopes the view for free. Two mechanisms below app/ composing without either
-- knowing about the other is the argument for building tenancy this way.
drop view retrievable_fact;
create view retrievable_fact with (security_invoker = true) as
    select * from fact where subject_key <> 'tolov malumotlari';

-- ------------------------------------------------------------- the policies --
--
-- FORCE, not just ENABLE. Without FORCE the table's owner is exempt from its
-- own policies, which would make this whole file decorative the moment anyone
-- ran the app as the role that owns the tables.
--
-- One policy per table covering all four commands. USING filters what is read
-- and what may be updated or deleted; WITH CHECK filters what may be written,
-- so a row cannot be inserted into, or moved into, another tenant.
alter table source   enable row level security;
alter table fact     enable row level security;
alter table alias    enable row level security;
alter table chunk    enable row level security;
alter table purchase enable row level security;
alter table business enable row level security;

alter table source   force row level security;
alter table fact     force row level security;
alter table alias    force row level security;
alter table chunk    force row level security;
alter table purchase force row level security;
alter table business force row level security;

create policy tenant_isolation on source
    using (business_id = app_current_business())
    with check (business_id = app_current_business());
create policy tenant_isolation on fact
    using (business_id = app_current_business())
    with check (business_id = app_current_business());
create policy tenant_isolation on alias
    using (business_id = app_current_business())
    with check (business_id = app_current_business());
create policy tenant_isolation on chunk
    using (business_id = app_current_business())
    with check (business_id = app_current_business());
create policy tenant_isolation on purchase
    using (business_id = app_current_business())
    with check (business_id = app_current_business());

-- The business table sees only ITSELF. Read-only to the app: a tenant renaming
-- itself is a feature that does not exist yet, and until it does, no app query
-- has any reason to write here. Notably this also means no app-role query can
-- enumerate businesses or read another business's bot_token.
create policy tenant_self on business
    for select using (id = app_current_business());

-- --------------------------------------------------------- the resolvers --
--
-- Two questions that must be answered BEFORE a tenant is known, which is
-- exactly what the policy above forbids. SECURITY DEFINER, so they run as the
-- owner and see every row -- and each returns only an id, never a token, never
-- a list. They are the entire hole in the fence, and they are this narrow on
-- purpose.
--
-- search_path is pinned: a SECURITY DEFINER function with a caller-controlled
-- search_path is a privilege-escalation hole.

-- The bot's own question: "an update arrived on my token -- whose bot am I?"
-- This is the real multi-tenant path, and it replaces reading a single
-- TELEGRAM_BOT_TOKEN env var and assuming.
create function app_business_for_token(token text) returns uuid
language sql stable security definer set search_path = pg_catalog, public as $fn$
    select id from business where bot_token = token
$fn$;

-- The pre-auth stand-in. There is no login yet, so the API's "which business is
-- this request for" has exactly one honest answer: the only one there is.
-- Raises rather than picking when that stops being true, which is what makes
-- deploying auth-before-tenancy fail loudly instead of merging two businesses
-- into one silently.
create function app_sole_business() returns uuid
language plpgsql stable security definer set search_path = pg_catalog, public as $fn$
declare
    n int;
    result uuid;
begin
    select count(*) into n from business;
    if n <> 1 then
        raise exception 'app_sole_business(): % businesses exist. There is no login yet, so the request cannot be attributed to one. Build auth before creating a second business.', n;
    end if;
    select id into result from business;
    return result;
end
$fn$;

-- ------------------------------------------------------------- the grants --
--
-- The app role owns nothing, so it cannot drop a policy, disable RLS, or alter
-- a table. It gets DML and the two resolvers, and that is all.
--
-- The role itself is created by migrate.py rather than here, because creating a
-- login role means handling a password and a .sql file has no way to read one.
-- This file only grants, and fails loudly if the role is absent.
grant usage on schema public to talkwisp_app;
grant select, insert, update, delete
    on source, fact, alias, chunk, purchase to talkwisp_app;
grant select on retrievable_fact to talkwisp_app;
grant select on business to talkwisp_app;
grant select on schema_migrations to talkwisp_app;
grant execute on function app_current_business() to talkwisp_app;
grant execute on function app_business_for_token(text) to talkwisp_app;
grant execute on function app_sole_business() to talkwisp_app;

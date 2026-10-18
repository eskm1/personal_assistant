-- Content plan: every post has a job, its kind (Know-how, Ideas, On site, Finished homes,
-- Work with us), and the month has a mix Bryan sets (how many of each kind). The planner
-- shows how this month is tracking ("This month" strip); Claude's prompts learn the kinds,
-- this month's gaps and the offers (the only things a post may offer: Studio never invents
-- a price or promotion). Posts not tied to a client project live in a standing "Topics"
-- project, like "Everyday posts" for loose footage.

-- The kind of post. Claude sets it on the ideas it writes; the Studio machine fills in the
-- rest (only where it's still empty, so it never overwrites someone's choice); anyone who
-- can edit ideas can change it.
alter table public.ideas add column post_type text
  check (post_type in ('knowhow', 'ideas', 'onsite', 'finished', 'offer'));

-- Carousels (slides instead of a Reel) come in a later update; the format is allowed now.
alter table public.ideas drop constraint ideas_format_check;
alter table public.ideas add constraint ideas_format_check
  check (format in ('talking_head', 'day_in_the_life', 'compilation', 'carousel'));

-- Job kinds for later updates ('carousel': the Studio machine lays out a carousel;
-- 'topics': weekly suggested topics), declared once here. Nobody may queue them yet.
alter table public.jobs drop constraint jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
  check (kind in ('organise', 'develop', 'music', 'make', 'style', 'place', 'ideas', 'carousel', 'topics'));

-- Standing projects Studio keeps for posts that aren't one renovation job: 'everyday'
-- (loose footage, footage_ideas.py) and 'topics' (Know-how and Ideas posts). One of each.
alter table public.projects add column standing text unique check (standing in ('everyday', 'topics'));

update public.projects set standing = 'everyday'
  where id = (select id from public.projects
              where lower(name) = 'everyday posts' or lower(folder) = 'everyday posts'
              order by status <> 'active', id limit 1);

update public.projects set standing = 'topics'
  where id = (select id from public.projects
              where lower(name) = 'topics' and standing is null
              order by status <> 'active', id limit 1);

insert into public.projects (name, brand, folder, brief, standing, created_by)
  select 'Topics', 'urban_makers', 'Topics',
         'Urban Makers posts that aren''t about one client''s renovation: practical know-how (HDB rules, '
         || 'materials that last in Singapore, what things really involve), design ideas (layouts, zoning, '
         || 'light, colour), and how to work with us.',
         'topics', null
  where not exists (select 1 from public.projects where standing = 'topics');

-- ── The plan for the month (one row) ──────────────────────────────────────────

-- mix: monthly targets per kind; offers: [{name, details, ask}], the only offers a post
-- (or any call to action) may make.
create function public.valid_mix(m jsonb) returns boolean
language sql immutable as $$
  select jsonb_typeof(m) = 'object' and not exists (
    select 1 from jsonb_each(m) e
    where e.key not in ('knowhow', 'ideas', 'onsite', 'finished', 'offer')
       or case when jsonb_typeof(e.value) <> 'number' then true
               else (e.value)::numeric not between 0 and 60 or (e.value)::numeric <> trunc((e.value)::numeric) end)
$$;

create function public.valid_offers(o jsonb) returns boolean
language sql immutable as $$
  select jsonb_typeof(o) = 'array' and jsonb_array_length(o) <= 10 and not exists (
    select 1 from jsonb_array_elements(o) x
    where jsonb_typeof(x) <> 'object'
       or jsonb_typeof(x -> 'name') is distinct from 'string'
       or length(trim(x ->> 'name')) not between 1 and 80
       or coalesce(jsonb_typeof(x -> 'details'), 'string') <> 'string'
       or length(coalesce(x ->> 'details', '')) > 500
       or coalesce(jsonb_typeof(x -> 'ask'), 'string') <> 'string'
       or length(coalesce(x ->> 'ask', '')) > 200)
$$;

create table public.content_plan (
  id             smallint primary key default 1 check (id = 1),
  posts_per_week int not null default 3 check (posts_per_week between 0 and 21),
  mix            jsonb not null default '{"knowhow": 4, "ideas": 2, "onsite": 3, "finished": 2, "offer": 2}'::jsonb
                 check (public.valid_mix(mix)),
  offers         jsonb not null default '[
    {"name": "Free consultation", "details": "", "ask": "Book a free consultation"},
    {"name": "Site visit", "details": "", "ask": "Book a site visit"},
    {"name": "Floor plan review", "details": "", "ask": "Send us your floor plan"}
  ]'::jsonb check (public.valid_offers(offers)),
  updated_by     uuid references public.profiles (id) on delete set null,
  updated_at     timestamptz not null default now()
);
insert into public.content_plan (id) values (1);

create function public.stamp_content_plan() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  new.updated_at := now();
  new.updated_by := coalesce(auth.uid(), new.updated_by);
  return new;
end $$;
create trigger content_plan_stamp before update on public.content_plan
  for each row execute function public.stamp_content_plan();

-- ── How this month is tracking ────────────────────────────────────────────────

-- A timestamp from text the Studio machine wrote (ideas.production.updated_at), or null.
create function public.try_timestamptz(t text) returns timestamptz
language plpgsql stable as $$
begin
  return t::timestamptz;
exception when others then
  return null;
end $$;

-- This month (Singapore time) against the mix. A post counts for the month when it's on
-- its way (Go, or in the edit, and not dismissed, rejected or already approved; archived
-- projects' plans don't count) or was approved this month ("ready": Studio never posts,
-- Bryan does). Returns {month, starts_on, ends_on, posts_per_week, target, planned, ready,
-- untyped, kinds: {<kind>: {target, planned, ready, short, ids}}}. Runs with the caller's
-- rights, so a member sees exactly what they can read.
create function public.content_month(as_of timestamptz default now()) returns jsonb
language sql stable set search_path = public as $$
  with m as (
    select date_trunc('month', as_of at time zone 'Asia/Singapore') as starts
  ), bounds as (
    select starts, starts + interval '1 month' as ends,
           starts at time zone 'Asia/Singapore' as starts_tz,
           (starts + interval '1 month') at time zone 'Asia/Singapore' as ends_tz
    from m
  ), plan as (
    select coalesce((select posts_per_week from content_plan where id = 1), 3) as per_week,
           coalesce((select mix from content_plan where id = 1),
                    '{"knowhow": 4, "ideas": 2, "onsite": 3, "finished": 2, "offer": 2}'::jsonb) as mix
  ), counted as (
    select i.id, i.post_type,
           coalesce(i.production ->> 'status', '') in ('approved', 'posted') as ready
    from ideas i join projects p on p.id = i.project_id, bounds b
    where i.status <> 'dismissed' and (
      ((i.decision = 'go' or i.status = 'made' or i.production is not null)
        and coalesce(i.production ->> 'status', '') not in ('approved', 'posted', 'rejected')
        and p.status = 'active')
      or (coalesce(i.production ->> 'status', '') in ('approved', 'posted')
        and try_timestamptz(i.production ->> 'updated_at') >= b.starts_tz
        and try_timestamptz(i.production ->> 'updated_at') < b.ends_tz))
  ), kinds as (
    select k.kind, k.n,
           coalesce((plan.mix ->> k.kind)::int, 0) as target,
           count(c.id) as planned,
           count(c.id) filter (where c.ready) as ready,
           coalesce(jsonb_agg(c.id order by c.id) filter (where c.id is not null), '[]'::jsonb) as ids
    from unnest(array['knowhow', 'ideas', 'onsite', 'finished', 'offer']) with ordinality as k(kind, n)
    cross join plan
    left join counted c on c.post_type = k.kind
    group by k.kind, k.n, plan.mix
  )
  select jsonb_build_object(
    'month', to_char(b.starts, 'YYYY-MM'),
    'starts_on', to_char(b.starts, 'YYYY-MM-DD'),
    'ends_on', to_char(b.ends - interval '1 day', 'YYYY-MM-DD'),
    'posts_per_week', (select per_week from plan),
    'target', (select coalesce(sum(target), 0) from kinds),
    'planned', (select coalesce(sum(planned), 0) from kinds),
    'ready', (select coalesce(sum(ready), 0) from kinds),
    'untyped', (select count(*) from counted where post_type is null),
    'kinds', (select jsonb_object_agg(kind, jsonb_build_object(
                'target', target, 'planned', planned, 'ready', ready,
                'short', greatest(target - planned, 0), 'ids', ids)) from kinds))
  from bounds b
$$;

-- ── Row level security ────────────────────────────────────────────────────────

alter table public.content_plan enable row level security;

-- Everyone on the team reads the plan; only the owner changes the targets and offers.
-- (The row is made here; nobody inserts or deletes it.)
create policy "members read the content plan" on public.content_plan for select to authenticated
  using (public.is_member());
create policy "owner sets the content plan" on public.content_plan for update to authenticated
  using (public.is_owner()) with check (public.is_owner());

revoke execute on function public.content_month(timestamptz) from public, anon;
grant execute on function public.content_month(timestamptz) to authenticated, service_role;

alter publication supabase_realtime add table public.content_plan;

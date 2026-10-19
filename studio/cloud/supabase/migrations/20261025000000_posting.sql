-- Posting to Instagram (the Studio Director plan, phase 1; Bryan approved 2026-10-19): Studio posts
-- only what Bryan approved and scheduled. The Studio machine keeps the posts and their results
-- (its catalogue is the source of truth: posting.py, post_results.py) and mirrors them here so the
-- planner can show the posting calendar, Plan's "Coming up" and the Results page. Scheduling,
-- rescheduling and cancelling go through the Studio machine (POST /api/instagram/posts...), which
-- writes these rows with the service role; a row changed here directly is overwritten by the next
-- sync. The Instagram token never comes here.
--
-- content_month() now counts a post in the month it went out (or is scheduled to), and reports how
-- many of the month's posts are posted and scheduled, so the month's slots fill with what went out.

-- ── Posts ─────────────────────────────────────────────────────────────────────

-- id = the Studio machine's post id (one row per scheduling of a draft; a cancelled one stays).
create table public.posts (
  id            bigint primary key,
  production_id bigint not null,                      -- the draft on the Studio machine (ideas.production.id)
  idea_id       bigint references public.ideas (id) on delete set null,
  kind          text not null check (kind in ('reel', 'carousel')),
  title         text not null default '' check (length(title) <= 300),
  caption       text not null default '' check (length(caption) <= 5000),
  post_type     text check (post_type in ('knowhow', 'ideas', 'onsite', 'finished', 'offer')),
  version       int,
  scheduled_at  timestamptz not null,
  state         text not null check (state in ('scheduled', 'publishing', 'posted', 'failed', 'cancelled')),
  stage         text check (stage in ('preparing', 'uploading', 'processing', 'publishing')),
  error         text check (length(error) <= 1000),     -- why it failed or is waiting, in plain words
  problem       text check (problem in ('token', 'limit', 'spec', 'media', 'transient', 'offline', 'other')),
  next_try_at   timestamptz,
  permalink     text check (permalink is null or permalink ~ '^https://'),
  media_id      text,
  posted_at     timestamptz,
  cover_ms      int check (cover_ms is null or cover_ms >= 0),
  share_to_feed boolean not null default true,
  collaborators jsonb not null default '[]'::jsonb check (jsonb_typeof(collaborators) = 'array'),
  thumb_path    text,                                 -- a still in the thumbs bucket (posts/<id>.jpg)
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index posts_when on public.posts (coalesce(posted_at, scheduled_at)) where state <> 'cancelled';
create index posts_idea on public.posts (idea_id) where idea_id is not null;

-- ── Results ───────────────────────────────────────────────────────────────────

-- The latest numbers of each post on the account in the last 90 days (also posts made from the
-- phone: post_id null). Read once a day, and about an hour after Studio posts something.
create table public.post_results (
  media_id       text primary key,
  post_id        bigint references public.posts (id) on delete set null,
  idea_id        bigint references public.ideas (id) on delete set null,
  format         text check (format in ('reel', 'carousel', 'image')),
  post_type      text check (post_type in ('knowhow', 'ideas', 'onsite', 'finished', 'offer')),
  title          text not null default '',
  permalink      text check (permalink is null or permalink ~ '^https://'),
  posted_at      timestamptz,
  day            date,                                -- the Singapore day these numbers were read
  views          bigint,
  reach          bigint,
  likes          bigint,
  comments       bigint,
  saves          bigint,
  shares         bigint,
  interactions   bigint,
  avg_watch_ms   bigint,                              -- Reels: the average time a view lasted
  total_watch_ms bigint,                              -- Reels: all viewing time, replays included
  follows        bigint,
  profile_visits bigint,
  pulled_at      timestamptz not null default now()
);
create index post_results_posted on public.post_results (posted_at desc);

-- The account, one row per Singapore day: followers (as read the morning after), the day's reach,
-- views and interactions.
create table public.account_results (
  day          date primary key,
  followers    bigint,
  reach        bigint,
  views        bigint,
  interactions bigint,
  pulled_at    timestamptz not null default now()
);

-- ── Row level security: members read; the owner writes posts; results are the Studio machine's ──

alter table public.posts enable row level security;
alter table public.post_results enable row level security;
alter table public.account_results enable row level security;

create policy "members read posts" on public.posts for select to authenticated using (public.is_member());
create policy "owner adds posts" on public.posts for insert to authenticated with check (public.is_owner());
create policy "owner changes posts" on public.posts for update to authenticated
  using (public.is_owner()) with check (public.is_owner());
create policy "owner deletes posts" on public.posts for delete to authenticated using (public.is_owner());
create policy "members read post results" on public.post_results for select to authenticated using (public.is_member());
create policy "members read account results" on public.account_results for select to authenticated using (public.is_member());

alter publication supabase_realtime add table public.posts, public.post_results, public.account_results;

-- ── The month counts what went out ────────────────────────────────────────────

-- As 20261020000000_content_plan, plus: an idea with a post (scheduled, going out, posted or failed;
-- not cancelled) counts in the month of its post (posted_at, else scheduled_at); `posted` and
-- `scheduled` say how many of the month's posts went out and are waiting to. Ideas without a post
-- count as before (on their way, or approved this month).
create or replace function public.content_month(as_of timestamptz default now()) returns jsonb
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
  ), p as (
    -- each idea's post: a posted one first, else the newest
    select distinct on (idea_id) idea_id, state, coalesce(posted_at, scheduled_at) as out_at
    from posts where idea_id is not null and state <> 'cancelled'
    order by idea_id, (state = 'posted') desc, updated_at desc, id desc
  ), counted as (
    select i.id, i.post_type,
           coalesce(i.production ->> 'status', '') in ('approved', 'posted') or p.idea_id is not null as ready,
           coalesce(p.state = 'posted', false) as posted,
           coalesce(p.state in ('scheduled', 'publishing'), false) as scheduled
    from ideas i join projects pr on pr.id = i.project_id
    left join p on p.idea_id = i.id
    cross join bounds b
    where i.status <> 'dismissed' and (
      (p.idea_id is not null and p.out_at >= b.starts_tz and p.out_at < b.ends_tz)
      or (p.idea_id is null and (
        ((i.decision = 'go' or i.status = 'made' or i.production is not null)
          and coalesce(i.production ->> 'status', '') not in ('approved', 'posted', 'rejected')
          and pr.status = 'active')
        or (coalesce(i.production ->> 'status', '') in ('approved', 'posted')
          and try_timestamptz(i.production ->> 'updated_at') >= b.starts_tz
          and try_timestamptz(i.production ->> 'updated_at') < b.ends_tz))))
  ), kinds as (
    select k.kind, k.n,
           coalesce((plan.mix ->> k.kind)::int, 0) as target,
           count(c.id) as planned,
           count(c.id) filter (where c.ready) as ready,
           count(c.id) filter (where c.posted) as posted,
           count(c.id) filter (where c.scheduled) as scheduled,
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
    'posted', (select coalesce(sum(posted), 0) from kinds),
    'scheduled', (select coalesce(sum(scheduled), 0) from kinds),
    'untyped', (select count(*) from counted where post_type is null),
    'kinds', (select jsonb_object_agg(kind, jsonb_build_object(
                'target', target, 'planned', planned, 'ready', ready, 'posted', posted, 'scheduled', scheduled,
                'short', greatest(target - planned, 0), 'ids', ids)) from kinds))
  from bounds b
$$;

revoke execute on function public.content_month(timestamptz) from public, anon;
grant execute on function public.content_month(timestamptz) to authenticated, service_role;

-- The profile grid (Bryan, via the content lead, 2026-10-27: "see and plan the whole @urbanmakers_sg
-- profile before anything posts"). The planner's Grid page draws the profile as a phone shows it: the
-- posts already on the account (post_results, read by the Studio machine, now with a small still of
-- each), then what's scheduled, approved, waiting in Review and the ideas with a posting date, newest
-- at the top. Bryan drags tiles to change the order (Save this order sets the dates, one request), pins
-- (Instagram's API can't pin: the planner remembers it and reminds him to pin in the app), and taps a
-- tile to check its photos and caption. Nothing posts from here: posting stays Approve, then Schedule.
--
-- Until this runs the planner hides the grid's controls and says it needs the update; the Studio
-- machine sends nothing new (cloud_sync.check_grid).

-- ── Ideas ─────────────────────────────────────────────────────────────────────

alter table public.ideas
  -- The day it's meant to go out (Singapore). Set on the idea page, by the grid's Save this order or
  -- by Spread these. A scheduled post's own time wins once it's scheduled.
  add column post_on date,
  -- A Reel shows in the profile grid (Instagram's share_to_feed); false = the Reels tab only. The
  -- Schedule dialog starts from it and a scheduled Reel follows it. Carousels are always in the grid.
  add column in_grid boolean not null default true,
  -- Needs a fact from Bryan before it can go out: what's missing (may be ''), null = nothing. The
  -- words may also carry [check] where a figure goes (topics.py writes those): the planner flags both.
  add column needs_fact text check (needs_fact is null or length(needs_fact) <= 300),
  -- How a carousel's slides are laid out: null = words on the slides (the usual), 'photos' = each
  -- photo fills its slide (4:5), with slide 1's heading as an optional small cover line.
  add column slide_layout text check (slide_layout is null or slide_layout in ('photos')),
  -- Bryan's "Confirm photos": the photos and their order are what he wants. Changing the slides
  -- afterwards clears it (trigger below).
  add column photos_confirmed_at timestamptz,
  add column photos_confirmed_by uuid references public.profiles (id) on delete set null;

create index ideas_post_on on public.ideas (post_on) where post_on is not null;

create function public.ideas_photos_confirm() returns trigger
language plpgsql set search_path = public as $$
begin
  if new.slides is distinct from old.slides
     and new.photos_confirmed_at is not distinct from old.photos_confirmed_at then
    new.photos_confirmed_at := null;
    new.photos_confirmed_by := null;
  end if;
  return new;
end $$;

create trigger ideas_photos_confirm before update on public.ideas
  for each row execute function public.ideas_photos_confirm();

-- ── Posts on the account: a still, the caption, in the grid or not ───────────

alter table public.post_results
  -- A small still of the post (thumbs bucket results/<media_id>.jpg; the Studio machine keeps the
  -- copy it read from Instagram, whose own links run out).
  add column thumb_path text check (thumb_path is null or thumb_path ~ '^results/[A-Za-z0-9_.-]+$'),
  -- Instagram's is_shared_to_feed for a Reel (false = Reels tab only); null = not a Reel, or unknown.
  add column in_grid boolean,
  add column caption text not null default '' check (length(caption) <= 5000);

-- ── Pins ──────────────────────────────────────────────────────────────────────

-- What Bryan pins to the top of the profile (Instagram allows three). An idea (planned, scheduled or
-- posted through Studio), or a post made from the phone (media_id). Instagram's API can't pin, so this
-- is the planner's: the grid shows them first and says "Pin after <date>" until he does it in the app.
create table public.grid_pins (
  id         bigint generated always as identity primary key,
  idea_id    bigint unique references public.ideas (id) on delete cascade,
  media_id   text unique check (media_id is null or length(media_id) <= 64),
  position   smallint not null default 1 check (position between 1 and 3),
  pinned_by  uuid default auth.uid() references public.profiles (id) on delete set null,
  done_at    timestamptz,   -- Bryan pinned it in the Instagram app (the reminder stops)
  created_at timestamptz not null default now(),
  check ((idea_id is null) <> (media_id is null))
);

create function public.grid_pins_limit() returns trigger
language plpgsql set search_path = public as $$
begin
  if (select count(*) from public.grid_pins) >= 3 then
    raise exception 'Instagram pins at most 3 posts. Unpin one first.' using errcode = 'P0001';
  end if;
  return new;
end $$;

create trigger grid_pins_limit before insert on public.grid_pins
  for each row execute function public.grid_pins_limit();

alter table public.grid_pins enable row level security;
create policy "members read pins" on public.grid_pins for select to authenticated using (public.is_member());
create policy "members pin" on public.grid_pins for insert to authenticated with check (public.is_member());
create policy "members reorder pins" on public.grid_pins for update to authenticated
  using (public.is_member()) with check (public.is_member());
create policy "members unpin" on public.grid_pins for delete to authenticated using (public.is_member());

alter publication supabase_realtime add table public.grid_pins;

-- ── The grid in one request ───────────────────────────────────────────────────

-- Everything the Grid page draws, in one round trip (runs with the caller's rights, so RLS applies):
--   ideas  not dismissed or rejected, with a posting date, a draft or a post: their call, status,
--          production, date, flags, how many slides, the cover's still (a carousel's first slide, a
--          Reel's first take) and whether the words carry [check]
--   posts  each idea's post (posted first, else the newest that isn't cancelled), and posts with no idea
--   media  the latest 60 posts on the account (post_results), with their still
--   pins   the pins, in order
create function public.profile_grid() returns jsonb
language sql stable set search_path = public as $$
  with p as (
    select distinct on (coalesce(idea_id, -id)) *
    from posts where state <> 'cancelled'
    order by coalesce(idea_id, -id), (state = 'posted') desc, id desc
  ), picked as (
    select i.* from ideas i
    where i.status <> 'dismissed'
      and coalesce(i.production ->> 'status', '') <> 'rejected'
      and (i.post_on is not null or i.production is not null or exists (select 1 from p where p.idea_id = i.id))
  )
  select jsonb_build_object(
    'ideas', coalesce((select jsonb_agg(jsonb_build_object(
        'id', i.id, 'project_id', i.project_id, 'title', i.title, 'format', i.format, 'post_type', i.post_type,
        'decision', i.decision, 'status', i.status, 'production', i.production, 'post_on', i.post_on,
        'in_grid', i.in_grid, 'needs_fact', i.needs_fact, 'slide_layout', i.slide_layout,
        'photos_confirmed_at', i.photos_confirmed_at,
        'slides_count', coalesce(jsonb_array_length(i.slides), 0),
        'cover_text', coalesce(i.slides -> 0 ->> 'heading', ''),
        'cover', coalesce(i.slides -> 0 -> 'image' ->> 'thumb_path',
          (select coalesce(t.thumb_path, c.thumb_path) from shots s join takes t on t.shot_id = s.id
             left join clips c on c.id = t.clip_id
           where s.idea_id = i.id order by s.position, s.id, t.id limit 1)),
        'check', concat_ws(' ', i.title, i.hook, i.caption, i.cta, i.slides::text) ilike '%[check]%'
      ) order by i.id) from picked i), '[]'::jsonb),
    'posts', coalesce((select jsonb_agg(jsonb_build_object(
        'id', p.id, 'idea_id', p.idea_id, 'production_id', p.production_id, 'kind', p.kind, 'title', p.title,
        'post_type', p.post_type, 'state', p.state, 'scheduled_at', p.scheduled_at, 'posted_at', p.posted_at,
        'error', p.error, 'permalink', p.permalink, 'media_id', p.media_id, 'share_to_feed', p.share_to_feed,
        'thumb_path', p.thumb_path) order by p.id) from p), '[]'::jsonb),
    'media', coalesce((select jsonb_agg(jsonb_build_object(
        'media_id', r.media_id, 'post_id', r.post_id, 'idea_id', r.idea_id, 'format', r.format,
        'post_type', r.post_type, 'title', r.title, 'caption', r.caption, 'permalink', r.permalink,
        'posted_at', r.posted_at, 'thumb_path', r.thumb_path, 'in_grid', r.in_grid,
        'views', r.views, 'likes', r.likes, 'saves', r.saves) order by r.posted_at desc)
      from (select * from post_results order by posted_at desc nulls last limit 60) r), '[]'::jsonb),
    'pins', coalesce((select jsonb_agg(jsonb_build_object('id', g.id, 'idea_id', g.idea_id, 'media_id', g.media_id,
        'position', g.position, 'done_at', g.done_at, 'created_at', g.created_at) order by g.position, g.id) from grid_pins g), '[]'::jsonb))
$$;

revoke execute on function public.profile_grid() from public, anon;
grant execute on function public.profile_grid() to authenticated, service_role;

-- Save this order (and Spread these): many ideas' posting dates in one request, [{id, post_on}] with
-- post_on 'YYYY-MM-DD' or null. Runs with the caller's rights: members edit ideas, as anywhere else.
-- Returns how many ideas changed.
create function public.set_post_dates(p_dates jsonb) returns int
language plpgsql set search_path = public as $$
declare
  n int;
begin
  if jsonb_typeof(p_dates) <> 'array' or jsonb_array_length(p_dates) > 200 then
    raise exception 'a list of at most 200 {id, post_on}' using errcode = '22023';
  end if;
  with x as (
    select (e ->> 'id')::bigint as id, nullif(e ->> 'post_on', '')::date as post_on
    from jsonb_array_elements(p_dates) e
  ), done as (
    update ideas i set post_on = x.post_on from x
    where i.id = x.id and i.post_on is distinct from x.post_on
    returning i.id
  )
  select count(*) into n from done;
  return n;
end $$;

revoke execute on function public.set_post_dates(jsonb) from public, anon;
grant execute on function public.set_post_dates(jsonb) to authenticated, service_role;

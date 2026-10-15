-- Studio planning board, online (Supabase).
--
-- The team plans here: projects > arcs > ideas > shots (the storyboard), with
-- votes and comments. The Studio laptop syncs with it whenever it's on: it
-- pushes clip summaries (title, quality verdict, a still) and footage matched to
-- shots (takes), pulls the plan to match new footage against, and runs the jobs
-- only it can do (music shortlists with Bryan's Epidemic Sound key; "Make this",
-- which edits on the laptop). Claude jobs that only need text (organise, refine)
-- run in Edge Functions, so they work while the laptop is off.
--
-- Access: invite-only. Everyone with a profile is a member; one is the owner
-- (Bryan), who alone decides Go / Park / Dismiss and sends ideas to the editor.
-- The laptop and the Edge Functions use the service role, which bypasses RLS.

-- ── People ────────────────────────────────────────────────────────────────────

create table public.profiles (
  id          uuid primary key references auth.users (id) on delete cascade,
  name        text not null default '',
  email       text not null default '',
  role        text not null default 'team' check (role in ('owner', 'team')),
  created_at  timestamptz not null default now()
);

-- Every new auth user gets a profile; the very first one is the owner.
create function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, name, email, role)
  values (
    new.id,
    coalesce(nullif(new.raw_user_meta_data ->> 'name', ''), split_part(new.email, '@', 1)),
    coalesce(new.email, ''),
    case when exists (select 1 from public.profiles where role = 'owner') then 'team' else 'owner' end
  );
  return new;
end $$;

create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

create function public.is_member() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.profiles where id = auth.uid())
$$;

create function public.is_owner() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.profiles where id = auth.uid() and role = 'owner')
$$;

-- ── The plan ──────────────────────────────────────────────────────────────────

create table public.projects (
  id          bigint generated always as identity primary key,
  name        text not null,
  brand       text not null default 'urban_makers' check (brand in ('urban_makers', 'personal')),
  folder      text not null default '',          -- inbox subfolder on the laptop
  brief       text not null default '',
  status      text not null default 'active' check (status in ('active', 'archived')),
  created_by  uuid references public.profiles (id) on delete set null default auth.uid(),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table public.arcs (
  id          bigint generated always as identity primary key,
  project_id  bigint not null references public.projects (id) on delete cascade,
  title       text not null,
  purpose     text not null default '',
  position    double precision not null default 0,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table public.ideas (
  id          bigint generated always as identity primary key,
  project_id  bigint not null references public.projects (id) on delete cascade,
  arc_id      bigint references public.arcs (id) on delete set null,   -- null = Unsorted
  title       text not null,
  brand       text not null default 'urban_makers' check (brand in ('urban_makers', 'personal')),
  format      text not null default 'talking_head'
              check (format in ('talking_head', 'day_in_the_life', 'compilation')),
  hook        text not null default '',
  purpose     text not null default '',
  cta         text not null default '',
  caption     text not null default '',
  est_seconds double precision not null default 30,
  -- {mood, tempo_bpm, search, source: epidemic|instagram, note,
  --  shortlist: [{recording_id, title, artist, bpm, preview_url, page_url, why}], chosen: {...} | null}
  music       jsonb not null default '{}'::jsonb,
  decision    text not null default 'proposed' check (decision in ('proposed', 'go', 'parked')),
  status      text not null default 'new' check (status in ('new', 'made', 'dismissed')),
  -- Written by the laptop once made: {id, status, version, updated_at}
  production  jsonb,
  position    double precision not null default 0,
  created_by  uuid references public.profiles (id) on delete set null default auth.uid(),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index ideas_project on public.ideas (project_id, arc_id, position);

create table public.shots (
  id          bigint generated always as identity primary key,
  idea_id     bigint not null references public.ideas (id) on delete cascade,
  position    double precision not null default 0,
  kind        text check (kind in ('talking', 'broll', 'detail', 'before_after', 'reaction', 'screen')),
  framing     text check (framing in ('wide', 'medium', 'close', 'detail', 'pov', 'overhead')),
  camera      text check (camera in ('iphone', 'zfc', 'gopro', 'any')),
  description text not null,
  line        text not null default '',
  on_screen   text not null default '',
  seconds     double precision,
  why         text not null default '',
  filmed_at   timestamptz,                      -- ticked off on site
  filmed_by   uuid references public.profiles (id) on delete set null,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index shots_idea on public.shots (idea_id, position);

-- ── Footage (written by the laptop) ───────────────────────────────────────────

create table public.clips (
  id          bigint primary key,                -- the laptop catalogue's clip id
  title       text not null default '',
  recorded_at timestamptz,
  duration    double precision,
  project_id  bigint references public.projects (id) on delete set null,
  verdict     text check (verdict in ('good', 'check', 'retake')),
  flags       jsonb not null default '[]'::jsonb, -- [{level, text, tip}]
  thumb_path  text,                               -- object in the "thumbs" bucket
  updated_at  timestamptz not null default now()
);

create table public.takes (
  id          bigint generated always as identity primary key,
  shot_id     bigint not null references public.shots (id) on delete cascade,
  clip_id     bigint not null references public.clips (id) on delete cascade,
  start       double precision,
  "end"       double precision,
  source      text not null default 'manual' check (source in ('auto', 'manual')),
  note        text not null default '',
  thumb_path  text,                               -- a still near "start", uploaded by the laptop
  created_by  uuid references public.profiles (id) on delete set null default auth.uid(),
  created_at  timestamptz not null default now(),
  unique (shot_id, clip_id)
);
create index takes_clip on public.takes (clip_id);

-- ── Team input ────────────────────────────────────────────────────────────────

create table public.votes (
  idea_id     bigint not null references public.ideas (id) on delete cascade,
  user_id     uuid not null references public.profiles (id) on delete cascade,
  vote        smallint not null check (vote in (1, -1)),
  created_at  timestamptz not null default now(),
  primary key (idea_id, user_id)
);

create table public.comments (
  id          bigint generated always as identity primary key,
  idea_id     bigint not null references public.ideas (id) on delete cascade,
  author      uuid references public.profiles (id) on delete set null default auth.uid(),
  author_name text not null default '',
  body        text not null check (length(body) between 1 and 4000),
  created_at  timestamptz not null default now()
);
create index comments_idea on public.comments (idea_id, id);

-- ── Background work ───────────────────────────────────────────────────────────

-- organise / develop run in Edge Functions (runner 'cloud'); music / make run on
-- the laptop (runner 'laptop'), whenever it's on.
create table public.jobs (
  id           bigint generated always as identity primary key,
  kind         text not null check (kind in ('organise', 'develop', 'music', 'make')),
  runner       text not null check (runner in ('cloud', 'laptop')),
  project_id   bigint references public.projects (id) on delete cascade,
  idea_id      bigint references public.ideas (id) on delete cascade,
  payload      jsonb not null default '{}'::jsonb,
  state        text not null default 'queued' check (state in ('queued', 'running', 'done', 'failed')),
  result       jsonb,
  error        text,
  requested_by uuid references public.profiles (id) on delete set null default auth.uid(),
  created_at   timestamptz not null default now(),
  started_at   timestamptz,
  updated_at   timestamptz not null default now()
);
create index jobs_open on public.jobs (runner, state, id);

-- Text the Claude prompts need from the laptop: brands.md and the playbook.
create table public.context (
  key         text primary key check (key in ('brands', 'playbook')),
  value       text not null default '',
  updated_at  timestamptz not null default now()
);

-- One row: when the laptop last checked in, so the board can say if it's on.
create table public.laptop_status (
  id          smallint primary key default 1 check (id = 1),
  last_seen   timestamptz,
  version     text not null default '',
  info        jsonb not null default '{}'::jsonb
);
insert into public.laptop_status (id) values (1);

-- ── Housekeeping triggers ─────────────────────────────────────────────────────

create function public.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

create trigger projects_touch before update on public.projects for each row execute function public.touch_updated_at();
create trigger arcs_touch before update on public.arcs for each row execute function public.touch_updated_at();
create trigger ideas_touch before update on public.ideas for each row execute function public.touch_updated_at();
create trigger shots_touch before update on public.shots for each row execute function public.touch_updated_at();
create trigger clips_touch before update on public.clips for each row execute function public.touch_updated_at();
create trigger jobs_touch before update on public.jobs for each row execute function public.touch_updated_at();

-- Bryan's calls stay Bryan's: only the owner (or the service role) can change a
-- decision, dismiss or mark made, or touch what the laptop writes.
create function public.guard_idea_owner_fields() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if auth.role() = 'service_role' or public.is_owner() then
    return new;
  end if;
  if tg_op = 'INSERT' then
    new.decision := 'proposed';
    new.status := 'new';
    new.production := null;
    new.created_by := auth.uid();
    return new;
  end if;
  if new.decision is distinct from old.decision
     or new.status is distinct from old.status
     or new.production is distinct from old.production then
    raise exception 'only the owner can decide on an idea' using errcode = '42501';
  end if;
  return new;
end $$;

create trigger ideas_guard before insert or update on public.ideas
  for each row execute function public.guard_idea_owner_fields();

-- Comments carry their author's name; filming ticks carry who ticked.
create function public.stamp_comment() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if auth.role() <> 'service_role' then
    new.author := auth.uid();
  end if;
  new.author_name := coalesce((select name from public.profiles where id = new.author), new.author_name, '');
  return new;
end $$;
create trigger comments_stamp before insert on public.comments for each row execute function public.stamp_comment();

create function public.stamp_filmed() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.filmed_at is distinct from old.filmed_at then
    new.filmed_by := case when new.filmed_at is null then null else coalesce(auth.uid(), new.filmed_by) end;
  end if;
  return new;
end $$;
create trigger shots_stamp_filmed before update on public.shots for each row execute function public.stamp_filmed();

-- ── Row level security ────────────────────────────────────────────────────────

alter table public.profiles enable row level security;
alter table public.projects enable row level security;
alter table public.arcs enable row level security;
alter table public.ideas enable row level security;
alter table public.shots enable row level security;
alter table public.clips enable row level security;
alter table public.takes enable row level security;
alter table public.votes enable row level security;
alter table public.comments enable row level security;
alter table public.jobs enable row level security;
alter table public.context enable row level security;
alter table public.laptop_status enable row level security;

create policy "members read profiles" on public.profiles for select to authenticated using (public.is_member());
create policy "members rename themselves" on public.profiles for update to authenticated
  using (id = auth.uid()) with check (id = auth.uid() and role = (select p.role from public.profiles p where p.id = auth.uid()));

-- The plan itself: every member can read and edit.
create policy "members read projects" on public.projects for select to authenticated using (public.is_member());
create policy "members add projects" on public.projects for insert to authenticated with check (public.is_member());
create policy "members edit projects" on public.projects for update to authenticated
  using (public.is_member()) with check (public.is_member());
create policy "owner deletes projects" on public.projects for delete to authenticated using (public.is_owner());
create policy "members plan arcs" on public.arcs for all to authenticated
  using (public.is_member()) with check (public.is_member());
create policy "members read ideas" on public.ideas for select to authenticated using (public.is_member());
create policy "members add ideas" on public.ideas for insert to authenticated with check (public.is_member());
create policy "members edit ideas" on public.ideas for update to authenticated
  using (public.is_member()) with check (public.is_member());
-- Removing an idea is the owner's call (Dismiss), or the author's own unwanted one.
create policy "owner or author deletes ideas" on public.ideas for delete to authenticated
  using (public.is_owner() or created_by = auth.uid());
create policy "members plan shots" on public.shots for all to authenticated
  using (public.is_member()) with check (public.is_member());

create policy "members see clips" on public.clips for select to authenticated using (public.is_member());

create policy "members see takes" on public.takes for select to authenticated using (public.is_member());
create policy "members attach footage" on public.takes for insert to authenticated
  with check (public.is_member() and source = 'manual' and created_by = auth.uid());
create policy "members detach footage" on public.takes for delete to authenticated using (public.is_member());

create policy "members see votes" on public.votes for select to authenticated using (public.is_member());
create policy "members vote for themselves" on public.votes for insert to authenticated
  with check (public.is_member() and user_id = auth.uid());
create policy "members change their vote" on public.votes for update to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "members withdraw their vote" on public.votes for delete to authenticated using (user_id = auth.uid());

create policy "members read comments" on public.comments for select to authenticated using (public.is_member());
create policy "members comment" on public.comments for insert to authenticated with check (public.is_member());
create policy "authors delete their comments" on public.comments for delete to authenticated
  using (author = auth.uid() or public.is_owner());

-- Jobs: members see them all; they can queue a music shortlist; only the owner
-- can send an idea to the editor. organise/develop are queued by the Edge Functions.
create policy "members see jobs" on public.jobs for select to authenticated using (public.is_member());
create policy "members queue music" on public.jobs for insert to authenticated
  with check (public.is_member() and kind = 'music' and runner = 'laptop' and state = 'queued');
create policy "owner queues edits" on public.jobs for insert to authenticated
  with check (public.is_owner() and kind = 'make' and runner = 'laptop' and state = 'queued');

create policy "members read context" on public.context for select to authenticated using (public.is_member());
create policy "members see the laptop" on public.laptop_status for select to authenticated using (public.is_member());

-- ── Live updates ──────────────────────────────────────────────────────────────

alter publication supabase_realtime add table
  public.projects, public.arcs, public.ideas, public.shots, public.clips, public.takes,
  public.votes, public.comments, public.jobs, public.laptop_status;

-- ── Stills (private bucket; the laptop uploads, members read) ─────────────────

insert into storage.buckets (id, name, public) values ('thumbs', 'thumbs', false)
  on conflict (id) do nothing;

create policy "members read stills" on storage.objects for select to authenticated
  using (bucket_id = 'thumbs' and public.is_member());

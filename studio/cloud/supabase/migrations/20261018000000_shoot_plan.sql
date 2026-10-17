-- Shoot plan: a project's renovation phases and the site days the team is there, so the
-- planner can show what to film on each visit (and what can't be filmed after this week).
-- Shots belong to a phase (Claude places them; the team can move them) or are pinned to
-- one site day. Plus: footage uploaded straight to a shot from the planner, and the
-- per-post inbox folder Studio makes for each Go idea.

create table public.phases (
  id          bigint generated always as identity primary key,
  project_id  bigint not null references public.projects (id) on delete cascade,
  name        text not null check (length(name) between 1 and 80),   -- "Waterproofing"
  starts_on   date,
  ends_on     date,
  position    double precision not null default 0,
  note        text not null default '',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  check (ends_on is null or starts_on is null or ends_on >= starts_on)
);
create index phases_project on public.phases (project_id, position);

create table public.site_days (
  id          bigint generated always as identity primary key,
  project_id  bigint not null references public.projects (id) on delete cascade,
  day         date not null,
  phase_id    bigint references public.phases (id) on delete set null,
  who         text not null default '',                               -- who's on site, free text
  note        text not null default '',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (project_id, day)
);

alter table public.shots
  add column phase_id bigint references public.phases (id) on delete set null,
  add column shoot_on date;                      -- pinned to one site day (else: any day in its phase)
create index shots_phase on public.shots (phase_id);

-- The inbox folder Studio made for a Go idea (camera clips dropped there are matched to
-- this idea's shots only). Written by the Studio machine.
alter table public.ideas add column folder text not null default '';

-- Which post folder a clip came from, if any. Written by the Studio machine.
alter table public.clips add column idea_id bigint references public.ideas (id) on delete set null;

-- Footage uploaded from the planner straight to a shot. Written by the Studio machine
-- (it receives the upload); members read it to show progress.
create table public.shot_uploads (
  id          uuid primary key,                  -- the Studio machine's upload id
  shot_id     bigint not null references public.shots (id) on delete cascade,
  file_name   text not null,
  size        bigint not null,
  state       text not null default 'uploading'
              check (state in ('uploading', 'received', 'ingested', 'failed')),
  error       text,
  clip_id     bigint references public.clips (id) on delete set null,
  uploaded_by uuid references public.profiles (id) on delete set null,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index shot_uploads_shot on public.shot_uploads (shot_id);

-- New job: 'place' (the Studio machine asks Claude to put a project's unplaced shots into
-- its phases; any member may ask).
alter table public.jobs drop constraint jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
  check (kind in ('organise', 'develop', 'music', 'make', 'style', 'place'));

-- ── Triggers ──────────────────────────────────────────────────────────────────

create trigger phases_touch before update on public.phases
  for each row execute function public.touch_updated_at();
create trigger site_days_touch before update on public.site_days
  for each row execute function public.touch_updated_at();
create trigger shot_uploads_touch before update on public.shot_uploads
  for each row execute function public.touch_updated_at();

-- ── Row level security ────────────────────────────────────────────────────────

alter table public.phases enable row level security;
alter table public.site_days enable row level security;
alter table public.shot_uploads enable row level security;

create policy "members plan phases" on public.phases for all to authenticated
  using (public.is_member()) with check (public.is_member());
create policy "members plan site days" on public.site_days for all to authenticated
  using (public.is_member()) with check (public.is_member());
create policy "members see uploads" on public.shot_uploads for select to authenticated using (public.is_member());

create policy "members queue placing" on public.jobs for insert to authenticated
  with check (public.is_member() and kind = 'place' and runner = 'laptop' and state = 'queued');

alter publication supabase_realtime add table public.phases, public.site_days, public.shot_uploads;

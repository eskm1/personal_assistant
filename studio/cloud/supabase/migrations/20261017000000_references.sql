-- Reference guides: reels the team admires (their own best ones and other accounts'),
-- each with a note on why it works. The laptop analyses each one once (cuts, pace,
-- music level, and Claude's read of a dozen stills) and, whenever the set of active
-- references differs from the one the current style guide was built from, drafts a
-- new style guide that says what changed. The owner approves it; the laptop then
-- writes it into the playbook the idea generator and editor follow.

create table public.reference_videos (
  id          bigint generated always as identity primary key,
  brand       text not null default 'urban_makers' check (brand in ('urban_makers', 'personal')),
  side        text not null default 'inspiration' check (side in ('ours', 'inspiration')),
  url         text,                               -- a reel / short link (public)
  file_name   text,                               -- or a video dropped in the laptop's References folder
  title       text not null default '',
  why         text not null default '',           -- the team's note: why it works
  status      text not null default 'active' check (status in ('active', 'retired')),
  added_by    uuid references public.profiles (id) on delete set null default auth.uid(),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  retired_at  timestamptz,
  check (url is not null or file_name is not null),
  check (url is null or url ~* '^https://')
);
create unique index reference_videos_url on public.reference_videos (url) where url is not null;
create unique index reference_videos_file on public.reference_videos (file_name) where file_name is not null;

-- Written by the laptop only.
create table public.reference_analysis (
  reference_id bigint primary key references public.reference_videos (id) on delete cascade,
  state        text not null default 'pending' check (state in ('pending', 'running', 'done', 'failed')),
  measured     jsonb,                              -- style_study.measure() numbers
  look         jsonb,                              -- Claude's read of the stills
  stills       text[] not null default '{}',       -- objects in the "thumbs" bucket: refs/<id>/<n>.jpg
  error        text,
  analyser     int not null default 1,             -- bumped when the measuring changes, to re-analyse
  analysed_at  timestamptz,
  updated_at   timestamptz not null default now()
);

create table public.style_guides (
  id            bigint generated always as identity primary key,
  brand         text not null default 'urban_makers' check (brand in ('urban_makers', 'personal')),
  version       int not null,
  status        text not null default 'draft' check (status in ('draft', 'active', 'archived', 'rejected')),
  content       text not null default '',          -- the "House style" playbook section (Markdown)
  report        text not null default '',          -- for people: what the references show
  changes       text not null default '',          -- what changed since the previous guide, and why
  reference_ids bigint[] not null default '{}',    -- the active references it was built from
  added_ids     bigint[] not null default '{}',
  removed_ids   bigint[] not null default '{}',
  usage         jsonb,
  created_by    uuid references public.profiles (id) on delete set null,
  created_at    timestamptz not null default now(),
  approved_by   uuid references public.profiles (id) on delete set null,
  approved_at   timestamptz,
  unique (brand, version)
);

-- New jobs: 'style' (the laptop drafts a style guide; any member may ask).
alter table public.jobs drop constraint jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
  check (kind in ('organise', 'develop', 'music', 'make', 'style'));

-- ── Triggers ──────────────────────────────────────────────────────────────────

create trigger reference_videos_touch before update on public.reference_videos
  for each row execute function public.touch_updated_at();
create trigger reference_analysis_touch before update on public.reference_analysis
  for each row execute function public.touch_updated_at();

-- Every reference gets an analysis row the laptop picks up; a changed link is re-analysed.
create function public.queue_reference_analysis() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if tg_op = 'INSERT' then
    insert into public.reference_analysis (reference_id) values (new.id) on conflict do nothing;
  elsif new.url is distinct from old.url or new.file_name is distinct from old.file_name then
    update public.reference_analysis set state = 'pending', error = null where reference_id = new.id;
  end if;
  if new.status = 'retired' and (tg_op = 'INSERT' or old.status <> 'retired') then
    new.retired_at := now();
  elsif new.status = 'active' then
    new.retired_at := null;
  end if;
  return new;
end $$;
create trigger reference_videos_queue_ins after insert on public.reference_videos
  for each row execute function public.queue_reference_analysis();
create trigger reference_videos_queue_upd before update on public.reference_videos
  for each row execute function public.queue_reference_analysis();

-- Approving a guide makes it the one in force; the previous one is archived.
create function public.activate_style_guide() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.status = 'active' and old.status is distinct from 'active' then
    update public.style_guides set status = 'archived'
      where brand = new.brand and status = 'active' and id <> new.id;
    new.approved_by := coalesce(auth.uid(), new.approved_by);
    new.approved_at := now();
  end if;
  return new;
end $$;
create trigger style_guides_activate before update on public.style_guides
  for each row execute function public.activate_style_guide();

-- ── Row level security ────────────────────────────────────────────────────────

alter table public.reference_videos enable row level security;
alter table public.reference_analysis enable row level security;
alter table public.style_guides enable row level security;

create policy "members read references" on public.reference_videos for select to authenticated using (public.is_member());
create policy "members add references" on public.reference_videos for insert to authenticated
  with check (public.is_member());
create policy "members edit references" on public.reference_videos for update to authenticated
  using (public.is_member()) with check (public.is_member());
create policy "owner or adder deletes references" on public.reference_videos for delete to authenticated
  using (public.is_owner() or added_by = auth.uid());

create policy "members read analyses" on public.reference_analysis for select to authenticated using (public.is_member());

create policy "members read style guides" on public.style_guides for select to authenticated using (public.is_member());
-- Only the owner approves, rejects or edits a guide; the laptop (service role) writes drafts.
create policy "owner decides style guides" on public.style_guides for update to authenticated
  using (public.is_owner()) with check (public.is_owner());

create policy "members queue style guides" on public.jobs for insert to authenticated
  with check (public.is_member() and kind = 'style' and runner = 'laptop' and state = 'queued');

alter publication supabase_realtime add table
  public.reference_videos, public.reference_analysis, public.style_guides;

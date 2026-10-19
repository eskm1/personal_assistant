-- The reference library: the team imports many Instagram posts at once (a pasted list or a CSV
-- such as an export of saved posts), as "Ours" or "Inspiration". Bulk-imported references are
-- held: nothing is downloaded or spent until the owner presses "Analyse" (a sample first, or all
-- of them). The Studio machine then analyses them slowly (one every minute or so, so Instagram
-- doesn't block it): Reels are measured and looked at as before, carousels and photos slide by
-- slide, and whatever engagement Instagram shows (likes, comments, views, followers) is kept.
-- From all the analyses Claude writes a "taste report" (design, content, engagement and
-- recommendations) with a proposed style guide update; the owner approves it, and only then do
-- the style guide and the playbook's taste section change.

-- ── Imports ───────────────────────────────────────────────────────────────────

-- One row per bulk import (a pasted list or a CSV file), for the planner's progress line.
create table public.reference_imports (
  id          bigint generated always as identity primary key,
  brand       text not null default 'urban_makers' check (brand in ('urban_makers', 'personal')),
  side        text not null default 'inspiration' check (side in ('ours', 'inspiration')),
  name        text not null default '' check (length(name) <= 200),   -- the file name, or "Pasted list"
  total       int not null default 0,      -- links in the list
  added       int not null default 0,      -- new references
  duplicates  int not null default 0,      -- already in references, or twice in the list
  invalid     int not null default 0,      -- lines that weren't links
  created_by  uuid references public.profiles (id) on delete set null default auth.uid(),
  created_at  timestamptz not null default now()
);

-- The Instagram post a link points at (/reel/<code>/, /p/<code>/, /tv/<code>/, with or without
-- the account in front): the same post under two links is one reference.
create function public.reference_shortcode(u text) returns text
language sql immutable as $$
  select substring(u from '^https://(?:www\.|m\.)?instagram\.com/(?:[A-Za-z0-9_.]+/)?(?:reels?|p|tv)/([A-Za-z0-9_-]{5,})')
$$;

alter table public.reference_videos
  add column account   text not null default '' check (length(account) <= 100),  -- the Instagram account
  add column post_type text check (post_type in ('reel', 'video', 'carousel', 'image', 'series')),
  add column purpose   text check (purpose in ('design', 'content', 'engagement')), -- the team's tag, if any
  add column import_id bigint references public.reference_imports (id) on delete set null,
  add column shortcode text;

-- Earlier links: the first reference of each post keeps the key (a later duplicate keeps none).
update public.reference_videos v set shortcode = public.reference_shortcode(v.url)
  where v.url is not null and public.reference_shortcode(v.url) is not null
    and v.id = (select min(w.id) from public.reference_videos w
                where public.reference_shortcode(w.url) = public.reference_shortcode(v.url));
create unique index reference_videos_shortcode on public.reference_videos (shortcode) where shortcode is not null;
create index reference_videos_import on public.reference_videos (import_id) where import_id is not null;

create function public.reference_key() returns trigger
language plpgsql as $$
begin
  if tg_op = 'INSERT' or new.url is distinct from old.url then
    new.shortcode := public.reference_shortcode(new.url);
  end if;
  return new;
end $$;
create trigger reference_videos_key before insert or update on public.reference_videos
  for each row execute function public.reference_key();

-- ── Analyses ──────────────────────────────────────────────────────────────────

-- 'held': imported in bulk and waiting for the owner's "Analyse" (nothing spent yet).
alter table public.reference_analysis drop constraint reference_analysis_state_check;
alter table public.reference_analysis add constraint reference_analysis_state_check
  check (state in ('held', 'pending', 'running', 'done', 'failed'));
alter table public.reference_analysis
  add column kind       text check (kind in ('video', 'images')),  -- how it was analysed
  add column engagement jsonb,   -- {likes, comments, views, followers, uploaded, source} as Instagram showed them
  add column problem    text check (problem in ('login', 'rate', 'gone', 'other')); -- why it failed, for grouping
create index reference_analysis_state on public.reference_analysis (state);

create or replace function public.queue_reference_analysis() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if tg_op = 'INSERT' then
    insert into public.reference_analysis (reference_id, state)
      values (new.id, case when new.import_id is null then 'pending' else 'held' end)
      on conflict do nothing;
  elsif new.url is distinct from old.url or new.file_name is distinct from old.file_name then
    update public.reference_analysis set state = case when state = 'held' then 'held' else 'pending' end,
      error = null, problem = null where reference_id = new.id;
  end if;
  if new.status = 'retired' and (tg_op = 'INSERT' or old.status <> 'retired') then
    new.retired_at := now();
  elsif new.status = 'active' then
    new.retired_at := null;
  end if;
  return new;
end $$;

-- A whole list in one call: new links become held references, the rest are counted as
-- duplicates (the same post already in references, or twice in the list). Members only (checked
-- here: it runs with the definer's rights so it can keep the import's totals), and the references
-- are added in the caller's name. rows: [{url, account, post_type, purpose, why, title}].
-- Returns {import_id, added, duplicates, invalid}.
create function public.import_references(p_side text, p_brand text, p_name text, p_rows jsonb, p_invalid int default 0)
returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  imp bigint;
  n_added int := 0;
  n_bad int := 0;
  n_total int := coalesce(jsonb_array_length(p_rows), 0);
  r jsonb;
  got bigint;
begin
  if not public.is_member() then
    raise exception 'only the team can import references' using errcode = '42501';
  end if;
  if n_total > 5000 then
    raise exception 'at most 5000 links at a time' using errcode = '22023';
  end if;
  if coalesce(p_side, 'inspiration') not in ('ours', 'inspiration') or coalesce(p_brand, 'urban_makers') not in ('urban_makers', 'personal') then
    raise exception 'unknown side or brand' using errcode = '22023';
  end if;
  insert into public.reference_imports (brand, side, name, total, invalid, created_by)
    values (coalesce(p_brand, 'urban_makers'), coalesce(p_side, 'inspiration'), left(coalesce(p_name, ''), 200),
            n_total + coalesce(p_invalid, 0), coalesce(p_invalid, 0), auth.uid())
    returning id into imp;
  for r in select * from jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) loop
    got := null;
    if coalesce(r ->> 'url', '') !~* '^https://[^\s]+$' or length(r ->> 'url') > 2000 then
      n_bad := n_bad + 1;  -- the planner only sends links; anything else is counted as not a link
      continue;
    end if;
    insert into public.reference_videos (brand, side, url, title, why, account, post_type, purpose, import_id, added_by)
      values (coalesce(p_brand, 'urban_makers'), coalesce(p_side, 'inspiration'), r ->> 'url',
              left(coalesce(r ->> 'title', ''), 200), left(coalesce(r ->> 'why', ''), 4000),
              left(coalesce(r ->> 'account', ''), 100),
              case when r ->> 'post_type' in ('reel', 'video', 'carousel', 'image', 'series') then r ->> 'post_type' end,
              case when r ->> 'purpose' in ('design', 'content', 'engagement') then r ->> 'purpose' end, imp, auth.uid())
      on conflict do nothing
      returning id into got;
    if got is not null then n_added := n_added + 1; end if;
  end loop;
  update public.reference_imports set added = n_added, duplicates = n_total - n_added - n_bad,
    invalid = invalid + n_bad where id = imp;
  return jsonb_build_object('import_id', imp, 'added', n_added, 'duplicates', n_total - n_added - n_bad,
                            'invalid', coalesce(p_invalid, 0) + n_bad);
end $$;
revoke execute on function public.import_references(text, text, text, jsonb, int) from public, anon;
grant execute on function public.import_references(text, text, text, jsonb, int) to authenticated;

-- ── Taste reports ─────────────────────────────────────────────────────────────

-- What the whole library says (design, content, engagement) and what to change, written by the
-- Studio machine from every analysed reference. Each comes with a proposed style guide (a draft in
-- style_guides, guide_id) and a short "taste" section for the playbook. Approving the report (owner
-- only) makes that guide the one in force; the Studio machine then writes both into the playbook.
create table public.taste_reports (
  id              bigint generated always as identity primary key,
  brand           text not null default 'urban_makers' check (brand in ('urban_makers', 'personal')),
  version         int not null,
  status          text not null default 'draft' check (status in ('draft', 'approved', 'archived', 'rejected')),
  report          text not null default '',          -- the whole report, Markdown (also kept in the Studio folder)
  sections        jsonb not null default '{}',       -- the same, structured, for the planner's page
  taste_section   text not null default '',          -- the playbook's "Taste" section
  guide_id        bigint references public.style_guides (id) on delete set null,  -- the proposed guide (draft)
  reference_ids   bigint[] not null default '{}',    -- the analysed references it read
  usage           jsonb,
  created_at      timestamptz not null default now(),
  approved_by     uuid references public.profiles (id) on delete set null,
  approved_at     timestamptz,
  unique (brand, version)
);

-- Approving a report puts its style guide in force (the guide trigger archives the previous one)
-- and archives the previous approved report; rejecting it rejects its draft guide too.
create function public.decide_taste_report() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.status = 'approved' and old.status is distinct from 'approved' then
    update public.taste_reports set status = 'archived'
      where brand = new.brand and status = 'approved' and id <> new.id;
    new.approved_by := coalesce(auth.uid(), new.approved_by);
    new.approved_at := now();
    if new.guide_id is not null then
      update public.style_guides set status = 'active' where id = new.guide_id and status = 'draft';
    end if;
  elsif new.status = 'rejected' and old.status is distinct from 'rejected' and new.guide_id is not null then
    update public.style_guides set status = 'rejected' where id = new.guide_id and status = 'draft';
  end if;
  return new;
end $$;
create trigger taste_reports_decide before update on public.taste_reports
  for each row execute function public.decide_taste_report();

-- ── Jobs ──────────────────────────────────────────────────────────────────────

-- 'refs': the owner starts, pauses, resumes or stops analysing the library (payload {action:
-- analyse | pause | resume | stop | retry, mode: sample | all, n, estimate_usd}); 'taste': the owner
-- asks for a taste report. Both spend, so only the owner may ask.
alter table public.jobs drop constraint jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
  check (kind in ('organise', 'develop', 'music', 'make', 'style', 'place', 'ideas', 'carousel', 'topics',
                  'refs', 'taste'));
create policy "owner queues reference analysis" on public.jobs for insert to authenticated
  with check (public.is_owner() and kind in ('refs', 'taste') and runner = 'laptop' and state = 'queued');

-- ── Row level security ────────────────────────────────────────────────────────

alter table public.reference_imports enable row level security;
alter table public.taste_reports enable row level security;

create policy "members read imports" on public.reference_imports for select to authenticated using (public.is_member());
create policy "members import" on public.reference_imports for insert to authenticated with check (public.is_member());
create policy "members read taste reports" on public.taste_reports for select to authenticated using (public.is_member());
-- Only the owner approves or rejects a report; the Studio machine (service role) writes drafts.
create policy "owner decides taste reports" on public.taste_reports for update to authenticated
  using (public.is_owner()) with check (public.is_owner());

alter publication supabase_realtime add table public.reference_imports, public.taste_reports;

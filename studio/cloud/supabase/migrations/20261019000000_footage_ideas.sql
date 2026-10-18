-- Ideas from new footage: after a shoot day's uploads, the Studio machine has Claude
-- suggest posts from the new clips (into each project's "From your footage" arc) and add
-- the footage to the posts already planned. It does that by itself once uploads go quiet;
-- this adds the planner's button for it (job kind 'ideas') and a plain "needs a retake"
-- flag on clips, so the planner only warns about footage that isn't usable.

-- New job: 'ideas' (the Studio machine reads a project's footage of the last 14 days;
-- any member may ask).
alter table public.jobs drop constraint jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
  check (kind in ('organise', 'develop', 'music', 'make', 'style', 'place', 'ideas'));

create policy "members queue footage ideas" on public.jobs for insert to authenticated
  with check (public.is_member() and kind = 'ideas' and runner = 'laptop' and state = 'queued');

-- usable: Claude's (or Bryan's) call that the clip can be used at all. Written by the
-- Studio machine. flagged: the only quality warning to show on cards and thumbnails:
-- the clip needs a retake or isn't usable ('check' is a note for the detail view).
alter table public.clips add column usable boolean;
alter table public.clips add column flagged boolean
  generated always as (coalesce(verdict = 'retake', false) or coalesce(not usable, false)) stored;

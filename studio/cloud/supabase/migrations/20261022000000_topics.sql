-- Suggested topics: once a week (and when the month's mix falls behind) the Studio machine has
-- Claude suggest a few posts for the month's gaps (topics.py): Know-how and Ideas carousels in
-- the standing Topics project, or a Reel from footage where Bryan already explained it on site.
-- Each is a proposed idea Bryan Go's, Parks or Dismisses as usual. This adds what the planner
-- needs for it: the "Suggest topics" button (job kind 'topics'), why Studio suggested each idea,
-- and the team's list of questions clients ask (the best source of topics).

-- Why Studio suggested an idea (null for every other idea). Written by the Studio machine:
-- {why, source: transcript | question | calendar | reference | basics | plan, clip_id, at,
--  question_id, run, suggested_at}. "why" is the short "why now" the planner shows on the card.
create function public.valid_suggestion(s jsonb) returns boolean
language sql immutable as $$
  select s is null or (jsonb_typeof(s) = 'object'
    and coalesce(jsonb_typeof(s -> 'why'), 'string') = 'string'
    and length(coalesce(s ->> 'why', '')) <= 300
    and coalesce(jsonb_typeof(s -> 'source'), 'string') = 'string'
    and length(coalesce(s ->> 'source', '')) <= 40)
$$;

alter table public.ideas add column suggestion jsonb check (public.valid_suggestion(suggestion));
create index ideas_suggested on public.ideas (id) where suggestion is not null;

-- "Suggest topics" in the planner: any member may ask (one Claude call, a few cents), one at a
-- time. The Studio machine runs it (it has the footage transcripts) and writes the ideas.
create policy "members queue topics" on public.jobs for insert to authenticated
  with check (public.is_member() and kind = 'topics' and runner = 'laptop' and state = 'queued'
              and not exists (select 1 from public.jobs j where j.kind = 'topics' and j.state in ('queued', 'running')));

-- ── Questions clients ask ─────────────────────────────────────────────────────

-- What clients really ask Bryan and Michelle ("Can I hack this wall in a resale flat?"): the
-- strongest source of Know-how posts. Members add and edit; Studio reads the open ones when it
-- suggests topics and links the post it suggested (idea_id). status: open (not used yet) |
-- suggested (a post was suggested from it, waiting for Bryan) | used (that post is Go or made) |
-- passed (Bryan dismissed that post; "Suggest again" puts the question back to open).
create table public.client_questions (
  id          bigint generated always as identity primary key,
  question    text not null check (length(trim(question)) between 3 and 500),
  note        text not null default '' check (length(note) <= 2000),     -- context, what Bryan answered
  asked_by    text not null default '' check (length(asked_by) <= 120),  -- who asked: "a client at handover"
  status      text not null default 'open' check (status in ('open', 'suggested', 'used', 'passed')),
  idea_id     bigint references public.ideas (id) on delete set null,
  created_by  uuid references public.profiles (id) on delete set null default auth.uid(),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index client_questions_status on public.client_questions (status, id);
create index client_questions_idea on public.client_questions (idea_id);

create trigger client_questions_touch before update on public.client_questions
  for each row execute function public.touch_updated_at();

-- Who added it is whoever is signed in (the Studio machine never adds questions). A question
-- whose post was deleted is open again.
create function public.stamp_client_question() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if tg_op = 'INSERT' then
    if auth.role() <> 'service_role' then
      new.created_by := auth.uid();
    end if;
  elsif new.idea_id is null and old.idea_id is not null and new.status = 'suggested' then
    new.status := 'open';
  end if;
  return new;
end $$;
create trigger client_questions_stamp before insert or update on public.client_questions
  for each row execute function public.stamp_client_question();

-- The question follows Bryan's call on the post made from it: Go or made = used, dismissed =
-- passed, otherwise suggested (Parked or undecided).
create function public.follow_question_idea() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  update public.client_questions q
     set status = case when new.status = 'dismissed' then 'passed'
                       when new.decision = 'go' or new.status = 'made' or new.production is not null then 'used'
                       else 'suggested' end
   where q.idea_id = new.id
     and q.status is distinct from (case when new.status = 'dismissed' then 'passed'
                                         when new.decision = 'go' or new.status = 'made' or new.production is not null then 'used'
                                         else 'suggested' end);
  return null;
end $$;
create trigger ideas_follow_questions after update of decision, status, production on public.ideas
  for each row execute function public.follow_question_idea();

alter table public.client_questions enable row level security;

create policy "members read client questions" on public.client_questions for select to authenticated
  using (public.is_member());
create policy "members add client questions" on public.client_questions for insert to authenticated
  with check (public.is_member());
create policy "members edit client questions" on public.client_questions for update to authenticated
  using (public.is_member()) with check (public.is_member());
-- Removing one is the owner's call, or the person who added it.
create policy "owner or author deletes client questions" on public.client_questions for delete to authenticated
  using (public.is_owner() or created_by = auth.uid());

alter publication supabase_realtime add table public.client_questions;

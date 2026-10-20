-- Michelle can Go and Park (Bryan, 2026-10-20: "Michelle can Go/Park and send back").
-- The team may now set an idea's call to Go, Parked or back to undecided. Dismiss (status),
-- Make this (production, status 'made') and everything the Studio machine writes stay the
-- owner's (or the service role's), as before. Only the guard on ideas changes: the same
-- function, redefined, so the trigger from 20261015000000_planning.sql keeps calling it.
-- Until this runs, a team member's Go or Park is refused by the database (42501) and the
-- planner says so.

create or replace function public.guard_idea_owner_fields() returns trigger
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
  if new.status is distinct from old.status
     or new.production is distinct from old.production then
    raise exception 'only the owner can dismiss or make an idea' using errcode = '42501';
  end if;
  -- Go / Park / undecided: anyone on the team, on an idea that isn't dismissed or made yet.
  if new.decision is distinct from old.decision
     and (new.decision not in ('proposed', 'go', 'parked')
          or old.status <> 'new' or old.production is not null) then
    raise exception 'only the owner can change the call on this idea' using errcode = '42501';
  end if;
  return new;
end $$;

comment on function public.guard_idea_owner_fields() is
  'Ideas: the team may Go / Park / undo (status new, not made); dismiss, make and the Studio machine''s fields stay the owner''s.';

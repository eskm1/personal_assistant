-- Hook scores and Bryan's voice (Bryan approved 2026-10-20, from instagram-agent-skill, MIT).
-- Where Claude writes an idea's hook (plan-organise, plan-develop, ideas from footage, suggested
-- topics) it now writes three options from 26 hook formulas; the Studio machine scores them
-- (hookscore, studio/wording.py) and keeps the strongest as the hook. The planner's idea page
-- shows the score beside the Hook, with the other two one tap away.
-- And the Edge Functions' prompts get Bryan's voice file and the "never write these" list, which
-- the Studio machine pushes into the context table like brands.md and the playbook.

-- [{text, formula, score}] best first: text the hook, formula the formula's name ("" when none),
-- score 0-100 (null for a Chinese hook, or not scored yet). Claude's own options arrive without
-- "score"; the Studio machine adds it (cloud_sync.score_hooks).
create function public.valid_hook_options(o jsonb) returns boolean
language sql immutable as $$
  select o is null or (jsonb_typeof(o) = 'array' and jsonb_array_length(o) <= 6
    and not exists (select 1 from jsonb_array_elements(o) e
                    where jsonb_typeof(e) <> 'object'
                       or coalesce(jsonb_typeof(e -> 'text'), 'null') <> 'string'
                       or length(e ->> 'text') > 1000
                       or length(coalesce(e ->> 'formula', '')) > 80
                       or coalesce(jsonb_typeof(e -> 'score'), 'number') not in ('number', 'null')))
$$;

alter table public.ideas add column if not exists hook_options jsonb
  check (public.valid_hook_options(hook_options));

comment on column public.ideas.hook_options is
  'Three hook options [{text, formula, score}] best first; the hook is usually the first. Written by Claude (Edge Functions, the Studio machine); scored by the Studio machine. Anyone who can edit the idea may swap the hook.';

-- The context the Edge Functions read: also Bryan's voice file (voice_file.py) and the never list
-- with his own allowlist (wording.never_text), pushed by the Studio machine.
alter table public.context drop constraint if exists context_key_check;
alter table public.context add constraint context_key_check
  check (key in ('brands', 'playbook', 'voice', 'wording'));

-- Carousels: an idea can be a Reel or a carousel (ideas.format = 'carousel', allowed since the
-- content plan). A carousel needs no shoot. Claude writes its slides (plan-develop, the same
-- "Refine with Claude" / storyboard job a Reel gets), the team edits them line by line in the
-- planner and picks each slide's picture (a still from Urban Makers' own footage, a photo
-- uploaded from the phone, or text only), and "Make this" asks the Studio machine to lay it
-- out (job kind 'carousel'), which lands in Review like a draft Reel.

-- The slides, in order: [{key, role, heading, body, image}]
--   role:  cover (the hook) | point | ask (the closing call to action)
--   image: {source: text | clip | photo | concept, clip_id, at, photo_id, thumb_path, note}
--          clip = a still from one of the library's clips (clip_id, at seconds); photo = a photo
--          uploaded to the Studio machine (photo_id); concept = an AI concept image (a later
--          update; never shown as built work). thumb_path = a preview in the "thumbs" bucket;
--          note = what picture would suit (Claude's suggestion).
-- Anyone who can edit ideas edits them; null = not written yet (a Reel always has null).
create function public.valid_slides(s jsonb) returns boolean
language sql immutable as $$
  select s is null or (jsonb_typeof(s) = 'array' and jsonb_array_length(s) <= 12 and not exists (
    select 1 from jsonb_array_elements(s) x
    where jsonb_typeof(x) <> 'object'
       or coalesce(x ->> 'role', '') not in ('cover', 'point', 'ask')
       or jsonb_typeof(x -> 'heading') is distinct from 'string'
       or length(x ->> 'heading') > 200
       or coalesce(jsonb_typeof(x -> 'body'), 'string') <> 'string'
       or length(coalesce(x ->> 'body', '')) > 800
       or coalesce(jsonb_typeof(x -> 'key'), 'string') <> 'string'
       or length(coalesce(x ->> 'key', '')) > 40
       or (x ? 'image' and jsonb_typeof(x -> 'image') <> 'null' and (
            jsonb_typeof(x -> 'image') <> 'object'
            or coalesce(x -> 'image' ->> 'source', '') not in ('text', 'clip', 'photo', 'concept')
            or coalesce(jsonb_typeof(x -> 'image' -> 'clip_id'), 'number') not in ('number', 'null')
            or coalesce(jsonb_typeof(x -> 'image' -> 'at'), 'number') not in ('number', 'null')
            or coalesce(jsonb_typeof(x -> 'image' -> 'photo_id'), 'string') not in ('string', 'null')
            or length(coalesce(x -> 'image' ->> 'photo_id', '')) > 64
            or coalesce(jsonb_typeof(x -> 'image' -> 'note'), 'string') not in ('string', 'null')
            or length(coalesce(x -> 'image' ->> 'note', '')) > 300
            or coalesce(jsonb_typeof(x -> 'image' -> 'thumb_path'), 'string') not in ('string', 'null')
            or (x -> 'image' ->> 'thumb_path' is not null
                and (x -> 'image' ->> 'thumb_path' !~ '^(clips|takes|stills|photos)/[A-Za-z0-9_.-]+$'
                     or length(x -> 'image' ->> 'thumb_path') > 200)))))
  )
$$;

alter table public.ideas add column slides jsonb check (public.valid_slides(slides));

-- "Make this" on a carousel: only the owner spends, as with Reels ('make'), and only for an
-- idea that is a carousel. The Studio machine lays it out and writes ideas.production.
create policy "owner queues carousels" on public.jobs for insert to authenticated
  with check (public.is_owner() and kind = 'carousel' and runner = 'laptop' and state = 'queued'
              and exists (select 1 from public.ideas i where i.id = idea_id and i.format = 'carousel'));

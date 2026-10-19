-- Review's clean-up (Bryan approved 2026-10-19): a clip archived in Review's Library is hidden from
-- the planner's footage pickers too (From library, a slide's picture from footage, the shoot plan's
-- "Needs a home" tray). The Studio machine writes it with each clip's summary (cloud_sync.py), like
-- the rest of the clips table; null = in the library. Nothing else changes: its takes stay where
-- they are, and Review's Undo brings it back.
alter table public.clips add column if not exists archived_at timestamptz;

comment on column public.clips.archived_at is
  'Set by the Studio machine when the clip is archived in Review''s Library (null = in the library). Pickers leave archived clips out.';

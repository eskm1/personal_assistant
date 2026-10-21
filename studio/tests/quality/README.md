# Studio edit-quality suites

These hold the edit quality Bryan signed off on 2026-10-21 ("I don't hear any random sentences be cut off ...
at least now the hygiene factors are there"). Run them before any push that touches the editor,
transcription, the fix rounds or rendering.

```
cd studio
python tests/quality/run.py                 # all suites, 4 at a time; one line each, exit 1 on any failure
python tests/quality/run.py cuts ending     # just these
python tests/quality/run.py --jobs 2 --keep # fewer at once; keep the logs and folders
python tests/quality/suite_cuts.py          # one suite on its own (STUDIO_QUALITY_KEEP=1 keeps its folder)
```

How long: about 6,800 CPU-seconds in all, most of it real renders (HyperFrames overlays and 1080x1920
encodes); `finalcheck` is over half of it and runs its renders one after another. On a 4-vCPU box that is
about 30 minutes at best; measured 2026-10-21: 43 minutes, all green, on a 4-vCPU box shared with other
work (load ~15). Each line shows the suite's wall time and its CPU time (with its ffmpeg and Chrome).

Use Studio's own Python (the server's or a session's venv: the one with `requirements.txt`). Needs ffmpeg
with the flite filter (Ubuntu's ffmpeg has it), node, and HyperFrames installed in `render/` (`npm ci`), as
the editor itself does. Nothing reaches Claude, Gemini, Epidemic, Telegram or Supabase: Claude, music and
Whisper are stubbed in each suite, `studio/.env` is never read (`common.py`), keys are blank, and each suite
works in a temporary folder of its own (`STUDIO_ROOT` included), removed after.

## What each suite holds

| Suite | Checks | What it holds |
|---|---|---|
| `suite_cuts.py` | 41 | Cut points follow the voice, not Whisper's early word ends: no word's ending clipped, no cut inside a sounding word, decays kept. Fillers and stutters dropped. Whole sentences: a gap cut from inside a sentence comes back; a long one is asked of Claude (the join guard); Bryan's note asking for a cut keeps it cut. The clipped-word check catches and fixes an old draft on re-render. |
| `suite_ending.py` | 71 | Pauses inside a sentence stay, pauses between sentences shorten to a breath. Whole sentences at the end and at every join to another shot. Patches refuse to trim a pause inside a sentence or move a cut mid-sentence. The close: a beat of the last shot, the end card (ask, handle, 1.8 s), music fading, no caption or card over it, Resolve gets it. Start over (a fresh plan with Bryan's notes), the RELEASE bump that rejects and restarts the waiting drafts once, the playbook migration. |
| `suite_finalcheck.py` | 59 | The listen-back and watch-through (`final_check.py`): each flag fires on a crafted bad render (word cut short, said twice, a broken sentence, ending mid-sentence; a title over the face, a flash, too fast to read, the same animation twice, still on at the close) and stays quiet on a clean one; the fixes come in the read's shape and Studio's own rounds make them; a failure never breaks a draft; the check runs alongside Claude's read on a ~45 s Reel within its budget. |
| `suite_patches.py` | 59 | The fix rounds as patches (`fix_actions.py`): caption word, drop words, restore words, card timing, hook text, then one re-plan for what no patch can do; judged fix by fix; the cost cap; a round that makes things worse is dropped; what's left says why. The spell-check and its guard (never a number, a new name or a change of meaning), the glossary reaching Whisper and the captions, a carousel slide patch. |
| `suite_selffix.py` | 67 | Studio fixes its own read before Bryan sees a draft: one round, Bryan told once at the end, the fixed render delivered, rules learned into the playbook, the cost recorded apart. Send-back notes (editing becomes a rule, taste doesn't). Edge cases: a round that isn't better, one that fails, rounds off, the round limit, a failed read, a re-render, a restart mid-rounds. Carousels through the same loop. |
| `suite_edit_units.py` | 70 | Unit checks: the sentence guard (stray last word, mid-sentence start, dead air), caption case and timing, cards clear of the caption line, the bundled fonts and their licences, the music ducking expression, learned rules (count, cap, retire, merge, forget, Bryan's part untouched), notes sorted, the trend, scores. |
| `suite_whisper_prompt.py` | 10 | The glossary's hotwords fit Whisper's 448-token window (the 2026-10-21 "maximum decoding length must be > 0" failure), and an overflow retries without them instead of failing the edit. |

Real ffmpeg, HyperFrames and OpenCV renders in cuts, ending, finalcheck, patches (part 1) and selffix (parts
1, 2, 4); the loops' edge cases use quick fake renders. `--skip-real` on patches and selffix skips the real
renders (fewer checks: the runner always runs them in full).

## The test media

Made at run time by `media.py`, nothing large committed:

- **Word-by-word speech** (cuts, ending, finalcheck, patches): each word spoken by flite on its own so its
  true start and end are known to the sample, laid out as sentences with pauses, decays on long words, a
  room floor; "Whisper" timings that end 60-200 ms early, as the real one does. Same words and seeds as
  the originals, so the timings are identical.
- **Phrase speech** (selffix): flite phrases recorded quietly (about -41 LUFS) over hiss and a mains hum.
- **A person** on a plain background, face high or low: `fixtures/person.jpg`, cut from OpenCV's sample
  photo `messi5.jpg` (Apache-2.0, `fixtures/person.LICENSE.txt`). OpenCV's face detector needs a real
  face, so this 25 KB picture is the one media file committed.
- B-roll (ffmpeg's test pattern), a warm chord bed for music, tones.

`fixtures/before/` holds Studio as it was before two fixes (xz-compressed, 77 KB): `editor.py` before
bde7803 (cuts follow the voice) and before 576f763 (gentle cuts, the ending), and the playbook seed before
576f763. The suites load them to show the test media still reproduces the bug each fix is for, as the
"old draft" a re-render must repair, and as the old playbook the migration rewrites. If a later change
elsewhere stops the old editor importing, fix the import in the suite, don't drop the check.

## Adding to it

A new check goes in the suite that owns the behaviour; raise its count in `run.py`'s `SUITES` (the runner
fails a suite that makes fewer checks than listed). A new suite: `suite_<name>.py`, `from common import ...`
first, `finish()` last, then a line in `SUITES` and in the table above.

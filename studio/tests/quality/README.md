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

How long: about 7,800 CPU-seconds in all, most of it real renders (HyperFrames overlays and 1080x1920
encodes); `finalcheck` is over half of it and runs its renders one after another. On a 4-vCPU box that is
about 30 minutes at best; measured 2026-10-21: 43 minutes, all green, on a 4-vCPU box shared with other
work (load ~15), and 39 minutes with slideshow added (7,775 CPU-seconds); 2026-10-22, with pip added: 44 minutes, all green, 561 checks (9,270 CPU-seconds; pip about 33 minutes of it, its seven real renders). 2026-10-22, with pictures added: 74 minutes, all green, 624 checks (15,421 CPU-seconds; pictures about 42 minutes of it, its nine real drafts). 2026-10-22, with look added (Site Notes the default in every suite): 72 minutes, all green, 685 checks (15,261 CPU-seconds; look about 20 minutes of it). The suites need about 2.5 GB of free disk at once (finalcheck and ending about 800 and 700 MB each). Each line shows the suite's wall time and its CPU time (with its ffmpeg and Chrome).

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
| `suite_edit_units.py` | 83 | Unit checks: the sentence guard and clause ends (a whole clause before "and"/"so"/"but"/"then" may end what is said) (stray last word, mid-sentence start, dead air), caption case and timing, cards clear of the caption line, the bundled fonts and their licences, the music ducking expression, learned rules (count, cap, retire, merge, forget, Bryan's part untouched), notes sorted, the trend, scores. |
| `suite_slideshow.py` | 97 | Slideshow Reels (`slideshow.py`): a carousel's slides made into a 9:16 Reel with music. Owner only, one per carousel; the slides laid out again for the Reel (no Swipe cue or counter, a carousel's own layout exactly as before), a missing photo falls back to the carousel's slide; times from the words, slides in order, crossfades, a slow push-in inside the safe zones, the end card and the music fade, read in frames and sound. Studio's read and the watch-through (no listen-back: no voice), send back (order, times, a track link), a music-level patch, Start over, posting as a Reel, the carousel untouched, a personal post with no music or end card, a release's restart. |
| `suite_pip.py` | 80 | Voice-overs (`voiceover.py`): the footage told apart (a talking head from B-roll, a few words in the background still B-roll; the studio from the site by its sound, its camera, its setting and its time, and by sound and camera alone), when a post is a voice-over (never on site, never without B-roll, off by setting); the plan split into his voice and the B-roll laid over it, Bryan's notes read; a real voice-over draft: 9:16 H.264/AAC, him full screen for his first and last sentence, the site footage with his rounded window in a bottom corner in between (each shot its own clip from its in-point, changing at clause ends, starting at its words), captions beside the window, the site's sound under him, the read told it's deliberate; his cut identical to a plain talking-head edit of the same footage; "Full screen here" and "Move me to the right" notes, a re-render keeping it, the read's layout fix; on site edited exactly as the editor before did (plan, cut, picture); Resolve (B-roll on V2, the window layer); the mix's site level; the docs, help and planner wording. |
| `suite_pictures.py` | 67 | Picture inserts (`pictures.py`): Bryan's notes asking for an image, picture or photo read (and notes about the video's picture not), the prompt (real things pictures, processes and numbers drawings) and schema only when pictures are on; a real draft with a picture: one 9:16 Gemini call with the rules (no words, faces, logos, places) and one check, timed to his words clear of the opening and the close, the frame at its time shows it with the Concept mark and a push-in, its cost in usage and the cost line, "What the editor did", the read told, captions lit, the watch-through quiet; a re-render and a fix round reuse it (no new call), Resolve's layer renders it again from the kept picture; the cap (per Reel length, per draft version across Studio's rounds, 4 by default); Gemini down, its filter or the check failing twice: a drawing instead, said once, never paid twice; "use an image of a decorative glass panel here instead of the animation" makes it a picture at that moment (a send-back, and the read's patch); no key, the setting off or a Finished homes post: drawings. Gemini is a local fake server counting its calls. |
| `suite_look.py` | 55 | Site Notes, the Reels' graphics look (Bryan, 2026-10-22: "go with A for now"), the default: the setting (`STUDIO_TITLE_STYLE`, gallery one setting away, a draft keeps its look), Archivo bundled with its licence and the only family any page loads (the overlay, the drawn inserts, a whole draft), captions 74 px 750 white with the soft shadow and the key word in brass, the hook on stacked dark labels from x 64 with no shade over the frame, a callout's brass label and its leader, cards as a dark panel over the footage (digits and bars, "Now" / "The plan" pairs, points), never full screen; the end card over the last frame under the dark shade; captions beside a voice-over's window at 60 px; read in the pages and in rendered pixels. Gallery and classic pages byte for byte as before (`editor.py` at c0aa84f), a gallery insert pixel for pixel as before (`motion.py` and `motion.css` at c0aa84f); the picture's "Concept · not a photo" label, full bleed. One plan made in both looks: the same timing, the card over the footage against gallery's full-screen card, the end card's picture still there, the watch-through's boxes. The release: the waiting Reels made again (not started over), carousels left, Telegram once. `STUDIO_LOOK_STRIP=<folder>` saves a frame strip of each look. |
| `suite_wiki.py` | 136 | Urban Makers' wiki as a source of topics (`wiki.py`, `topics.py`): a local fake of the wiki's REST API with the read-only role's rules (exactly the six named columns, `select=*` refused with 403, the token and anon key in headers only); one read a day kept on disk (0600), the copy used when the wiki is down, offline or answers nonsense, an hour's rest after a failure; the sanitiser on invented snippets (project and client links with their text, image placeholders, internal and commercial lines and callouts, money words and signs, addresses, postcodes, unit numbers, Blk, towns, phones, emails, people, brands and brand sections, case studies, checklists, unverified facts, directories) with the trade facts kept; the token and the key never in a log line, an error or the copy on disk; a whole topics run with a stub Claude: the digest in the prompt within its budget, no dropped text in it, the suggestion's article on `ideas.suggestion` and its facts in the purpose Write slides reads, a made-up article claimed by nothing, the least used article first next time, one waiting not offered again. |
| `suite_whisper_prompt.py` | 10 | The glossary's hotwords fit Whisper's 448-token window (the 2026-10-21 "maximum decoding length must be > 0" failure), and an overflow retries without them instead of failing the edit. |

Real ffmpeg, HyperFrames and OpenCV renders in cuts, ending, finalcheck, patches (part 1), selffix (parts
1, 2, 4) and slideshow (every render, about 9 minutes on its own); the loops' edge cases use quick fake renders. `--skip-real` on patches and selffix skips the real
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
- **A voice-over's footage** (pip): the person and flite phrases "in the studio" (a still camera, a plain wall,
  clean sound) and "on site" (the picture shaking, a busy background, the site's noise about 11 dB under the
  voice), and three B-roll clips (three test patterns, a site rumble, one with a few words in the background).
- **A talking head with known word edges** (pictures): the ending suite's speech over the person, and the fake
  Gemini's pictures (warm flutes, a colour of each brief's own, drawn with numpy).
- **A carousel's slides** (slideshow): 1080x1350 PNGs drawn with Pillow, one flat colour each so a frame tells
  which slide is up, a white frame at the slide's margins and a dark line at its edge (to see nothing is cropped).

`fixtures/before/` holds Studio as it was before five changes (xz-compressed, about 220 KB): `editor.py` before
bde7803 (cuts follow the voice), before 576f763 (gentle cuts, the ending), at ca2fe96 (before voice-overs) and at
c0aa84f (before Site Notes, with `motion.py` and `motion.css`: the gallery look's pages must stay as they were), the
playbook seed before 576f763, and `carousel.py` before 9d1f570 (slideshow Reels). The suites load them to show the test media
still reproduces the bug each fix is for, as the "old draft" a re-render must repair, as the old playbook
the migration rewrites, as the carousel layout a carousel must still get now the Reel has its own, and as the
editor whose on-site edits a voice-over change must leave exactly as they were, and as the gallery look Site Notes must leave exactly as it was. If a
later change elsewhere stops an old file importing, fix the import in the suite, don't drop the check.

## Adding to it

A new check goes in the suite that owns the behaviour; raise its count in `run.py`'s `SUITES` (the runner
fails a suite that makes fewer checks than listed). A new suite: `suite_<name>.py`, `from common import ...`
first, `finish()` last, then a line in `SUITES` and in the table above.

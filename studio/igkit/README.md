# igkit

Parts of **instagram-agent-skill** by Jake Schincariol (MIT, see `LICENSE`),
https://github.com/Jakeschincariol/instagram-agent-skill, commit `d03c56b` (2026-09-13).
Copied in on 2026-10-20 with Bryan's OK. Studio imports these as a module; nothing here
runs on its own, calls the network or needs a package.

| File | From | Changed |
| --- | --- | --- |
| `humanize.py` | `skills/ig-human/humanize.py` | see below |
| `detect.py` | `skills/ig-human/detect.py` | no |
| `slop.json` | `skills/ig-human/slop.json` | no |
| `hookscore.py` | `skills/ig-reel/hookscore.py` | no |
| `beats.py` | `skills/ig-reel/beats.py` | no |
| `hooks.json` | `skills/ig-reel/hooks.json` | no |
| `caption.py` | `skills/ig-caption/caption.py` | no |
| `voice_template.md` | `templates/voice.md` | no |

The prompt wording in `studio/wording.py` (hook options, the caption's first line, the rewrite
pass) is adapted from the skills' `SKILL.md` files (ig-human, ig-reel, ig-caption).

Changes to `humanize.py`, so it can run on everything Studio writes without changing meaning:

- the word swaps (the lexical pass) are optional: Studio runs `humanize(text, lex, lexical=False)`
  and only reports slop; Claude rewrites flagged text itself (`studio/wording.py`);
- hashtags and @mentions are protected like links;
- Chinese punctuation stays Chinese: a curly quote, an em dash (破折号) or an ellipsis next to a
  Chinese character is left alone;
- a dash between two numbers becomes a hyphen ("3—4 weeks" -> "3-4 weeks"), never "3, 4";
- a dash at the start or end of a line goes instead of turning into a comma;
- a zero-width joiner inside an emoji and the tags of a flag emoji are kept;
- arrows (→) are left alone;
- the comma clean-up only touches the commas the pass put in.

Studio's own words (trade terms Bryan really uses, such as "hack" for hacking tiles) go in
`Studio/humanize_allow.txt`, not in `slop.json`, so this folder can be updated from upstream.

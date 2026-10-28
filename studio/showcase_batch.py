"""
Re-date, re-title and re-caption a set of planned carousels, and send the ready ones to Studio as photo carousels
(the content lead's showcase brief, 2026-10-28). Run on the laptop, with studio\\.env's Supabase service key:

    .\\venv\\Scripts\\python showcase_batch.py spec.json --folder "<path>"           # shows what it would change
    .\\venv\\Scripts\\python showcase_batch.py spec.json --folder "<path>" --apply   # changes it, queues the carousels

Both runs list the photos' folder in OneDrive (with rclone, as Studio does) and match every carousel's photos first,
so a missing photo shows before anything changes.

spec.json (batches/ holds them; a folder that names a place is passed with --folder, never written in the repo):

    {"project": "Finished homes", "year": 2026, "folder": "<the folder's full path from OneDrive's top>",
     "ideas": [{"match": "words in the idea's title now", "date": "2026-11-02",
                "title": "One long room, one long table", "grid": "Week 1 is the top row, ...",
                "caption": "...", "photos": ["Other 12", "11"], "cover": "One long room, one long table.",
                "folder": "<another folder's full path>"}]}

- match: picks the idea by its title now (case ignored); defaults to "title". Every entry must match exactly one
  idea in the project and no idea twice, or nothing changes.
- date and title: the posting date and the title, written "Mon 2 Nov · One long room, one long table".
- grid: the idea's grid comment (a comment starting "Grid"), changed in place, or added.
- caption, photos, cover: with photos, the carousel is queued like "Make this" (a carousel job): Studio fetches
  the photos from the folder in that order (showcase.py: "11" at the folder's top, "Other 12" a picture called 12
  in the subfolder starting "Other"), slide 1 gets the cover line, and the draft lands in Review. Nothing is posted
  or scheduled. An idea already made is left as it is.
"""
import argparse
import json
import re
import sys
from datetime import date

import cloud_sync
import showcase

GRID = re.compile(r"^\s*grid\b", re.I)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def stamp(day: date, title: str) -> str:
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')} · {title.strip()}"


def plan(spec: dict, ideas: list[dict], comments: dict[int, list[dict]]) -> tuple[list[dict], list[str]]:
    """Each entry with its idea and the changes; the problems that stop it."""
    out, problems, taken = [], [], {}
    for n, e in enumerate(spec.get("ideas") or [], 1):
        want = norm(e.get("match") or e.get("title"))
        hits = [i for i in ideas if want and want in norm(i["title"])]
        if len(hits) != 1:
            problems.append(f"entry {n} ({e.get('title')!r}): {len(hits)} ideas match {want!r}")
            continue
        idea = hits[0]
        if idea["id"] in taken:
            problems.append(f"entry {n} and entry {taken[idea['id']]} both pick idea {idea['id']}")
        taken[idea["id"]] = n
        try:
            day = date.fromisoformat(e["date"])
        except (KeyError, ValueError):
            problems.append(f"entry {n}: the date isn't YYYY-MM-DD")
            continue
        sets = {"post_on": day.isoformat(), "title": stamp(day, e.get("title") or idea["title"])}
        if isinstance(e.get("caption"), str):
            sets["caption"] = e["caption"].strip()
        make = None
        if e.get("photos"):
            try:
                make = showcase.check_spec({"folder": e.get("folder") or spec.get("folder"), "files": e["photos"],
                                            "cover": e.get("cover") or ""})
            except showcase.ShowcaseError as err:
                problems.append(f"entry {n}: {err}")
            if idea.get("format") != "carousel":
                problems.append(f"entry {n}: idea {idea['id']} isn't a carousel")
            if idea.get("status") == "made" or idea.get("production"):
                make = None  # already made: left as it is
        grid = [c for c in comments.get(idea["id"], []) if GRID.match(c["body"] or "")]
        out.append({"n": n, "idea": idea, "sets": sets, "grid": e.get("grid"), "grid_old": grid, "make": make})
    return out, problems


def check_photos(rows: list[dict]) -> list[str]:
    """Every carousel's photos found in OneDrive, each folder listed once; prints what each matched. Returns the
    problems (a missing or ambiguous photo, a folder not found, rclone missing here)."""
    import onedrive
    makes = [r for r in rows if r["make"]]
    if not makes:
        return []
    if onedrive.rclone_path() is None:
        return ["the photos can't be checked on this computer (rclone isn't installed); run it where Studio's "
                "OneDrive connection is, or add --no-photo-check"]
    problems, listed = [], {}
    for r in makes:
        folder = r["make"]["folder"]
        try:
            if folder not in listed:
                listed[folder] = showcase.listing(folder)
            found = showcase.match(listed[folder], r["make"]["files"])
            print(f"  #{r['idea']['id']} photos: " + ", ".join(str(e.get("Path") or e["Name"]) for e in found))
        except (showcase.ShowcaseError, onedrive.OneDriveError) as err:
            problems.append(f"entry {r['n']} (#{r['idea']['id']}): {err}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--apply", action="store_true", help="make the changes and queue the carousels")
    ap.add_argument("--folder", help="the OneDrive folder of the photos, its full path (instead of the spec's; it may "
                                     "name a place)")
    ap.add_argument("--no-photo-check", action="store_true", help="don't list OneDrive first (Studio still checks)")
    args = ap.parse_args()
    spec = json.loads(open(args.spec, encoding="utf-8").read())
    if args.folder:
        spec["folder"] = args.folder
    api = cloud_sync.Client()
    projects = [p for p in api.select("projects", {"select": "id,name"}) if norm(p["name"]) == norm(spec["project"])]
    if len(projects) != 1:
        print(f"Found {len(projects)} projects called {spec['project']!r}; nothing changed.")
        return 1
    pid = projects[0]["id"]
    ideas = api.select("ideas", {"project_id": f"eq.{pid}", "status": "neq.dismissed",
                                 "select": "id,title,format,status,decision,post_on,caption,production"})
    comments: dict[int, list[dict]] = {}
    if ideas:
        ids = ",".join(str(i["id"]) for i in ideas)
        for c in api.select("comments", {"idea_id": f"in.({ids})", "select": "id,idea_id,body"}):
            comments.setdefault(c["idea_id"], []).append(c)
    rows, problems = plan(spec, ideas, comments)
    for r in rows:
        i = r["idea"]
        print(f"#{i['id']}: {i['title']!r} -> {r['sets']['title']!r}, posting {r['sets']['post_on']}"
              f"{', caption replaced' if 'caption' in r['sets'] else ''}"
              f"{', grid comment ' + ('changed' if r['grid_old'] else 'added') if r['grid'] else ''}"
              f"{', then made from ' + str(len(r['make']['files'])) + ' photos' if r['make'] else ''}")
    left = [i for i in ideas if i["id"] not in {r["idea"]["id"] for r in rows}]
    if left:
        print(f"Not in the spec (left as they are): {', '.join('#' + str(i['id']) for i in left)}")
    if not problems and not args.no_photo_check:
        print("\nThe photos in OneDrive:")
        problems += check_photos(rows)
    if problems:
        print("\nNothing changed:\n- " + "\n- ".join(problems))
        return 1
    if not args.apply:
        print("\nNothing changed (add --apply to make these changes).")
        return 0
    for r in rows:
        iid = r["idea"]["id"]
        api.update("ideas", {"id": f"eq.{iid}"}, r["sets"])
        if r["grid"]:
            if r["grid_old"]:
                api.update("comments", {"id": f"eq.{r['grid_old'][0]['id']}"}, {"body": r["grid"]})
            else:
                api.insert("comments", {"idea_id": iid, "body": r["grid"], "author_name": "Content plan"}, returning=False)
    queued = 0
    for r in rows:
        if r["make"]:
            api.insert("jobs", {"kind": "carousel", "runner": "laptop", "state": "queued", "idea_id": r["idea"]["id"],
                                "payload": {"caption": r["sets"].get("caption", r["idea"].get("caption") or ""),
                                            "photos": r["make"]}}, returning=False)
            queued += 1
    print(f"\nDone: {len(rows)} ideas changed, {queued} carousels sent to Studio. They land in Review in a few minutes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

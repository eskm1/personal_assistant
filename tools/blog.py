"""
Blog publishing for bryanjlum.com.

The site is a static Astro blog: one markdown file per post in
src/content/blog/, deployed by Cloudflare Pages on every push to main. So
"publishing a post" = committing a markdown file to the GitHub repo over the
Contents API — the same shape as the second-brain inbox capture, but gated
behind the confirmation flow because a confirmed publish goes live on the
public site a minute or two later.

Voice matching is the model's job, not this module's: the tool descriptions
and system prompt tell it to read recent posts first and mirror their style.
This module only validates mechanics (filename slug, frontmatter fields) so a
bad file can't break the Cloudflare build.
"""
import base64
import json
import re
from urllib.parse import quote

import requests

from config import (
    BLOG_GITHUB_TOKEN,
    BLOG_REPO,
    BLOG_BRANCH,
    BLOG_POSTS_PATH,
    BLOG_BASE_URL,
)
from tools import pending

_API = "https://api.github.com"

_FILENAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.(md|mdx)$")
_PUBDATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# How many posts list_blog_posts fetches frontmatter for (each is one API call).
_LIST_FETCH_CAP = 15


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {BLOG_GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _file_url(filename: str) -> str:
    return f"{_API}/repos/{BLOG_REPO}/contents/{quote(BLOG_POSTS_PATH, safe='/')}/{quote(filename)}"


def _post_url(filename: str) -> str:
    slug = filename.rsplit(".", 1)[0]
    return f"{BLOG_BASE_URL.rstrip('/')}/blog/{slug}/"


def _token_missing() -> str | None:
    if not BLOG_GITHUB_TOKEN:
        return "⚠️ Blog publishing is not set up yet — BLOG_GITHUB_TOKEN is missing on the server."
    return None


def _parse_frontmatter(markdown: str) -> tuple[dict, str]:
    """Split a post into (frontmatter dict, body). Simple 'key: value' lines only —
    enough to validate and summarise, not a YAML parser."""
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", markdown, re.DOTALL)
    if not m:
        return {}, markdown
    fields = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip("'\"")
    return fields, m.group(2)


def _get_existing(filename: str) -> tuple[str | None, str | None]:
    """Return (sha, decoded content) of an existing post, or (None, None) if absent."""
    r = requests.get(_file_url(filename), headers=_headers(), params={"ref": BLOG_BRANCH}, timeout=15)
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    payload = r.json()
    return payload["sha"], base64.b64decode(payload["content"]).decode("utf-8")


# ── Read tools ────────────────────────────────────────────────────────────────

def list_blog_posts() -> str:
    """List published blog posts with title, date and tags (newest first)."""
    missing = _token_missing()
    if missing:
        return missing
    try:
        r = requests.get(
            f"{_API}/repos/{BLOG_REPO}/contents/{quote(BLOG_POSTS_PATH, safe='/')}",
            headers=_headers(),
            params={"ref": BLOG_BRANCH},
            timeout=15,
        )
        r.raise_for_status()
        files = [f for f in r.json() if f["type"] == "file" and f["name"].endswith((".md", ".mdx"))]
        if not files:
            return "No blog posts found."

        posts = []
        skipped = len(files) - _LIST_FETCH_CAP if len(files) > _LIST_FETCH_CAP else 0
        for f in files[:_LIST_FETCH_CAP]:
            _sha, content = _get_existing(f["name"])
            fm, _body = _parse_frontmatter(content or "")
            posts.append({
                "file": f["name"],
                "title": fm.get("title", "(no title)"),
                "date": fm.get("pubDate", ""),
                "tags": fm.get("tags", ""),
                "draft": fm.get("draft", "") == "true",
            })
        posts.sort(key=lambda p: p["date"], reverse=True)

        lines = []
        for p in posts:
            flag = "  [DRAFT — not on the live site]" if p["draft"] else ""
            tags = f"  tags: {p['tags']}" if p["tags"] else ""
            lines.append(f"• {p['date']}  {p['title']}{flag}\n  file: {p['file']}{tags}\n  {_post_url(p['file'])}")
        out = "Blog posts (newest first):\n" + "\n".join(lines)
        if skipped:
            out += f"\n(+{skipped} older posts not shown)"
        return out
    except Exception as e:
        return f"Blog list error: {e}"


def read_blog_post(filename: str) -> str:
    """Read the full markdown source (frontmatter + body) of one post."""
    missing = _token_missing()
    if missing:
        return missing
    try:
        _sha, content = _get_existing(filename)
        if content is None:
            return f"No blog post named '{filename}'. Use list_blog_posts to see filenames."
        return f"File: {filename}\nLive URL: {_post_url(filename)}\n\n{content}"
    except Exception as e:
        return f"Blog read error: {e}"


# ── Publish (staged behind the confirmation gate) ─────────────────────────────

def _do_publish(filename: str, markdown: str, update: bool) -> str:
    try:
        sha, _existing = _get_existing(filename)
        if sha and not update:
            return (
                f"❌ Not published: '{filename}' already exists. "
                "Pick a different filename, or call publish_blog_post with update=true to replace it."
            )
        action = "update" if sha else "add"
        put_body = {
            "message": f"blog: {action} {filename} via Telegram",
            "content": base64.b64encode(markdown.encode("utf-8")).decode("ascii"),
            "branch": BLOG_BRANCH,
        }
        if sha:
            put_body["sha"] = sha
        r = requests.put(_file_url(filename), headers=_headers(), data=json.dumps(put_body), timeout=15)
        r.raise_for_status()

        fm, _body = _parse_frontmatter(markdown)
        if fm.get("draft", "") == "true":
            return (
                f"✅ Committed '{filename}' as a DRAFT (draft: true) — it will NOT appear on the "
                f"live site until the draft flag is removed with an update."
            )
        return (
            f"✅ Published '{fm.get('title', filename)}'\n"
            f"{_post_url(filename)}\n"
            "Cloudflare Pages is rebuilding — the post is live in a minute or two."
        )
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status in (401, 403):
            return "❌ Publish failed: the blog GitHub token is missing, expired, or lacks write access."
        return f"❌ Publish failed: {e}"
    except Exception as e:
        return f"❌ Publish failed: {e}"


def publish_blog_post(filename: str, markdown: str, update: bool = False) -> str:
    """Validate a post and stage the publish for confirmation (does not commit yet)."""
    missing = _token_missing()
    if missing:
        return missing

    filename = filename.strip()
    if not _FILENAME_RE.match(filename):
        return (
            f"Invalid filename '{filename}'. Use a lowercase kebab-case slug ending in .md, "
            "e.g. 'learning-to-pace.md' — it becomes the URL /blog/learning-to-pace/."
        )

    fm, body = _parse_frontmatter(markdown)
    problems = []
    if not fm:
        problems.append("missing frontmatter block (--- ... ---) at the top")
    else:
        for field in ("title", "description", "pubDate"):
            if not fm.get(field):
                problems.append(f"frontmatter is missing '{field}'")
        if fm.get("pubDate") and not _PUBDATE_RE.match(fm["pubDate"]):
            problems.append(f"pubDate must be YYYY-MM-DD (got '{fm['pubDate']}')")
    if not body.strip():
        problems.append("the post body is empty")
    if problems:
        return "Not staged — fix these first: " + "; ".join(problems) + ". A broken file would fail the site build."

    is_draft = fm.get("draft", "") == "true"
    verb = "UPDATE existing post" if update else "publish NEW post"
    visibility = "as a hidden draft (draft: true)" if is_draft else f"LIVE at {_post_url(filename)}"
    summary = (
        f"Blog: {verb} '{fm.get('title', filename)}' ({filename}, {len(body)} chars) — {visibility}"
    )
    return pending.stage(summary, lambda: _do_publish(filename, markdown, update))


# ── Tool definitions (Anthropic schema) ──────────────────────────────────────

TOOL_DEFS = [
    {
        "name": "list_blog_posts",
        "description": (
            "List posts on Bryan's personal blog (bryanjlum.com) with title, date, tags and filename, "
            "newest first. Use this first when drafting a new post, to pick recent posts to read for voice."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_blog_post",
        "description": (
            "Read the full markdown source (frontmatter + body) of one blog post by filename. "
            "ALWAYS read 1-2 recent posts before drafting a new one, and mirror their voice, "
            "frontmatter format, and one-sentence-per-line style exactly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Post filename from list_blog_posts, e.g. 'the-goal-is-to-keep-playing.md'"},
            },
            "required": ["filename"],
        },
    },
    {
        "name": "publish_blog_post",
        "description": (
            "Publish a post to bryanjlum.com by committing a markdown file to the site's GitHub repo "
            "(Cloudflare Pages then deploys it live within minutes). Does NOT commit immediately — it stages "
            "the publish and returns a summary for Bryan to confirm, like other destructive actions. "
            "Only call this AFTER Bryan has seen the complete final draft in chat and approved it. "
            "The markdown must be the FULL file: frontmatter (title, description, pubDate, tags) then the body. "
            "Set draft: true in the frontmatter to commit without it appearing on the live site. "
            "Use update=true only when deliberately replacing an existing post."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Lowercase kebab-case slug ending in .md — becomes the URL, e.g. 'learning-to-pace.md' → /blog/learning-to-pace/",
                },
                "markdown": {
                    "type": "string",
                    "description": "Complete file content: '---' frontmatter block, then the post body in markdown.",
                },
                "update": {
                    "type": "boolean",
                    "description": "Set true to replace an existing post (edit/typo fix). Default false = new posts only.",
                    "default": False,
                },
            },
            "required": ["filename", "markdown"],
        },
    },
]

DISPATCH = {
    "list_blog_posts": list_blog_posts,
    "read_blog_post": read_blog_post,
    "publish_blog_post": publish_blog_post,
}

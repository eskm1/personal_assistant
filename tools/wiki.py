"""
Urban Makers internal wiki (knowledge base) integration.

Mirrors the capabilities of the quote engine's in-app Claude agent: search, list,
read, and edit wiki articles. It writes to the SAME Supabase project as the umcpm
tools (rlcigpzbjuigpjnewimm), reusing UMCPM_SUPABASE_URL / UMCPM_SERVICE_KEY.

Like the web app (which shows an Accept/Discard preview), edits here are NOT
immediate: create/append STAGE the change via the confirmation gate and only write
after Bryan confirms. Every write also appends a wiki_revisions snapshot, matching
dbSaveWikiArticle in the app.

Ava (the Telegram bot) only READS the wiki directly; writing is Bob's domain, so
the write tools are exposed under Bob's namespace in tools/bob.py
(bob_create_wiki_article / bob_append_wiki_article) and attributed to him.
"""
import re
import json
import secrets
import requests

from config import UMCPM_SUPABASE_URL, UMCPM_SERVICE_KEY, UMCPM_BASE_URL
from tools import pending

_REST = f"{UMCPM_SUPABASE_URL.rstrip('/')}/rest/v1"

# The 12 seeded domains (see migration 015_wiki.sql). Free-text is allowed, but
# steer new articles onto these so the wiki stays organised.
DOMAINS = [
    "Carpentry", "Tiling & waterproofing", "Electrical", "Plumbing",
    "Painting & finishes", "Permits & authorities", "Pricing & estimation",
    "Site management", "Suppliers & materials", "Client management",
    "Standards & specs", "General",
]

EDITOR = "Bob (via Ava)"


def _headers(extra: dict | None = None) -> dict:
    h = {
        "apikey": UMCPM_SERVICE_KEY,
        "Authorization": f"Bearer {UMCPM_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _safe_term(term: str) -> str:
    """Strip characters that would corrupt a PostgREST or=(...) filter."""
    return re.sub(r'[,()"\*%]', " ", term).strip()


def _wiki_slug(title: str) -> str:
    """Mirror the app's _wikiSlugify: lowercased, hyphenated, capped, random suffix."""
    base = re.sub(r"[^a-z0-9]+", "-", (title or "").lower().strip()).strip("-")[:60] or "article"
    return f"{base}-{secrets.token_hex(3)}"


def _wiki_link(article_id: str) -> str:
    """Deep link to a wiki article. wiki.html reads ?article=<id> (the UUID) and opens it."""
    return f"{UMCPM_BASE_URL.rstrip('/')}/wiki?article={article_id}"


def _resolve_domain(domain: str) -> str:
    if not domain:
        return "General"
    for d in DOMAINS:
        if d.lower() == domain.lower():
            return d
    return domain  # allow free-text, but exact known names win


def _write_revision(article: dict) -> None:
    requests.post(
        f"{_REST}/wiki_revisions",
        headers=_headers(),
        data=json.dumps({
            "article_id": article["id"],
            "title": article.get("title"),
            "body_md": article.get("body_md"),
            "domain": article.get("domain"),
            "tags": article.get("tags", []),
            "editor": EDITOR,
        }),
        timeout=15,
    )


# ── Read tools ────────────────────────────────────────────────────────────────

def search_wiki(query: str, max_results: int = 10) -> str:
    """Search the wiki by keyword across title and body."""
    try:
        term = _safe_term(query)
        if not term:
            return "Please give a search term."
        params = {
            "select": "id,title,slug,domain,tags,body_md",
            "or": f"(title.ilike.*{term}*,body_md.ilike.*{term}*)",
            "order": "updated_at.desc",
            "limit": str(min(max_results, 20)),
        }
        r = requests.get(f"{_REST}/wiki_articles", headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return f"No wiki articles matching '{query}'."

        lines = []
        for a in rows:
            body = (a.get("body_md") or "").replace("\n", " ")
            snippet = (body[:120] + "…") if len(body) > 120 else body
            tags = f" #{' #'.join(a['tags'])}" if a.get("tags") else ""
            lines.append(
                f"ID: {a['id']}\n"
                f"Title: {a['title']}  [{a.get('domain', '')}]{tags}\n"
                f"Snippet: {snippet}"
            )
        return "\n---\n".join(lines)
    except Exception as e:
        return f"Wiki search error: {e}"


def list_wiki_articles(domain: str = "") -> str:
    """List wiki articles (optionally filtered to one domain)."""
    try:
        params = {
            "select": "id,title,slug,domain,status",
            "order": "domain.asc,title.asc",
            "limit": "200",
        }
        if domain.strip():
            params["domain"] = f"eq.{_resolve_domain(domain)}"
        r = requests.get(f"{_REST}/wiki_articles", headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return "No wiki articles found." if not domain else f"No wiki articles in '{domain}'."

        lines = []
        for a in rows:
            flag = "" if a.get("status") == "published" else " (draft)"
            lines.append(f"• [{a['domain']}] {a['title']}{flag}\n  {_wiki_link(a['id'])}")
        return "Wiki articles:\n" + "\n".join(lines)
    except Exception as e:
        return f"Wiki list error: {e}"


def read_wiki_article(article_id: str) -> str:
    """Read the full Markdown body of one wiki article by ID."""
    try:
        params = {"select": "id,title,slug,domain,tags,body_md,status", "id": f"eq.{article_id}", "limit": "1"}
        r = requests.get(f"{_REST}/wiki_articles", headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return f"No wiki article with ID {article_id}."
        a = rows[0]
        tags = f"\nTags: {', '.join(a['tags'])}" if a.get("tags") else ""
        return (
            f"Title: {a['title']}\n"
            f"Domain: {a.get('domain', '')}  |  Status: {a.get('status', '')}{tags}\n"
            f"Link: {_wiki_link(a['id'])}\n\n"
            f"{a.get('body_md', '') or '(empty)'}"
        )
    except Exception as e:
        return f"Wiki read error: {e}"


# ── Write executors (run only after confirmation) ─────────────────────────────

def _do_create_wiki_article(title: str, body_md: str, domain: str, tags: list[str], status: str) -> str:
    try:
        row = {
            "title": title,
            "slug": _wiki_slug(title),
            "domain": _resolve_domain(domain),
            "tags": tags,
            "body_md": body_md,
            "status": status if status in ("draft", "published") else "draft",
            "author": EDITOR,
            "updated_by": EDITOR,
        }
        r = requests.post(
            f"{_REST}/wiki_articles",
            headers=_headers({"Prefer": "return=representation"}),
            data=json.dumps(row),
            timeout=15,
        )
        r.raise_for_status()
        article = r.json()[0]
        _write_revision(article)
        return f"✅ Wiki article created: '{title}'\n{_wiki_link(article['id'])}"
    except Exception as e:
        return f"Wiki create error: {e}"


def _do_append_wiki_article(article_id: str, markdown: str) -> str:
    try:
        params = {"select": "id,title,slug,domain,tags,body_md", "id": f"eq.{article_id}", "limit": "1"}
        r = requests.get(f"{_REST}/wiki_articles", headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return f"No wiki article with ID {article_id}."
        current = rows[0]

        new_body = (current.get("body_md") or "").rstrip() + "\n\n" + markdown.strip() + "\n"
        r2 = requests.patch(
            f"{_REST}/wiki_articles?id=eq.{article_id}",
            headers=_headers({"Prefer": "return=representation"}),
            data=json.dumps({"body_md": new_body, "updated_by": EDITOR}),
            timeout=15,
        )
        r2.raise_for_status()
        article = r2.json()[0]
        _write_revision(article)
        return f"✅ Appended to '{article['title']}'\n{_wiki_link(article['id'])}"
    except Exception as e:
        return f"Wiki append error: {e}"


# ── Write tools (stage via the confirmation gate) ─────────────────────────────

def create_wiki_article(
    title: str,
    body_md: str,
    domain: str = "General",
    tags: str = "",
    status: str = "draft",
) -> str:
    """Stage a NEW wiki article for confirmation (does not save immediately)."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    dom = _resolve_domain(domain)
    summary = f"Create wiki article '{title}' in [{dom}] ({len(body_md)} chars, status={status})"
    return pending.stage(summary, lambda: _do_create_wiki_article(title, body_md, dom, tag_list, status))


def append_to_wiki_article(article_id: str, markdown: str) -> str:
    """Stage an APPEND to an existing wiki article for confirmation (does not save immediately)."""
    summary = f"Append {len(markdown)} chars to wiki article {article_id}"
    return pending.stage(summary, lambda: _do_append_wiki_article(article_id, markdown))


# ── Tool definitions (Anthropic schema) ──────────────────────────────────────
# Read tools go straight into Ava's toolset; the write tools are picked up and
# re-exposed by tools/bob.py under Bob's names.

READ_TOOL_DEFS = [
    {
        "name": "search_wiki",
        "description": (
            "Search the Urban Makers internal wiki (knowledge base) by keyword across title and body. "
            "Use this FIRST before creating a new article, to find an existing one to append to."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword(s) to search"},
                "max_results": {"type": "integer", "description": "Max results (1-20, default 10)", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_wiki_articles",
        "description": "List wiki articles with review links, optionally filtered to one domain.",
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": f"Optional domain filter. Known domains: {', '.join(DOMAINS)}", "default": ""},
            },
        },
    },
    {
        "name": "read_wiki_article",
        "description": "Read the full content of one wiki article by its ID. Read the target before proposing an append so your addition fits.",
        "input_schema": {
            "type": "object",
            "properties": {
                "article_id": {"type": "string", "description": "Article ID from search_wiki/list_wiki_articles"},
            },
            "required": ["article_id"],
        },
    },
]

WRITE_TOOL_DEFS = [
    {
        "name": "bob_create_wiki_article",
        "description": (
            "Ask Bob to add a NEW article to the Urban Makers wiki. Does NOT save immediately — it stages the "
            "article and returns a summary. Show Bryan the summary and, once he confirms, call "
            "confirm_pending_action to save. Prefer appending to an existing article (search_wiki first) "
            "over creating duplicates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Article title"},
                "body_md": {"type": "string", "description": "Article body in Markdown"},
                "domain": {"type": "string", "description": f"One of: {', '.join(DOMAINS)}", "default": "General"},
                "tags": {"type": "string", "description": "Comma-separated tags (optional)", "default": ""},
                "status": {"type": "string", "description": "'draft' (default) or 'published'", "enum": ["draft", "published"], "default": "draft"},
            },
            "required": ["title", "body_md"],
        },
    },
    {
        "name": "bob_append_wiki_article",
        "description": (
            "Ask Bob to APPEND Markdown to an existing Urban Makers wiki article. Does NOT save immediately — "
            "it stages the change and returns a summary. Show Bryan the summary and, once he confirms, call "
            "confirm_pending_action."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "article_id": {"type": "string", "description": "Target article ID from search_wiki/list_wiki_articles"},
                "markdown": {"type": "string", "description": "Markdown to append to the article body"},
            },
            "required": ["article_id", "markdown"],
        },
    },
]

READ_DISPATCH = {
    "search_wiki": search_wiki,
    "list_wiki_articles": list_wiki_articles,
    "read_wiki_article": read_wiki_article,
}

WRITE_DISPATCH = {
    "bob_create_wiki_article": create_wiki_article,
    "bob_append_wiki_article": append_to_wiki_article,
}

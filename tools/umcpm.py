"""
Urban Makers (umcpm) integration — create projects and draft quotes,
and return reviewable deep links.

Talks directly to the umcpm Supabase project via its PostgREST API using a
service-role key (server-side only, bypasses RLS). The key lives in .env as
UMCPM_SERVICE_KEY and must never be committed or exposed client-side.
"""
import re
import json
import requests

from config import UMCPM_SUPABASE_URL, UMCPM_SERVICE_KEY, UMCPM_BASE_URL

_REST = f"{UMCPM_SUPABASE_URL.rstrip('/')}/rest/v1"


def _headers(extra: dict | None = None) -> dict:
    h = {
        "apikey": UMCPM_SERVICE_KEY,
        "Authorization": f"Bearer {UMCPM_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _slugify(name: str) -> str:
    """Mirror the app's slug logic: keep [A-Za-z0-9-_ space], spaces -> '-', cap 80."""
    cleaned = re.sub(r"[^a-zA-Z0-9\-_ ]", "", name)
    slug = re.sub(r"\s+", "-", cleaned.strip())
    return slug[:80] or "project"


def _project_link(slug: str, quote_id: str | None = None) -> str:
    base = UMCPM_BASE_URL.rstrip("/")
    if quote_id:
        return f"{base}/projects/{slug}/quote/{quote_id}"
    return f"{base}/projects/{slug}/quote"


def _blank_quote_data(project_name: str, client_name: str, proj_type: str,
                      address: str, quote_no: str = "") -> dict:
    """Minimal quote-builder state. applyState() fills every other field via fallbacks."""
    return {
        "version": 1,
        "quoteStatus": "draft",
        "items": [],
        "meta": {
            "projectName": project_name or "",
            "clientName": client_name or "",
            "projType": proj_type or "",
            "projectAddr": address or "",
            "quoteNo": quote_no or "",
        },
    }


def _find_project(query: str) -> dict | None:
    """Find a single active project by name or slug (case-insensitive partial match)."""
    term = query.strip()
    params = {
        "select": "id,slug,project_name,client_name,stage,status",
        "or": f"(project_name.ilike.*{term}*,slug.ilike.*{term}*)",
        "order": "created_at.desc",
        "limit": "5",
    }
    r = requests.get(f"{_REST}/projects", headers=_headers(), params=params, timeout=15)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return None
    # Prefer an exact (case-insensitive) name/slug match if present
    for row in rows:
        if row.get("project_name", "").lower() == term.lower() or row.get("slug", "").lower() == term.lower():
            return row
    return rows[0]


def _insert_draft_quote(project_id: str, quote_name: str, data: dict) -> dict:
    body = {
        "project_id": project_id,
        "name": quote_name,
        "data": data,
        "is_approved": False,
    }
    r = requests.post(
        f"{_REST}/quotes",
        headers=_headers({"Prefer": "return=representation"}),
        data=json.dumps(body),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()[0]


# ── Tool functions ────────────────────────────────────────────────────────────

def create_umcpm_project(
    project_name: str,
    client_name: str = "",
    proj_type: str = "",
    address: str = "",
    designer: str = "",
    notes: str = "",
) -> str:
    """Create a new umcpm project with an initial blank draft quote."""
    try:
        slug = _slugify(project_name)

        project_body = {
            "slug": slug,
            "project_name": project_name,
            "client_name": client_name or None,
            "proj_type": proj_type or None,
            "address": address or None,
            "designer": designer or None,
            "notes": notes or None,
            "stage": "to_quote",
        }

        r = requests.post(
            f"{_REST}/projects",
            headers=_headers({"Prefer": "return=representation"}),
            data=json.dumps(project_body),
            timeout=15,
        )

        # Retry once with a suffixed slug if there's a unique-slug collision
        if r.status_code == 409:
            project_body["slug"] = f"{slug}-2"
            slug = project_body["slug"]
            r = requests.post(
                f"{_REST}/projects",
                headers=_headers({"Prefer": "return=representation"}),
                data=json.dumps(project_body),
                timeout=15,
            )

        r.raise_for_status()
        project = r.json()[0]

        data = _blank_quote_data(project_name, client_name, proj_type, address)
        quote = _insert_draft_quote(project["id"], "Draft quote", data)

        link = _project_link(project["slug"], quote["id"])
        return (
            f"✅ Created project '{project_name}' with a draft quote.\n"
            f"Review/edit the quote here:\n{link}"
        )

    except Exception as e:
        return f"umcpm create project error: {e}"


def add_draft_quote(project_query: str, quote_name: str = "Draft quote") -> str:
    """Add a new blank draft quote to an existing umcpm project (found by name or slug)."""
    try:
        project = _find_project(project_query)
        if not project:
            return f"No umcpm project found matching '{project_query}'. Try list_umcpm_projects to see names."

        data = _blank_quote_data(
            project.get("project_name", ""),
            project.get("client_name", ""),
            "",
            "",
        )
        quote = _insert_draft_quote(project["id"], quote_name, data)

        link = _project_link(project["slug"], quote["id"])
        return (
            f"✅ Added draft quote '{quote_name}' to '{project['project_name']}'.\n"
            f"Review/edit it here:\n{link}"
        )

    except Exception as e:
        return f"umcpm add draft quote error: {e}"


def list_umcpm_projects(query: str = "", status: str = "active") -> str:
    """List umcpm projects, optionally filtered by a name/slug search term."""
    try:
        params = {
            "select": "id,slug,project_name,client_name,stage",
            "status": f"eq.{status}",
            "order": "created_at.desc",
            "limit": "20",
        }
        if query.strip():
            term = query.strip()
            params["or"] = f"(project_name.ilike.*{term}*,slug.ilike.*{term}*)"

        r = requests.get(f"{_REST}/projects", headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()

        if not rows:
            return "No matching umcpm projects found."

        stage_label = {
            "to_quote": "To quote", "to_close": "To close", "to_start": "To start",
            "ongoing": "Ongoing", "handing_over": "Handing over",
        }
        lines = []
        for p in rows:
            client = f" — {p['client_name']}" if p.get("client_name") else ""
            stage = stage_label.get(p.get("stage", ""), p.get("stage", ""))
            link = f"{UMCPM_BASE_URL.rstrip('/')}/projects/{p['slug']}/quote"
            lines.append(f"• {p['project_name']}{client} [{stage}]\n  {link}")

        return "Projects:\n" + "\n".join(lines)

    except Exception as e:
        return f"umcpm list projects error: {e}"


def get_umcpm_project_link(project_query: str) -> str:
    """Return a reviewable link for an existing umcpm project (its latest quote, if any)."""
    try:
        project = _find_project(project_query)
        if not project:
            return f"No umcpm project found matching '{project_query}'."

        # Find the most recent quote for a deep link; fall back to the quote builder root
        params = {
            "select": "id,created_at",
            "project_id": f"eq.{project['id']}",
            "order": "created_at.desc",
            "limit": "1",
        }
        r = requests.get(f"{_REST}/quotes", headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        quotes = r.json()

        quote_id = quotes[0]["id"] if quotes else None
        link = _project_link(project["slug"], quote_id)
        return f"{project['project_name']}:\n{link}"

    except Exception as e:
        return f"umcpm get link error: {e}"


# ── Tool definitions (Anthropic schema) ──────────────────────────────────────

TOOL_DEFS = [
    {
        "name": "create_umcpm_project",
        "description": (
            "Create a new project in Urban Makers (umcpm) with an initial blank draft quote. "
            "Returns a link to review/edit the draft quote. Use when Bryan wants to start a new project/quotation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "Project name (required)"},
                "client_name": {"type": "string", "description": "Client name", "default": ""},
                "proj_type": {"type": "string", "description": "Project type, e.g. 'Condo', 'HDB', 'Landed'", "default": ""},
                "address": {"type": "string", "description": "Site address", "default": ""},
                "designer": {"type": "string", "description": "Designer name", "default": ""},
                "notes": {"type": "string", "description": "Any notes about the project", "default": ""},
            },
            "required": ["project_name"],
        },
    },
    {
        "name": "add_draft_quote",
        "description": (
            "Add a new blank draft quote to an EXISTING Urban Makers project, found by name or slug. "
            "Returns a link to review/edit it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "project_query": {"type": "string", "description": "Project name or slug to search for"},
                "quote_name": {"type": "string", "description": "Name for the new draft quote", "default": "Draft quote"},
            },
            "required": ["project_query"],
        },
    },
    {
        "name": "list_umcpm_projects",
        "description": "List Urban Makers projects with review links. Optionally filter by a name/slug search term.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional name/slug filter", "default": ""},
                "status": {"type": "string", "description": "'active' (default) or 'archived'", "default": "active"},
            },
        },
    },
    {
        "name": "get_umcpm_project_link",
        "description": "Get a quick reviewable link to an existing Urban Makers project (opens its latest quote).",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_query": {"type": "string", "description": "Project name or slug"},
            },
            "required": ["project_query"],
        },
    },
]

DISPATCH = {
    "create_umcpm_project": create_umcpm_project,
    "add_draft_quote": add_draft_quote,
    "list_umcpm_projects": list_umcpm_projects,
    "get_umcpm_project_link": get_umcpm_project_link,
}

"""
Urban Makers catalogue editing from Telegram — STAGE ONLY into the shared queue.

Mirrors the quote engine's in-app AI catalogue pipeline (PLAN_CATALOGUE, Chunk 4).
Both origins — the in-app assistant and this Telegram bot — write proposals into the
SAME Supabase table, `catalogue_change_requests`. Nothing here applies a change:
`propose_catalogue_edit` inserts a `status='pending'` row that an editor approves &
applies from the /catalogue "Pending changes" panel in the web app.

Reads and writes the SAME Supabase project as the umcpm/wiki tools
(rlcigpzbjuigpjnewimm), reusing UMCPM_SUPABASE_URL / UMCPM_SERVICE_KEY. The service
role bypasses RLS and carries NO per-user JWT identity, so origin='telegram' and
requested_by='telegram:<user_id>' are set EXPLICITLY on every inserted row.

v1 op set (matches catalogue-apply.mjs): add_product, update_product, delete_product,
add_addon, update_addon. Payload column names mirror the catalogue tables:
  products: name, cat, description, unit, base_price  (+ id for update/delete)
  addons:   name, cat, unit, unit_price               (+ id for update)
No variant groups/options or category restructuring in v1.
"""
import json
import requests

from config import UMCPM_SUPABASE_URL, UMCPM_SERVICE_KEY, UMCPM_BASE_URL
from tools.pending import current_conversation

_REST = f"{UMCPM_SUPABASE_URL.rstrip('/')}/rest/v1"

# v1 op scope, kept in lockstep with catalogue-apply.mjs.
PRODUCT_OPS = {"add_product", "update_product", "delete_product"}
ADDON_OPS = {"add_addon", "update_addon"}
VALID_OPS = PRODUCT_OPS | ADDON_OPS


def _headers(extra: dict | None = None) -> dict:
    h = {
        "apikey": UMCPM_SERVICE_KEY,
        "Authorization": f"Bearer {UMCPM_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _requested_by() -> str:
    """The Telegram identity for this proposal. bot.py sets current_conversation to
    '<chat_id>:<user_id>' before the chat loop, so the user id is the last segment."""
    conv = current_conversation.get()
    user_id = conv.rsplit(":", 1)[-1] if conv else ""
    return f"telegram:{user_id}"


def _catalogue_review_link() -> str:
    return f"{UMCPM_BASE_URL.rstrip('/')}/catalogue"


# ── Read helper ───────────────────────────────────────────────────────────────

def read_catalogue(query: str = "") -> str:
    """Load the current catalogue (products, categories, add-ons) so the model can
    reference real ids, names, categories, units and prices before proposing edits."""
    try:
        term = (query or "").strip()

        prod_params = {
            "select": "id,name,cat,unit,base_price,qty_basis",
            "order": "cat.asc,name.asc",
            "limit": "500",
        }
        if term:
            safe = term.replace(",", " ").replace("(", " ").replace(")", " ").replace("*", " ").strip()
            prod_params["or"] = f"(name.ilike.*{safe}*,cat.ilike.*{safe}*)"
        rp = requests.get(f"{_REST}/products", headers=_headers(), params=prod_params, timeout=15)
        rp.raise_for_status()
        products = rp.json()

        rc = requests.get(
            f"{_REST}/categories",
            headers=_headers(),
            params={"select": "name,emoji,notes", "order": "name.asc", "limit": "200"},
            timeout=15,
        )
        rc.raise_for_status()
        categories = rc.json()

        addon_params = {
            "select": "id,name,cat,unit,unit_price",
            "order": "cat.asc,name.asc",
            "limit": "500",
        }
        if term:
            safe = term.replace(",", " ").replace("(", " ").replace(")", " ").replace("*", " ").strip()
            addon_params["or"] = f"(name.ilike.*{safe}*,cat.ilike.*{safe}*)"
        ra = requests.get(f"{_REST}/addons", headers=_headers(), params=addon_params, timeout=15)
        ra.raise_for_status()
        addons = ra.json()

        if not products and not addons:
            scope = f" matching '{query}'" if term else ""
            return f"No catalogue items{scope} found."

        lines = []
        cat_names = ", ".join(c["name"] for c in categories) if categories else "(none)"
        lines.append(f"Categories: {cat_names}")

        lines.append("\nProducts (id | name [cat] — unit @ base_price):")
        if products:
            for p in products:
                lines.append(
                    f"  {p['id']} | {p.get('name', '')} [{p.get('cat', '')}] — "
                    f"{p.get('unit', '')} @ {p.get('base_price', '')}"
                )
        else:
            lines.append("  (none)")

        lines.append("\nAdd-ons (id | name [cat] — unit @ unit_price):")
        if addons:
            for a in addons:
                lines.append(
                    f"  {a['id']} | {a.get('name', '')} [{a.get('cat', '')}] — "
                    f"{a.get('unit', '')} @ {a.get('unit_price', '')}"
                )
        else:
            lines.append("  (none)")

        return "\n".join(lines)
    except Exception as e:
        return f"Catalogue read error: {e}"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _fetch_product(product_id: str) -> dict | None:
    r = requests.get(
        f"{_REST}/products",
        headers=_headers(),
        params={"select": "id,name,cat,description,unit,base_price", "id": f"eq.{product_id}", "limit": "1"},
        timeout=15,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def _fetch_addon(addon_id: str) -> dict | None:
    r = requests.get(
        f"{_REST}/addons",
        headers=_headers(),
        params={"select": "id,name,cat,unit,unit_price", "id": f"eq.{addon_id}", "limit": "1"},
        timeout=15,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None


def _clean(value):
    """Trim strings; leave None as-is so callers can distinguish 'omitted'."""
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return v or None
    return value


def _insert_request(op: str, payload: dict, before: dict | None, summary: str) -> str:
    """Insert one pending catalogue_change_requests row (service role; RLS-bypassing,
    so origin/requested_by are set explicitly) and return a human summary + review link."""
    row = {
        "origin": "telegram",
        "op": op,
        "payload": payload,
        "before": before,
        "summary": summary,
        "status": "pending",
        "requested_by": _requested_by(),
    }
    r = requests.post(
        f"{_REST}/catalogue_change_requests",
        headers=_headers({"Prefer": "return=representation"}),
        data=json.dumps(row),
        timeout=15,
    )
    r.raise_for_status()
    return (
        f"✅ Staged for approval: {summary}\n"
        f"This is NOT applied yet. An editor approves it in the catalogue queue:\n"
        f"{_catalogue_review_link()}"
    )


# ── Propose tool ──────────────────────────────────────────────────────────────

def propose_catalogue_edit(
    op: str,
    name: str = "",
    category: str = "",
    unit: str = "",
    description: str = "",
    base_price: float | None = None,
    unit_price: float | None = None,
    item_id: str = "",
) -> str:
    """Stage a catalogue change into the shared approval queue. Never applies it."""
    try:
        op = (op or "").strip()
        if op not in VALID_OPS:
            return f"Unknown op '{op}'. Valid ops: {', '.join(sorted(VALID_OPS))}."

        name = (name or "").strip()
        category = (category or "").strip()
        unit = (unit or "").strip()
        description = (description or "").strip()
        item_id = (item_id or "").strip()

        # ── Products ──────────────────────────────────────────────────────────
        if op == "add_product":
            if not name:
                return "add_product needs a product name."
            payload = {
                "name": name,
                "cat": category or "Unassigned",
                "unit": unit or "lot",
                "base_price": float(base_price) if base_price is not None else 0,
            }
            if description:
                payload["description"] = description
            summary = f"Add product '{name}' [{payload['cat']}] — {payload['unit']} @ {payload['base_price']}"
            return _insert_request(op, payload, None, summary)

        if op == "update_product":
            if not item_id:
                return "update_product needs the product's item_id (use read_catalogue to find it)."
            before = _fetch_product(item_id)
            if not before:
                return f"No product with id '{item_id}'. Use read_catalogue to find the correct id."
            payload = {"id": item_id}
            if name:              payload["name"] = name
            if category:          payload["cat"] = category
            if unit:              payload["unit"] = unit
            if description:       payload["description"] = description
            if base_price is not None: payload["base_price"] = float(base_price)
            if len(payload) == 1:
                return "update_product: give at least one field to change (name/category/unit/description/base_price)."
            changes = ", ".join(f"{k}={v}" for k, v in payload.items() if k != "id")
            summary = f"Update product '{before.get('name', item_id)}' ({item_id}): {changes}"
            return _insert_request(op, payload, before, summary)

        if op == "delete_product":
            if not item_id:
                return "delete_product needs the product's item_id (use read_catalogue to find it)."
            before = _fetch_product(item_id)
            if not before:
                return f"No product with id '{item_id}'. Use read_catalogue to find the correct id."
            payload = {"id": item_id}
            summary = f"Delete product '{before.get('name', item_id)}' [{before.get('cat', '')}] ({item_id})"
            return _insert_request(op, payload, before, summary)

        # ── Add-ons ───────────────────────────────────────────────────────────
        if op == "add_addon":
            if not name:
                return "add_addon needs an add-on name."
            payload = {
                "name": name,
                "cat": category or None,
                "unit": unit or "lot",
                "unit_price": float(unit_price) if unit_price is not None else 0,
            }
            summary = f"Add add-on '{name}' [{category or ''}] — {payload['unit']} @ {payload['unit_price']}"
            return _insert_request(op, payload, None, summary)

        if op == "update_addon":
            if not item_id:
                return "update_addon needs the add-on's item_id (use read_catalogue to find it)."
            before = _fetch_addon(item_id)
            if not before:
                return f"No add-on with id '{item_id}'. Use read_catalogue to find the correct id."
            payload = {"id": item_id}
            if name:                   payload["name"] = name
            if category:               payload["cat"] = category
            if unit:                   payload["unit"] = unit
            if unit_price is not None:  payload["unit_price"] = float(unit_price)
            if len(payload) == 1:
                return "update_addon: give at least one field to change (name/category/unit/unit_price)."
            changes = ", ".join(f"{k}={v}" for k, v in payload.items() if k != "id")
            summary = f"Update add-on '{before.get('name', item_id)}' ({item_id}): {changes}"
            return _insert_request(op, payload, before, summary)

        return f"Unhandled op '{op}'."
    except Exception as e:
        return f"Catalogue propose error: {e}"


# ── Tool definitions (Anthropic schema) ──────────────────────────────────────

TOOL_DEFS = [
    {
        "name": "read_catalogue",
        "description": (
            "Read the current Urban Makers catalogue: products (id, name, category, unit, base_price), "
            "the category list, and add-ons (id, name, category, unit, unit_price). "
            "ALWAYS call this before propose_catalogue_edit so you use the real item id and current values. "
            "Optionally pass a search term to narrow to matching product/add-on names or categories."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional name/category filter", "default": ""},
            },
        },
    },
    {
        "name": "propose_catalogue_edit",
        "description": (
            "Stage a catalogue change for approval. It does NOT apply the change — it inserts a pending "
            "proposal into the shared review queue that an editor approves & applies on the /catalogue page. "
            "Call read_catalogue FIRST to get the correct item_id and current values. "
            "v1 ops: add_product, update_product, delete_product, add_addon, update_addon. "
            "For update_/delete_ ops you MUST pass item_id (from read_catalogue). "
            "Products use base_price; add-ons use unit_price. After staging, show Bryan the summary and the "
            "review link — never claim the change is live until an editor approves it in the app."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "description": "The change operation",
                    "enum": ["add_product", "update_product", "delete_product", "add_addon", "update_addon"],
                },
                "name": {"type": "string", "description": "Item name (required for add_product/add_addon; optional new name on update)", "default": ""},
                "category": {"type": "string", "description": "Category name (must match an existing category where possible)", "default": ""},
                "unit": {"type": "string", "description": "Pricing unit, e.g. 'sqft', 'm run', 'lot', 'each'", "default": ""},
                "description": {"type": "string", "description": "Product description (products only)", "default": ""},
                "base_price": {"type": "number", "description": "Product base price (products only)"},
                "unit_price": {"type": "number", "description": "Add-on unit price (add-ons only)"},
                "item_id": {"type": "string", "description": "Existing product/add-on id (REQUIRED for update_*/delete_* ops; get it from read_catalogue)", "default": ""},
            },
            "required": ["op"],
        },
    },
]

DISPATCH = {
    "read_catalogue": read_catalogue,
    "propose_catalogue_edit": propose_catalogue_edit,
}

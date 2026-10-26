"""
What the APIs actually cost: every Claude call records its token usage and price, and
every AI concept picture Gemini draws (concepts.py, kind 'image') its price, so the
dashboard can show the cost per clip, idea run and draft, and a monthly total, with the
pictures next to Claude.

Prices are US$ per million tokens (Anthropic's list prices). Thinking tokens are
billed as output and are already included in output_tokens. A picture costs
settings.IMAGE_COST_USD (Google's list price per image).
"""
import logging
import sqlite3

log = logging.getLogger("studio.usage")

# model: (input, output, cache read, cache write) in US$ per million tokens
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-opus-4-8": (5.00, 25.00, 0.50, 6.25),  # a possible server-side fallback model
}
DEFAULT_PRICE = PRICES["claude-opus-5-5"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id            INTEGER PRIMARY KEY,
    at            TEXT NOT NULL,
    kind          TEXT NOT NULL,     -- tag | ideas | edit | music | review | ... | image (a concept picture)
    ref           TEXT,              -- clip:12, production:3, ideas:<batch>
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cache_read    INTEGER,
    cache_write   INTEGER,
    cost_usd      REAL
);
CREATE INDEX IF NOT EXISTS usage_ref ON usage (ref);
"""


def cost(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
    price_in, price_out, price_read, price_write = next(
        (p for name, p in PRICES.items() if model and model.startswith(name)), DEFAULT_PRICE)
    return (input_tokens * price_in + output_tokens * price_out
            + cache_read * price_read + cache_write * price_write) / 1_000_000


def record(kind: str, ref: str, response) -> float:
    """Log one API response's usage. Never raises: cost tracking must not break work."""
    try:
        import catalog
        u = response.usage
        tokens = dict(input_tokens=getattr(u, "input_tokens", 0) or 0,
                      output_tokens=getattr(u, "output_tokens", 0) or 0,
                      cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                      cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0)
        model = getattr(response, "model", "") or ""
        usd = cost(model, **tokens)
        conn = catalog.connect()
        try:
            conn.executescript(_SCHEMA)
            conn.execute("""INSERT INTO usage (at, kind, ref, model, input_tokens, output_tokens, cache_read,
                                cache_write, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (catalog.now(), kind, ref, model, tokens["input_tokens"], tokens["output_tokens"],
                          tokens["cache_read"], tokens["cache_write"], usd))
            conn.commit()
        finally:
            conn.close()
        return usd
    except Exception as e:
        log.warning("Couldn't record API usage for %s: %s", ref, e)
        return 0.0


def record_image(ref: str, model: str, meta: dict | None, usd: float) -> float:
    """Log one picture Gemini drew (usageMetadata from its answer). Never raises."""
    return record_gemini("image", ref, model, meta, usd)


def record_gemini(kind: str, ref: str, model: str, meta: dict | None, usd: float) -> float:
    """Log one Gemini call at a set price (a picture, or a YouTube reference watched: kind 'style',
    so it counts toward the reference budget), with its tokens from usageMetadata. Never raises."""
    try:
        import catalog
        meta = meta if isinstance(meta, dict) else {}
        conn = catalog.connect()
        try:
            conn.executescript(_SCHEMA)
            conn.execute("""INSERT INTO usage (at, kind, ref, model, input_tokens, output_tokens, cache_read,
                                cache_write, cost_usd) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)""",
                         (catalog.now(), kind, ref, model, int(meta.get("promptTokenCount") or 0),
                          int(meta.get("candidatesTokenCount") or 0) + int(meta.get("thoughtsTokenCount") or 0), usd))
            conn.commit()
        finally:
            conn.close()
        return usd
    except Exception as e:
        log.warning("Couldn't record a Gemini call's cost for %s: %s", ref, e)
        return 0.0


def parts_for(conn: sqlite3.Connection, ref_prefix: str) -> dict:
    """What a clip or draft has cost so far, Claude and concept pictures apart."""
    conn.executescript(_SCHEMA)
    row = conn.execute("""SELECT COALESCE(SUM(CASE WHEN kind = 'image' THEN 0 ELSE cost_usd END), 0),
                                 COALESCE(SUM(CASE WHEN kind = 'image' THEN cost_usd ELSE 0 END), 0),
                                 COALESCE(SUM(CASE WHEN kind = 'image' THEN 1 ELSE 0 END), 0)
                          FROM usage WHERE ref = ? OR ref LIKE ?""", (ref_prefix, ref_prefix + ":%")).fetchone()
    return {"claude": round(row[0], 4), "images": round(row[1], 4), "image_count": row[2]}


def total_for(conn: sqlite3.Connection, ref_prefix: str) -> float:
    conn.executescript(_SCHEMA)
    row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE ref = ? OR ref LIKE ?",
                       (ref_prefix, ref_prefix + ":%")).fetchone()
    return round(row[0], 4)


def month_summary(conn: sqlite3.Connection) -> dict:
    """This calendar month: total and per kind (tagging, ideas, editing, music, pictures),
    with Claude and the concept pictures apart."""
    conn.executescript(_SCHEMA)
    import catalog
    month = catalog.now()[:7]
    rows = conn.execute("""SELECT kind, COUNT(*) AS calls, SUM(cost_usd) AS usd FROM usage
                           WHERE substr(at, 1, 7) = ? GROUP BY kind""", (month,)).fetchall()
    by_kind = {r["kind"]: {"calls": r["calls"], "usd": round(r["usd"] or 0, 4)} for r in rows}
    images = by_kind.get("image", {"calls": 0, "usd": 0.0})
    total = round(sum(k["usd"] for k in by_kind.values()), 4)
    return {"month": month, "usd": total, "by_kind": by_kind, "claude_usd": round(total - images["usd"], 4),
            "images_usd": images["usd"], "images": images["calls"]}

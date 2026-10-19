// Reading a list of Instagram links to import as references (library.js): a pasted list
// or a CSV. Pure functions, no page or database, so they're tested on their own.
const str = (v) => (typeof v === "string" ? v.trim() : v == null ? "" : String(v));

// ── Links: Instagram posts by their shortcode ───────────────────────────────

const IG_POST = /^\/(?:([A-Za-z0-9_.]+)\/)?(reels?|p|tv)\/([A-Za-z0-9_-]{5,})/;
// {url, shortcode, account, post_type} for a link, or null when it isn't one. Instagram's
// /reel/, /reels/, /p/ and /tv/ links (with or without the account in front, any tracking
// after) become one canonical link per post.
export function normalise(raw) {
  let s = String(raw || "").trim().replace(/^<|>$/g, "").replace(/^["']|["']$/g, "");
  if (!s) return null;
  if (!/^[a-z][a-z0-9+.-]*:/i.test(s)) s = `https://${s}`;
  let u;
  try { u = new URL(s); } catch { return null; }
  if (!/^https?:$/.test(u.protocol) || !u.hostname.includes(".") || u.username || u.password) return null;
  const host = u.hostname.toLowerCase().replace(/^(www|m)\./, "");
  if (host === "instagram.com") {
    const m = u.pathname.match(IG_POST);
    if (!m) return { url: null, error: "an Instagram link that isn't a post" };
    const kind = m[2] === "p" ? "p" : m[2] === "tv" ? "tv" : "reel";
    return { url: `https://www.instagram.com/${kind}/${m[3]}/`, shortcode: m[3], account: m[1] && !["reel", "reels", "p", "tv"].includes(m[1]) ? m[1] : "",
      post_type: kind === "reel" ? "reel" : kind === "tv" ? "video" : null };
  }
  u.protocol = "https:";
  u.hash = "";
  return { url: u.toString(), shortcode: null, account: "", post_type: null };
}

// A CSV line, with quoted fields ("a, b" and "" inside quotes).
function csvRows(text) {
  const rows = [];
  let row = [], field = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; } else if (c === '"') quoted = false; else field += c;
    } else if (c === '"' && field === "") quoted = true;
    else if (c === ",") { row.push(field); field = ""; } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); rows.push(row); row = []; field = "";
    } else field += c;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((f) => f.trim()));
}
const TYPE_WORDS = { reel: "reel", reels: "reel", carousel: "carousel", sidecar: "carousel", album: "carousel", image: "image", photo: "image", picture: "image", video: "video", igtv: "video", series: "series" };
const PURPOSES = ["design", "content", "engagement"];

// A pasted list or a file: a CSV with a url column (account, type, and a purpose / why column
// if it has them), or one link per line. Returns {rows: [{url, shortcode, account, post_type,
// purpose, why}], repeated, invalid: [lines], total}.
export function parseImport(text) {
  const clean = String(text || "").replace(/^﻿/, "");
  const lines = clean.split(/\r?\n/).filter((l) => l.trim());
  const out = { rows: [], repeated: 0, invalid: [], total: 0 };
  const seen = new Set();
  const add = (link, extra = {}) => {
    out.total += 1;
    const n = normalise(link);
    if (!n?.url) { out.invalid.push(String(link || "").slice(0, 120)); return; }
    const key = n.shortcode ? `ig:${n.shortcode}` : n.url.replace(/\/+$/, "").toLowerCase();
    if (seen.has(key)) { out.repeated += 1; return; }
    seen.add(key);
    const type = TYPE_WORDS[str(extra.type).toLowerCase()] || n.post_type;
    const purpose = PURPOSES.find((p) => str(extra.purpose).toLowerCase().includes(p)) || null;
    out.rows.push({ url: n.url, shortcode: n.shortcode, account: str(extra.account).replace(/^@/, "").slice(0, 100) || n.account,
      post_type: type || null, purpose, why: str(extra.why).slice(0, 4000) });
  };
  const head = lines.length ? csvRows(lines[0])[0]?.map((h) => h.trim().toLowerCase()) || [] : [];
  const col = (...names) => head.findIndex((h) => names.includes(h));
  const urlCol = col("url", "link", "post", "post_url", "permalink", "href");
  if (urlCol >= 0 && head.length > 1) {
    const c = { account: col("account", "username", "user", "handle", "owner", "author"), type: col("type", "post_type", "media_type", "kind", "format"),
      purpose: col("purpose", "tag", "tags", "for", "category", "reason"), why: col("why", "note", "notes", "comment") };
    for (const r of csvRows(clean).slice(1)) {
      if (!str(r[urlCol])) continue;
      add(r[urlCol], { account: r[c.account] ?? "", type: r[c.type] ?? "", purpose: r[c.purpose] ?? "", why: r[c.why] ?? "" });
    }
  } else {
    for (const l of lines) {
      const m = l.match(/(?:https?:\/\/)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+\/\S*/i);
      if (/^\s*(url|link)\s*$/i.test(l)) continue;
      add(m ? m[0].replace(/[),.;]+$/, "") : l.trim());
    }
  }
  return out;
}

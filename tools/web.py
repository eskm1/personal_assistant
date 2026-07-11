"""
Web browsing: fetch a URL and return readable page text plus the page's links.

Navigation works through the normal tool loop — the model reads a page, picks a
link from the returned list, and calls fetch_webpage again. No headless browser;
this is plain HTTP + HTML parsing, so JS-only pages won't render.

Safety rails:
- http/https only, redirects followed manually so EVERY hop is host-checked
- private/loopback/link-local addresses are refused (the bot runs on a server
  with internal services; a pasted link must not become an SSRF probe)
- response body capped at 2 MB, page text returned in 6k-char slices
"""
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

_MAX_BYTES = 2_000_000
_MAX_REDIRECTS = 5
_PAGE_CHARS = 6000
_MAX_LINKS = 30
_TIMEOUT = 20
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-SG,en;q=0.8",
}

# Tags whose contents are never readable page text
_STRIP_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "head")


def _host_is_blocked(hostname: str) -> bool:
    """True if the hostname resolves only to private/internal addresses."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False  # let requests raise the real DNS error with its message
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            return True
    return False


def _fetch(url: str) -> tuple[requests.Response, str]:
    """GET with manual redirect handling so each hop is validated. Returns (response, final_url)."""
    for _ in range(_MAX_REDIRECTS + 1):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Only http/https URLs are supported (got '{parsed.scheme or 'no scheme'}')")
        if not parsed.hostname:
            raise ValueError("URL has no hostname")
        if _host_is_blocked(parsed.hostname):
            raise ValueError(f"'{parsed.hostname}' resolves to a private/internal address — refusing to fetch")

        r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT, stream=True, allow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            next_url = urljoin(url, r.headers["location"])
            r.close()
            url = next_url
            continue
        return r, url
    raise ValueError("Too many redirects")


def _read_capped(r: requests.Response) -> tuple[bytes, bool]:
    body = b""
    truncated = False
    for chunk in r.iter_content(chunk_size=65536):
        body += chunk
        if len(body) >= _MAX_BYTES:
            truncated = True
            break
    r.close()
    return body, truncated


def _extract_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    seen: set[str] = set()
    lines: list[str] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"].strip())
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https"):
            continue
        href = href.split("#", 1)[0]
        if not href or href == base_url.split("#", 1)[0] or href in seen:
            continue
        seen.add(href)
        text = " ".join(a.get_text(" ", strip=True).split())[:80] or "(no text)"
        lines.append(f"{len(lines) + 1}. {text} — {href}")
        if len(lines) >= _MAX_LINKS:
            break
    return lines


def fetch_webpage(url: str, start_index: int = 0) -> str:
    """Fetch a webpage and return its readable text and links."""
    try:
        if not urlparse(url).scheme:
            url = "https://" + url
        r, final_url = _fetch(url)
        if r.status_code >= 400:
            r.close()
            return f"HTTP {r.status_code} fetching {final_url}"

        content_type = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        body, body_truncated = _read_capped(r)

        if content_type and content_type not in (
            "text/html", "application/xhtml+xml", "text/plain", "application/xml", "text/xml", ""
        ):
            return (
                f"URL: {final_url}\n"
                f"Content type is '{content_type}' ({len(body)} bytes) — not a readable webpage. "
                "I can only read HTML/text pages."
            )

        if content_type == "text/plain":
            title = ""
            text = body.decode(r.encoding or "utf-8", errors="replace")
            link_lines: list[str] = []
        else:
            soup = BeautifulSoup(body, "html.parser")
            title = soup.title.get_text(strip=True) if soup.title else ""
            link_lines = _extract_links(soup, final_url)
            for tag in soup(_STRIP_TAGS):
                tag.decompose()
            text = " \n".join(
                line.strip() for line in soup.get_text("\n").splitlines() if line.strip()
            )

        total = len(text)
        start = max(0, min(start_index, total))
        end = min(start + _PAGE_CHARS, total)
        chunk = text[start:end] or "(no readable text on this page — it may need JavaScript to render)"

        out = [f"Title: {title}" if title else "Title: (none)", f"URL: {final_url}", "", chunk]
        if end < total:
            out.append(
                f"\n[Showing characters {start}–{end} of {total}. "
                f"Call fetch_webpage again with start_index={end} to read more.]"
            )
        elif body_truncated:
            out.append("\n[Page was larger than the 2 MB download cap; content may be incomplete.]")
        if link_lines:
            out.append("\nLinks on this page:\n" + "\n".join(link_lines))
        return "\n".join(out)
    except requests.exceptions.Timeout:
        return f"Timed out fetching {url} (>{_TIMEOUT}s)."
    except requests.exceptions.RequestException as e:
        return f"Could not fetch {url}: {e}"
    except ValueError as e:
        return f"Could not fetch {url}: {e}"


TOOL_DEFS = [
    {
        "name": "fetch_webpage",
        "description": (
            "Fetch a webpage by URL and return its readable text plus a numbered list of links found on it. "
            "Use when Bryan shares a link or asks about a website. To navigate, pick a link URL from the "
            "results and call this tool again — keep it to a few hops and only follow links relevant to the "
            "question. Long pages are returned in chunks; pass start_index to continue reading. "
            "Plain HTTP fetch (no JavaScript), so some app-like sites may return little text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL to fetch (https://… )"},
                "start_index": {
                    "type": "integer",
                    "description": "Character offset to continue reading a long page from (default 0)",
                    "default": 0,
                },
            },
            "required": ["url"],
        },
    },
]

DISPATCH = {
    "fetch_webpage": fetch_webpage,
}

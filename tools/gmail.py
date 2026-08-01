import base64
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from io import BytesIO

from auth.google_oauth import get_gmail_service
from tools import pending

# An e-ticket PDF is a few pages of text; anything past this is being read for a
# purpose these tools don't serve, and it would blow out the model's context.
_MAX_ATTACHMENT_TEXT = 15_000
_MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024


# ── Helpers ───────────────────────────────────────────────────────────────────

def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "==")


def _walk(part: dict):
    """Yield every part of a message payload, depth-first."""
    yield part
    for sub in part.get("parts", []) or []:
        yield from _walk(sub)


def _html_to_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ModuleNotFoundError:
        return re.sub(r"<[^>]+>", " ", html)

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    text = soup.get_text("\n")
    # Marketing HTML is mostly whitespace once the tags are gone.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(l.strip() for l in text.splitlines())).strip()


def _decode_part(payload: dict) -> str:
    """Extract readable body text, preferring text/plain and falling back to HTML.

    The HTML fallback matters for exactly the emails Bryan asks about: airline and
    hotel confirmations are routinely HTML-only, and without this they came back
    as "(no readable body)" and the details had to be guessed from the snippet.
    """
    plain = html = ""
    for part in _walk(payload):
        if part.get("filename"):  # an attachment, not the body
            continue
        data = part.get("body", {}).get("data", "")
        if not data:
            continue
        mime = part.get("mimeType", "")
        if mime == "text/plain" and not plain:
            plain = _b64(data).decode("utf-8", errors="replace")
        elif mime == "text/html" and not html:
            html = _b64(data).decode("utf-8", errors="replace")

    if plain.strip():
        return plain
    return _html_to_text(html) if html else ""


def _attachments(payload: dict) -> list[dict]:
    """Every downloadable attachment on a message, in the order Gmail lists them."""
    found = []
    for part in _walk(payload):
        filename = part.get("filename") or ""
        body = part.get("body", {})
        if filename and body.get("attachmentId"):
            found.append({
                "filename": filename,
                "mime": part.get("mimeType", ""),
                "size": int(body.get("size", 0) or 0),
                "id": body["attachmentId"],
            })
    return found


def _pdf_to_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError:
        return ("Cannot read this PDF: pypdf is not installed on the server. "
                "Add it to requirements.txt and redeploy.")

    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            # Airlines often set an owner password only, which an empty user
            # password opens. A real password we cannot get past.
            if not reader.decrypt(""):
                return "This PDF is password-protected, so its text cannot be read."
        pages = [(p.extract_text() or "").strip() for p in reader.pages]
    except Exception as e:
        return f"Could not parse this PDF: {e}"

    text = "\n\n".join(f"--- page {i} ---\n{t}" for i, t in enumerate(pages, 1) if t)
    if len(re.sub(r"\s", "", text)) < 40:
        return ("This PDF has no extractable text — it is almost certainly a scan or a "
                f"single image ({len(pages)} page(s)). Ask Bryan for the details, or for "
                "a screenshot he can send as a photo.")
    return text


def _headers(msg: dict, *names: str) -> dict:
    hdrs = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    return {n: hdrs.get(n, "") for n in names}


# ── Tool functions ────────────────────────────────────────────────────────────

def search_gmail(query: str, max_results: int = 10) -> str:
    """Search Gmail and return a summary list."""
    try:
        svc = get_gmail_service()
        res = svc.users().messages().list(
            userId="me", q=query, maxResults=min(max_results, 20)
        ).execute()

        messages = res.get("messages", [])
        if not messages:
            return "No emails found matching that query."

        lines = []
        for m in messages:
            msg = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()
            h = _headers(msg, "From", "Subject", "Date")
            snippet = msg.get("snippet", "")[:80]
            lines.append(
                f"ID: {m['id']}\n"
                f"From: {h['From']}\n"
                f"Subject: {h['Subject']}\n"
                f"Date: {h['Date']}\n"
                f"Preview: {snippet}\n"
            )

        return "\n---\n".join(lines)

    except Exception as e:
        return f"Gmail search error: {e}"


def read_gmail(email_id: str) -> str:
    """Fetch and return the full content of an email."""
    try:
        svc = get_gmail_service()
        msg = svc.users().messages().get(
            userId="me", id=email_id, format="full"
        ).execute()

        h = _headers(msg, "From", "To", "Subject", "Date")
        payload = msg.get("payload", {})
        body = _decode_part(payload)
        if not body:
            body = msg.get("snippet", "(no readable body)")

        out = (
            f"From: {h['From']}\n"
            f"To: {h['To']}\n"
            f"Subject: {h['Subject']}\n"
            f"Date: {h['Date']}\n\n"
            f"{body.strip()}"
        )

        files = _attachments(payload)
        if files:
            listed = "\n".join(
                f"- {f['filename']} ({f['mime']}, {f['size'] // 1024 or 1} KB)" for f in files
            )
            out += (
                f"\n\n--- Attachments ({len(files)}) ---\n{listed}\n"
                "Call read_gmail_attachment with this email_id and the filename to read one."
            )

        return out

    except Exception as e:
        return f"Gmail read error: {e}"


def read_gmail_attachment(email_id: str, filename: str = "", attachment_id: str = "") -> str:
    """Download one attachment and return its text (PDFs included)."""
    try:
        svc = get_gmail_service()
        msg = svc.users().messages().get(userId="me", id=email_id, format="full").execute()
        files = _attachments(msg.get("payload", {}))

        if not files:
            return "That email has no attachments."

        target = None
        if attachment_id:
            target = next((f for f in files if f["id"] == attachment_id), None)
        elif filename:
            wanted = filename.strip().lower()
            target = (next((f for f in files if f["filename"].lower() == wanted), None)
                      or next((f for f in files if wanted in f["filename"].lower()), None))
        elif len(files) == 1:
            target = files[0]

        if not target:
            names = ", ".join(f["filename"] for f in files)
            return f"No attachment matched. This email has: {names}"

        if target["size"] > _MAX_ATTACHMENT_BYTES:
            return f"'{target['filename']}' is {target['size'] // (1024 * 1024)} MB — too large to read."

        blob = svc.users().messages().attachments().get(
            userId="me", messageId=email_id, id=target["id"]
        ).execute()
        data = _b64(blob.get("data", ""))

        mime, name = target["mime"], target["filename"]
        lower = name.lower()

        if mime == "application/pdf" or lower.endswith(".pdf"):
            text = _pdf_to_text(data)
        elif mime.startswith("text/") or lower.endswith((".txt", ".csv", ".ics", ".json", ".md")):
            text = data.decode("utf-8", errors="replace")
            if lower.endswith(".html") or mime == "text/html":
                text = _html_to_text(text)
        else:
            return (
                f"'{name}' is {mime}, which cannot be read as text. "
                "Readable types: PDF, and plain-text formats (txt, csv, ics, json). "
                "If Bryan needs this one, ask him to send it as a photo or paste the details."
            )

        if len(text) > _MAX_ATTACHMENT_TEXT:
            text = text[:_MAX_ATTACHMENT_TEXT] + f"\n\n[truncated at {_MAX_ATTACHMENT_TEXT} characters]"

        return f"Attachment: {name} ({mime})\n\n{text.strip()}"

    except Exception as e:
        return f"Gmail attachment error: {e}"


def _do_send_gmail(to: str, subject: str, body: str, cc: str = "") -> str:
    try:
        svc = get_gmail_service()

        mime = MIMEMultipart()
        mime["To"] = to
        mime["Subject"] = subject
        if cc:
            mime["Cc"] = cc
        mime.attach(MIMEText(body, "plain"))

        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        svc.users().messages().send(userId="me", body={"raw": raw}).execute()
        return f"Email sent to {to}."

    except Exception as e:
        return f"Gmail send error: {e}"


def send_gmail(to: str, subject: str, body: str, cc: str = "") -> str:
    """Stage a personal Gmail send for confirmation (does not send immediately)."""
    cc_line = f", cc {cc}" if cc else ""
    summary = f"Send personal (Gmail) email to {to}{cc_line} — subject: \"{subject}\""
    return pending.stage(summary, lambda: _do_send_gmail(to, subject, body, cc))


# ── Tool definitions (Anthropic schema) ──────────────────────────────────────

TOOL_DEFS = [
    {
        "name": "search_gmail",
        "description": (
            "Search the user's personal Gmail inbox using Gmail search syntax "
            "(e.g. 'from:alice is:unread', 'subject:invoice'). "
            "Returns a list of matching emails with IDs, senders, subjects, and previews."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query"},
                "max_results": {
                    "type": "integer",
                    "description": "Max emails to return (1–20, default 10)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_gmail",
        "description": (
            "Read the full content of a specific Gmail email given its ID. Also lists the email's "
            "attachments; read one with read_gmail_attachment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "Gmail message ID from search_gmail"},
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "read_gmail_attachment",
        "description": (
            "Read the contents of an attachment on a Gmail email — use this whenever the answer is "
            "inside an attached file rather than the email body, e.g. flight e-tickets, boarding "
            "passes, invoices and statements sent as PDFs. read_gmail lists an email's attachments; "
            "pass the filename from that list. Handles PDFs and plain-text formats (txt, csv, ics). "
            "Never guess dates, times or reference numbers from the email body when an attachment "
            "holds the real ones — read it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "Gmail message ID from search_gmail"},
                "filename": {
                    "type": "string",
                    "description": "Attachment filename as shown by read_gmail. Optional if the email has only one attachment.",
                    "default": "",
                },
                "attachment_id": {
                    "type": "string",
                    "description": "Gmail attachment ID, if known. Normally leave empty and use filename.",
                    "default": "",
                },
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "send_gmail",
        "description": (
            "Stage a personal (Gmail) email for sending. This does NOT send immediately — it stages "
            "the email and returns a summary. Show Bryan the summary and, once he confirms, call "
            "confirm_pending_action to actually send."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject line"},
                "body": {"type": "string", "description": "Plain-text email body"},
                "cc": {
                    "type": "string",
                    "description": "CC addresses, comma-separated (optional)",
                    "default": "",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
]

DISPATCH = {
    "search_gmail": search_gmail,
    "read_gmail": read_gmail,
    "read_gmail_attachment": read_gmail_attachment,
    "send_gmail": send_gmail,
}

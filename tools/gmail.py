import base64
import email as email_lib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from auth.google_oauth import get_gmail_service
from tools import pending


# ── Helpers ───────────────────────────────────────────────────────────────────

def _decode_part(part) -> str:
    """Recursively extract plain-text from a message part."""
    mime = part.get("mimeType", "")
    if mime == "text/plain":
        data = part.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    if mime.startswith("multipart/"):
        for sub in part.get("parts", []):
            text = _decode_part(sub)
            if text:
                return text
    return ""


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
        body = _decode_part(msg.get("payload", {}))
        if not body:
            body = msg.get("snippet", "(no readable body)")

        return (
            f"From: {h['From']}\n"
            f"To: {h['To']}\n"
            f"Subject: {h['Subject']}\n"
            f"Date: {h['Date']}\n\n"
            f"{body.strip()}"
        )

    except Exception as e:
        return f"Gmail read error: {e}"


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
        "description": "Read the full content of a specific Gmail email given its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "Gmail message ID from search_gmail"},
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
    "send_gmail": send_gmail,
}

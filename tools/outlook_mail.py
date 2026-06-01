import re
from auth.ms_graph import graph_get, graph_post


def search_outlook_mail(query: str, max_results: int = 10) -> str:
    try:
        params = {
            "$search": f'"{query}"',
            "$top": min(max_results, 20),
            "$select": "id,from,subject,receivedDateTime,bodyPreview",
        }
        data = graph_get("me/messages", params=params)
        messages = data.get("value", [])

        if not messages:
            return "No Outlook emails found matching that query."

        lines = []
        for msg in messages:
            sender = msg.get("from", {}).get("emailAddress", {})
            lines.append(
                f"ID: {msg['id'][:20]}...\n"
                f"From: {sender.get('name', '')} <{sender.get('address', '')}>\n"
                f"Subject: {msg.get('subject', '(no subject)')}\n"
                f"Date: {msg.get('receivedDateTime', '')[:19]}\n"
                f"Preview: {msg.get('bodyPreview', '')[:80]}\n"
            )
        return "\n---\n".join(lines)

    except Exception as e:
        return f"Outlook mail search error: {e}"


def read_outlook_mail(email_id: str) -> str:
    try:
        params = {"$select": "from,toRecipients,subject,receivedDateTime,body"}
        msg = graph_get(f"me/messages/{email_id}", params=params)

        sender = msg.get("from", {}).get("emailAddress", {})
        to_list = [r["emailAddress"].get("address", "") for r in msg.get("toRecipients", [])]
        body = msg.get("body", {}).get("content", "(no body)")

        # Strip HTML for readable plain-text
        body = re.sub(r"<[^>]+>", "", body).strip()
        body = re.sub(r"\n{3,}", "\n\n", body)

        return (
            f"From: {sender.get('name', '')} <{sender.get('address', '')}>\n"
            f"To: {', '.join(to_list)}\n"
            f"Subject: {msg.get('subject', '')}\n"
            f"Date: {msg.get('receivedDateTime', '')[:19]}\n\n"
            f"{body[:3000]}"
        )

    except Exception as e:
        return f"Outlook mail read error: {e}"


def send_outlook_mail(to: str, subject: str, body: str, cc: str = "") -> str:
    try:
        to_recipients = [{"emailAddress": {"address": a.strip()}} for a in to.split(",") if a.strip()]
        cc_recipients = [{"emailAddress": {"address": a.strip()}} for a in cc.split(",") if a.strip()]

        message: dict = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": to_recipients,
        }
        if cc_recipients:
            message["ccRecipients"] = cc_recipients

        graph_post("me/sendMail", {"message": message, "saveToSentItems": True})
        return f"Email sent to {to}."

    except Exception as e:
        return f"Outlook mail send error: {e}"


TOOL_DEFS = [
    {
        "name": "search_outlook_mail",
        "description": "Search the user's work Outlook inbox. Returns matching emails with IDs, senders, subjects, and previews.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms"},
                "max_results": {"type": "integer", "description": "Max emails to return (1–20, default 10)", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_outlook_mail",
        "description": "Read the full content of a specific Outlook work email by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "Full Outlook message ID from search_outlook_mail"},
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "send_outlook_mail",
        "description": (
            "Send an email from the user's work Outlook account. "
            "IMPORTANT: Always show the user the recipient, subject, and body and ask for confirmation before calling this."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address(es), comma-separated"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Plain-text email body"},
                "cc": {"type": "string", "description": "CC addresses, comma-separated (optional)", "default": ""},
            },
            "required": ["to", "subject", "body"],
        },
    },
]

DISPATCH = {
    "search_outlook_mail": search_outlook_mail,
    "read_outlook_mail": read_outlook_mail,
    "send_outlook_mail": send_outlook_mail,
}

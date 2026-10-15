"""
Epidemic Sound through its official MCP server (Streamable HTTP, JSON-RPC over
POST), authenticated with Bryan's personal API key. Used by the setup check now;
the editor uses it to search, recut and download tracks.

Only accounts listed in STUDIO_MUSIC_ACCOUNTS get Epidemic tracks: Bryan's plan
licenses one channel per platform, so it must not land on both Instagram accounts.
"""
import json
import re

import requests

from settings import EPIDEMIC_SOUND_API_KEY, EPIDEMIC_SOUND_MCP_URL, MUSIC_ACCOUNTS

PROTOCOL_VERSION = "2025-06-18"


def allowed_for(brand: str) -> bool:
    return brand in MUSIC_ACCOUNTS


class EpidemicSound:
    def __init__(self, api_key: str = EPIDEMIC_SOUND_API_KEY, url: str = EPIDEMIC_SOUND_MCP_URL):
        if not api_key:
            raise RuntimeError("EPIDEMIC_SOUND_API_KEY is not set")
        self.url, self.session_id, self._next_id = url, None, 0
        self.http = requests.Session()
        self.http.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Streamable HTTP servers may answer with plain JSON or an SSE stream.
            "Accept": "application/json, text/event-stream",
        })

    def _post(self, method: str, params: dict | None = None, notify: bool = False) -> dict | None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self._next_id += 1
            payload["id"] = self._next_id
        headers = {}
        if method != "initialize":
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        r = self.http.post(self.url, data=json.dumps(payload), headers=headers, timeout=60)
        if r.status_code in (401, 403):
            raise RuntimeError(f"Epidemic Sound refused the key (HTTP {r.status_code}); "
                               "check EPIDEMIC_SOUND_API_KEY or make a new key")
        r.raise_for_status()
        self.session_id = r.headers.get("Mcp-Session-Id", self.session_id)
        if notify:
            return None
        message = self._parse(r)
        if "error" in message:
            raise RuntimeError(f"Epidemic Sound error: {message['error'].get('message', message['error'])}")
        return message["result"]

    @staticmethod
    def _parse(r: requests.Response) -> dict:
        if r.headers.get("Content-Type", "").startswith("text/event-stream"):
            # The JSON-RPC response is the last "data:" event of the stream.
            events = [line[5:].strip() for line in r.text.splitlines() if line.startswith("data:")]
            for data in reversed(events):
                message = json.loads(data)
                if "result" in message or "error" in message:
                    return message
            raise RuntimeError("Epidemic Sound sent no response in its event stream")
        return r.json()

    def connect(self) -> dict:
        result = self._post("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "studio-worker", "version": "1"},
        })
        self._post("notifications/initialized", notify=True)
        return result.get("serverInfo", {})

    def tools(self) -> list[str]:
        return [t["name"] for t in self._post("tools/list")["tools"]]

    def call(self, tool: str, arguments: dict) -> dict:
        return self._post("tools/call", {"name": tool, "arguments": arguments})


def check() -> str:
    """One line for the setup check: who answered and which tools it offers."""
    client = EpidemicSound()
    server = client.connect()
    tools = client.tools()
    return f"connected to {server.get('name', 'Epidemic Sound')}, {len(tools)} tools ({', '.join(tools[:6])}{'…' if len(tools) > 6 else ''})"


# ── Track search (the editor) ─────────────────────────────────────────────────
# Claude drives Epidemic Sound's own tools (search, recut, download) through a
# local tool loop: the server describes its tools, Claude calls them, this module
# executes each call with Bryan's key. Nothing here hard-codes a tool's name or
# arguments, so it keeps working as Epidemic Sound changes its server.

TRACK_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "recording_id": {"type": "string"},
        "title": {"type": "string"},
        "artist": {"type": "string"},
        "download_url": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["found", "recording_id", "title", "artist", "download_url", "reason"],
    "additionalProperties": False,
}

_MAX_TOOL_ROUNDS = 12
_MAX_RESULT_CHARS = 20_000


def _tool_text(result: dict) -> str:
    text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
    if not text:
        text = json.dumps(result)[:_MAX_RESULT_CHARS]
    if len(text) > _MAX_RESULT_CHARS:
        text = text[:_MAX_RESULT_CHARS] + "\n[result shortened]"
    return text


def find_track(brief: str, seconds: float, out_dir, client=None, es: "EpidemicSound | None" = None,
               ref: str = ""):
    """Pick and download a music track for a post. Returns (path, track info)."""
    import anthropic
    from pathlib import Path

    from settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

    es = es or EpidemicSound()
    es.connect()
    listed = es._post("tools/list")["tools"]
    tools = [{"name": t["name"], "description": t.get("description", "")[:1024],
              "input_schema": t.get("inputSchema") or {"type": "object", "properties": {}}} for t in listed]
    client = client or anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or None)
    messages = [{"role": "user", "content": (
        f"Find one Epidemic Sound music track for a {seconds:.0f}-second Instagram Reel. Brief: {brief}\n\n"
        "Search the catalogue, pick the best fit (instrumental, works under speech), and get a download "
        "link for an MP3 of it. If the tools can make a version edited to the Reel's length, use that "
        "version. When done, answer with the track's details and the download URL exactly as the tools "
        "returned it. If nothing suitable is available, answer with found=false and say why.")}]
    for _ in range(_MAX_TOOL_ROUNDS):
        response = client.beta.messages.create(
            model=CLAUDE_MODEL, max_tokens=16000, tools=tools, messages=messages,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": TRACK_SCHEMA}},
        )
        import usage
        usage.record("music", ref, response)
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined the music search")
        calls = [b for b in response.content if b.type == "tool_use"]
        if not calls:
            text = next((b.text for b in response.content if b.type == "text"), "")
            track = json.loads(text)
            if not track["found"]:
                raise RuntimeError(f"no suitable track: {track['reason']}")
            path = Path(out_dir) / f"epidemic_{re.sub(r'[^A-Za-z0-9_-]', '', track['recording_id'])[:40] or 'track'}.mp3"
            _download(track["download_url"], path)
            return path, track
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for call in calls:
            try:
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": _tool_text(es.call(call.name, call.input))})
            except Exception as e:  # tell Claude, let it try another way
                results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                "content": f"{type(e).__name__}: {e}"})
        messages.append({"role": "user", "content": results})
    raise RuntimeError("music search took too many steps")


def _download(url: str, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, stream=True, timeout=120)
    if r.status_code in (401, 403):  # some links need the same key as the server
        r.close()
        r = requests.get(url, stream=True, timeout=120,
                         headers={"Authorization": f"Bearer {EPIDEMIC_SOUND_API_KEY}"})
    with r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    if path.stat().st_size < 10_000:
        raise RuntimeError("the downloaded music file is too small to be audio")


def describe_tools(path) -> list[str]:
    """Write the server's full tool list (names, descriptions, inputs) to a file."""
    es = EpidemicSound()
    es.connect()
    tools = es._post("tools/list")["tools"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tools, f, indent=2)
    return [t["name"] for t in tools]


if __name__ == "__main__":
    # python music.py: save Epidemic Sound's tool list to Studio\logs\epidemic_tools.json
    from settings import LOGS
    LOGS.mkdir(parents=True, exist_ok=True)
    names = describe_tools(LOGS / "epidemic_tools.json")
    print(f"{len(names)} tools saved to {LOGS / 'epidemic_tools.json'}:\n  " + "\n  ".join(names))

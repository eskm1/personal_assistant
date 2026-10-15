"""
Epidemic Sound through its official MCP server (Streamable HTTP, JSON-RPC over
POST), authenticated with Bryan's personal API key. Used by the setup check now;
the editor uses it to search, recut and download tracks.

Only accounts listed in STUDIO_MUSIC_ACCOUNTS get Epidemic tracks: Bryan's plan
licenses one channel per platform, so it must not land on both Instagram accounts.
"""
import json

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

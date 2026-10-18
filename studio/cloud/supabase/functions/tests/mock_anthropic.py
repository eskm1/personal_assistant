"""A stand-in for the Anthropic Messages API, for testing the Edge Functions without real calls.

    python3 mock_anthropic.py [port] [log.jsonl]

Answers POST /v1/messages (stream: true) as SSE: a thinking block, then the JSON text in chunks,
shaped by the request's output_config.format schema (organise: 2 new arcs, 3 ideas; develop:
5 shots, keeping filmed shots and one unfilmed one by keep_id). Every request body is appended to
the log for assertions. Words in the prompt switch behaviour:
  MOCK:refuse    stop_reason refusal, no text
  MOCK:cutoff    stop_reason max_tokens, truncated JSON
  MOCK:slow=N    take N seconds to stream
  MOCK:fallback  a declined partial, a fallback block, then the answer (usage.iterations too)
  MOCK:dropall   develop keeps no shots (keep_id 0 everywhere)
  MOCK:error     HTTP 500 (the SDK retries once)
A carousel (the schema has "slides") gets a cover, 5 points and an ask: the first point keeps
the picture of current slide 2 when it has one, the second takes a still from the first clip listed.
"""
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
LOG = sys.argv[2] if len(sys.argv) > 2 else None


def organise(prompt: str) -> dict:
    existing = re.findall(r"- (arc:\d+) ", prompt)
    return {
        "arcs": [
            {"key": "new:waterproofing", "title": "Waterproofing, done right",
             "purpose": "Show homeowners what proper wet-area waterproofing looks like."},
            {"key": "new:client", "title": "The client's story", "purpose": "Follow the family through the reno."},
        ],
        "ideas": [
            {"arc": "new:waterproofing", "title": "  Why we flood-test every bathroom  ", "brand": "urban_makers",
             "post_type": "knowhow", "format": "talking_head", "hook": "We fill your bathroom with water before you ever see it.",
             "purpose": "Homeowners learn what a flood test is and why it matters.", "cta": "Save this for your reno",
             "caption": "Every bathroom gets a 24h flood test. #renovation #singapore #waterproofing",
             "est_seconds": 35, "music": {"mood": "confident", "tempo_bpm": 96, "search": "confident minimal beat"}},
            {"arc": "new:client", "title": "Meet the Tans", "brand": "urban_makers", "post_type": "promo",
             "format": "day_in_the_life",
             "hook": "[client name] waited 3 years for this kitchen.", "purpose": "Make viewers root for the family.",
             "cta": "Follow for part 2", "caption": "Part 1. #hdbreno #kitchen #urbanmakers", "est_seconds": 45,
             "music": {"mood": "warm", "tempo_bpm": 84, "search": "warm acoustic"}},
            {"arc": existing[0] if existing else "arc:999999", "title": "Hacking day in 30 seconds",
             "brand": "urban_makers", "post_type": "onsite", "format": "compilation", "hook": "Day one: everything goes.",
             "purpose": "Satisfying demolition montage.", "cta": "DM us KENSINGTON", "caption": "#demolition #reno",
             "est_seconds": 30, "music": {"mood": "energetic", "tempo_bpm": 128, "search": "energetic drums"}},
        ],
        "notes": "Merged two duplicate flood-test ideas. Mock organiser.",
    }


def develop(prompt: str) -> dict:
    shots = re.findall(r"^  id (\d+) \[(\w+)\]", prompt, re.M)
    drop = "MOCK:dropall" in prompt
    keep = [] if drop else [int(i) for i, state in shots if state != "needed"]
    needed = [int(i) for i, state in shots if state == "needed"]
    if needed and not drop:
        keep.append(needed[0])  # reuse one unfilmed shot too
    out = []
    for n in range(5):
        k = keep[n] if n < len(keep) else 0
        out.append({"keep_id": k, "kind": ["talking", "broll", "detail", "before_after", "reaction"][n],
                    "framing": ["medium", "wide", "close", "detail", "pov"][n],
                    "camera": ["iphone", "gopro", "zfc", "any", "iphone"][n],
                    "description": f"Mock shot {n + 1}" + (f" (kept {k})" if k else ""),
                    "line": "Here's the thing." if n == 0 else "", "on_screen": "", "seconds": 4.5,
                    "why": "Mock reason."})
    title = re.search(r"^  title: (.*)$", prompt, re.M)
    arc = re.search(r"arc key: (arc:\d+)", prompt)
    return {
        "idea": {"arc": arc.group(1) if arc else "arc:0", "title": (title.group(1) if title else "Idea") + " (developed)",
                 "brand": "urban_makers", "post_type": "offer",
                 "format": "carousel" if "MOCK:tocarousel" in prompt else "talking_head", "hook": "Sharper hook.",
                 "purpose": "Clear purpose.",
                 "cta": "Save this", "caption": "Developed caption #reno", "est_seconds": 40,
                 "music": {"mood": "upbeat", "tempo_bpm": 110, "search": "upbeat house"}, "shots": out},
        "changes": f"Mock develop: {len(out)} shots, kept {[k for k in keep][:5]}.",
    }


def slides(prompt: str) -> dict:
    clips = re.findall(r"^  clip (\d+): ", prompt, re.M)
    pictured = re.search(r"^  2\. \[\w+\] .*picture: (?!text only)", prompt, re.M)
    title = re.search(r"^  title: (.*)$", prompt, re.M)
    points = [("Mark it off without a wall", "A rug, an open shelf or a sheer curtain says this is a different space."),
              ("Lamps, not downlights", "One warm lamp at sitting height. Put the ceiling lights on their own switch."),
              ("Face away from the TV", "Turn the chair to the window so the corner feels like its own room."),
              ("Keep the clutter behind doors", "Closed storage under the seat keeps the corner calm."),
              ("How we did it in a 4-room flat", "A built-in window seat with storage under it.")]
    out = [{"role": "cover", "heading": "Open-plan flat? Give yourself one quiet corner", "body": "",
            "keep_image": 0, "clip_id": 0, "at": 0, "image_idea": ""}]
    for n, (h, b) in enumerate(points):
        out.append({"role": "point", "heading": h, "body": b, "keep_image": 2 if n == 0 and pictured else 0,
                    "clip_id": int(clips[0]) if n == 1 and clips else (999999 if n == 2 else 0),
                    "at": 3.2 if n == 1 else 0, "image_idea": "a warm lamp beside a reading chair" if n == 1 else ""})
    out.append({"role": "ask", "heading": "Planning a reno?", "body": "Send us your floor plan and we'll suggest "
                "where your quiet corner could go.", "keep_image": 0, "clip_id": 0, "at": 0, "image_idea": ""})
    return {"title": (title.group(1) if title else "Carousel") + " (slides)", "post_type": "ideas",
            "purpose": "Flat owners with an open plan get a quiet corner without building a wall.",
            "caption": "A quiet corner doesn't need a wall. Save this for your reno. #hdbreno #interiordesign #singapore",
            "slides": out, "changes": f"Mock slides: {len(out)} slides."}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        if LOG:
            with open(LOG, "a") as f:
                f.write(json.dumps({"path": self.path, "beta": self.headers.get("anthropic-beta"),
                                    "api_key": bool(self.headers.get("x-api-key")), "body": body}) + "\n")
        prompt = body.get("system", "") if isinstance(body.get("system"), str) else ""
        prompt += "\n" + "\n".join(m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])
                                   for m in body.get("messages", []))
        if "MOCK:error" in prompt:
            data = json.dumps({"type": "error", "error": {"type": "api_error", "message": "mock failure"}}).encode()
            self.send_response(500)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        props = body.get("output_config", {}).get("format", {}).get("schema", {}).get("properties", {})
        answer = slides(prompt) if "slides" in props else develop(prompt) if "idea" in props else organise(prompt)
        text = json.dumps(answer)
        slow = re.search(r"MOCK:slow=(\d+)", prompt)
        stop = "end_turn"
        if "MOCK:refuse" in prompt:
            text, stop = "", "refusal"
        elif "MOCK:cutoff" in prompt:
            text, stop = text[: len(text) // 2], "max_tokens"

        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()

        def event(name, data):
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

        in_tokens = len(json.dumps(body)) // 4
        out_tokens = max(1, len(text) // 4)
        event("message_start", {"type": "message_start", "message": {
            "id": "msg_mock", "type": "message", "role": "assistant", "model": body.get("model"), "content": [],
            "stop_reason": None, "stop_sequence": None, "stop_details": None,
            "usage": {"input_tokens": in_tokens, "output_tokens": 1, "cache_read_input_tokens": 1000,
                      "cache_creation_input_tokens": 0}}})
        idx = 0
        event("content_block_start", {"type": "content_block_start", "index": idx,
                                      "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
        event("content_block_delta", {"type": "content_block_delta", "index": idx,
                                      "delta": {"type": "signature_delta", "signature": "mocksig"}})
        event("content_block_stop", {"type": "content_block_stop", "index": idx})
        idx += 1
        iterations = None
        if "MOCK:fallback" in prompt and text:
            event("content_block_start", {"type": "content_block_start", "index": idx,
                                          "content_block": {"type": "text", "text": ""}})
            event("content_block_delta", {"type": "content_block_delta", "index": idx,
                                          "delta": {"type": "text_delta", "text": '{"partial": tr'}})
            event("content_block_stop", {"type": "content_block_stop", "index": idx})
            idx += 1
            event("content_block_start", {"type": "content_block_start", "index": idx, "content_block": {
                "type": "fallback", "from": {"model": body.get("model")}, "to": {"model": "claude-opus-5"}}})
            event("content_block_stop", {"type": "content_block_stop", "index": idx})
            idx += 1
            iterations = [
                {"type": "message", "model": body.get("model"), "input_tokens": in_tokens, "output_tokens": 10,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "cache_creation": None},
                {"type": "fallback_message", "model": "claude-opus-5", "input_tokens": in_tokens,
                 "output_tokens": out_tokens, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                 "cache_creation": None},
            ]
        if text:
            event("content_block_start", {"type": "content_block_start", "index": idx,
                                          "content_block": {"type": "text", "text": ""}})
            chunks = [text[i:i + 200] for i in range(0, len(text), 200)]
            delay = int(slow.group(1)) / len(chunks) if slow else 0.005
            for c in chunks:
                time.sleep(delay)
                event("content_block_delta", {"type": "content_block_delta", "index": idx,
                                              "delta": {"type": "text_delta", "text": c}})
            event("content_block_stop", {"type": "content_block_stop", "index": idx})
        delta_usage = {"output_tokens": out_tokens}
        if iterations:
            delta_usage["iterations"] = iterations
        event("message_delta", {"type": "message_delta",
                                "delta": {"stop_reason": stop, "stop_sequence": None,
                                          "stop_details": {"type": "refusal", "category": "cyber",
                                                           "explanation": "mock"} if stop == "refusal" else None},
                                "usage": delta_usage})
        event("message_stop", {"type": "message_stop"})
        self.close_connection = True


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

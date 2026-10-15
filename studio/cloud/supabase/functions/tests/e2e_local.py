"""End-to-end test of the planning Edge Functions against the local Supabase stack.

Needs: `supabase start`, tests/mock_anthropic.py running on the host (port 8787), and
`supabase functions serve --env-file supabase/functions/.env` with
ANTHROPIC_BASE_URL=http://host.docker.internal:8787. Then:

    python3 e2e_local.py <local.env with API_URL, ANON_KEY, SERVICE_ROLE_KEY, JWT_SECRET, MAILPIT_URL> <mock log>

Creates its own users (@edge.test) and project and removes them afterwards. Acting as the owner
uses a token minted for the existing owner (local JWT secret), so no one's data is changed.
"""
import base64
import hashlib
import hmac
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ENV = {}
for line in open(sys.argv[1]):
    if "=" in line and not line.startswith("#"):
        k, v = line.strip().split("=", 1)
        ENV[k] = v.strip('"')
MOCK_LOG = sys.argv[2]
API, ANON, SERVICE = ENV["API_URL"], ENV["ANON_KEY"], ENV["SERVICE_ROLE_KEY"]
FN = API + "/functions/v1"
RUN = str(int(time.time()))
failures = []


def ok(cond, what):
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        failures.append(what)


def http(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json",
                                                                         **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw and raw[:1] in b"[{" else raw.decode()), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except ValueError:
            return e.code, raw.decode(), dict(e.headers)


def svc(method, path, body=None, prefer="return=representation"):
    return http(method, f"{API}/rest/v1/{path}", body,
                {"apikey": SERVICE, "authorization": f"Bearer {SERVICE}", "prefer": prefer})[1]


def admin(method, path, body=None):
    return http(method, f"{API}/auth/v1/admin/{path}", body, {"apikey": SERVICE, "authorization": f"Bearer {SERVICE}"})


def fn(name, body=None, token=None, method="POST"):
    h = {"apikey": ANON}
    if token:
        h["authorization"] = f"Bearer {token}"
    return http(method, f"{FN}/{name}", body, h)


def make_user(email, name):
    status, user, _ = admin("POST", "users", {"email": email, "password": "edge-pass-123", "email_confirm": True,
                                              "user_metadata": {"name": name}})
    assert status == 200, user
    status, tok, _ = http("POST", f"{API}/auth/v1/token?grant_type=password",
                          {"email": email, "password": "edge-pass-123"}, {"apikey": ANON})
    assert status == 200, tok
    return user["id"], tok["access_token"]


def b64(d):
    return base64.urlsafe_b64encode(d).rstrip(b"=").decode()


def mint(user_id, email):
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = int(time.time())
    claims = b64(json.dumps({"sub": user_id, "role": "authenticated", "aud": "authenticated", "email": email,
                             "iat": now, "exp": now + 3600, "aal": "aal1"}).encode())
    sig = hmac.new(ENV["JWT_SECRET"].encode(), f"{header}.{claims}".encode(), hashlib.sha256).digest()
    return f"{header}.{claims}.{b64(sig)}"


def wait_job(job_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        job = svc("GET", f"jobs?id=eq.{job_id}")[0]
        if job["state"] in ("done", "failed"):
            return job
        time.sleep(0.5)
    return svc("GET", f"jobs?id=eq.{job_id}")[0]


def shots_of(idea_id):
    return svc("GET", f"shots?idea_id=eq.{idea_id}&order=position,id")


def mock_requests():
    try:
        return [r for r in map(json.loads, open(MOCK_LOG)) if r["body"].get("messages")]
    except FileNotFoundError:
        return []


created_users, clip_ids, project_id = [], [], None
try:
    team_id, team_tok = make_user(f"edge-team-{RUN}@edge.test", "Edge Team")
    created_users.append(team_id)
    out_id, out_tok = make_user(f"edge-outsider-{RUN}@edge.test", "Outsider")
    created_users.append(out_id)
    svc("DELETE", f"profiles?id=eq.{out_id}")  # an account that isn't (any longer) on the team
    owner = svc("GET", "profiles?role=eq.owner&limit=1")[0]
    owner_tok = mint(owner["id"], owner["email"])
    project_id = svc("POST", "projects", {"name": f"Edge test {RUN}", "brief": "Bathroom + kitchen, HDB 4-room",
                                          "created_by": team_id})[0]["id"]
    print(f"project {project_id}, team user {team_id}")

    print("CORS / auth")
    for name in ("plan-organise", "plan-develop", "team"):
        s, _, h = http("OPTIONS", f"{FN}/{name}", headers={"origin": "https://planner.example",
                                                            "access-control-request-method": "POST",
                                                            "access-control-request-headers": "authorization,content-type"})
        hl = {k.lower(): v for k, v in h.items()}
        # Locally Kong answers preflights itself; the function's own answer is in tests/unit_test.ts.
        ok(s == 200 and hl.get("access-control-allow-origin") and "authorization" in
           hl.get("access-control-allow-headers", ""), f"preflight {name}: {s} {hl.get('access-control-allow-headers')}")
    s, body, _ = fn("plan-organise", {"project_id": project_id, "text": "x"})
    ok(s == 401, f"no JWT -> 401 ({s})")
    s, body, h = fn("plan-organise", {"project_id": project_id, "text": "x"}, out_tok)
    ok(s == 403 and {k.lower(): v for k, v in h.items()}.get("access-control-allow-origin") == "*",
       f"non-member -> 403 with CORS ({s} {body})")
    s, body, _ = fn("plan-organise", {"project_id": project_id, "text": "  "}, team_tok)
    ok(s == 400, f"empty text -> 400 ({s} {body})")
    s, body, _ = fn("plan-organise", {"project_id": 99999999, "text": "hi"}, team_tok)
    ok(s == 404, f"unknown project -> 404 ({s})")

    print("organise")
    before = len(mock_requests())
    t0 = time.time()
    s, body, _ = fn("plan-organise", {"project_id": project_id,
                                      "text": "flood test video\nmeet the client\nhacking montage"}, team_tok)
    ok(s == 200 and "job_id" in body and time.time() - t0 < 5, f"organise answers fast with job_id ({s} {body})")
    job = wait_job(body["job_id"])
    ok(job["state"] == "done", f"organise job done ({job['state']} {job['error']})")
    res = job["result"] or {}
    ok(res.get("ideas") == 3 and res.get("arcs") == 2 and len(res.get("idea_ids", [])) == 3 and res.get("notes"),
       f"result {{ideas, arcs, notes, idea_ids}}: {json.dumps({k: res.get(k) for k in ('ideas', 'arcs', 'idea_ids')})}")
    u = res.get("usage", {})
    exp = round((u.get("input_tokens", 0) * 4 + u.get("output_tokens", 0) * 20 + 1000 * 0.2) / 1e6, 6)
    ok(u.get("input_tokens", 0) > 0 and abs(u.get("cost_usd", -1) - exp) < 1e-6 and u.get("cache_read_input_tokens") == 1000,
       f"usage recorded {u} (expected cost {exp})")
    ok(job["requested_by"] == team_id, "organise job requested_by caller")
    arcs = svc("GET", f"arcs?project_id=eq.{project_id}&order=position")
    ideas = svc("GET", f"ideas?project_id=eq.{project_id}&order=id")
    ok([a["title"] for a in arcs] == ["Waterproofing, done right", "The client's story"] and
       [a["position"] for a in arcs] == [1, 2], f"2 arcs inserted in order {[(a['title'], a['position']) for a in arcs]}")
    ok(len(ideas) == 3 and all(i["decision"] == "proposed" and i["status"] == "new" and i["created_by"] == team_id
                               for i in ideas), "3 ideas, proposed/new, created_by requester")
    ok(ideas[0]["title"] == "Why we flood-test every bathroom", "idea title trimmed")
    ok(ideas[0]["music"] == {"mood": "confident", "tempo_bpm": 96, "search": "confident minimal beat"},
       f"music plan {ideas[0]['music']}")
    ok(ideas[2]["arc_id"] is None, "unknown arc key -> Unsorted")
    ok(ideas[0]["arc_id"] == arcs[0]["id"] and ideas[1]["arc_id"] == arcs[1]["id"], "ideas in their new arcs")
    ok(svc("GET", f"shots?idea_id=in.({','.join(str(i['id']) for i in ideas)})") == [], "organise inserts no shots")
    dev_jobs = svc("GET", f"jobs?project_id=eq.{project_id}&kind=eq.develop&order=id")
    ok(len(dev_jobs) == 3 and all(j["state"] == "queued" and j["runner"] == "cloud" and j["requested_by"] == team_id
                                  for j in dev_jobs) and sorted(j["idea_id"] for j in dev_jobs) == sorted(res["idea_ids"]),
       "one queued develop job per new idea")
    req = mock_requests()[before]
    b = req["body"]
    ok(b["model"] == "claude-opus-5-5" and b["fallbacks"] == "default" and b["stream"] is True and
       "server-side-fallback-2026-07-01" in (req["beta"] or "") and "thinking" not in b and
       b["output_config"]["effort"] == "medium" and b["output_config"]["format"]["type"] == "json_schema" and
       "shots" not in b["output_config"]["format"]["schema"]["properties"]["ideas"]["items"]["properties"],
       f"organise request: model/betas/fallbacks/effort medium/no thinking/no shots ({req['beta']})")
    ok("{brands}" not in b["system"] and "{playbook}" not in b["system"] and "{cameras}" not in b["system"] and
       "iphone = iPhone 16 Pro" in b["system"], "placeholders filled")

    print("develop (queued jobs kicked by the planner)")
    with ThreadPoolExecutor(4) as pool:
        kicks = list(pool.map(lambda j: fn("plan-develop", {"job_id": j["id"]}, team_tok), dev_jobs))
    ok(all(k[0] == 200 and "already" not in k[1] for k in kicks), f"kick 3 jobs {[k[1] for k in kicks]}")
    s, again, _ = fn("plan-develop", {"job_id": dev_jobs[0]["id"]}, team_tok)
    ok(s == 200 and again.get("already") is True, f"kicking again -> already ({again})")
    done = [wait_job(j["id"]) for j in dev_jobs]
    ok(all(d["state"] == "done" for d in done), f"develop jobs done {[(d['state'], d['error']) for d in done]}")
    ok(all(d["result"].get("changes") and d["result"].get("usage", {}).get("cost_usd", 0) > 0 for d in done),
       "develop results carry changes + usage")
    for i in ideas:
        sh = shots_of(i["id"])
        ok(len(sh) == 5 and [x["position"] for x in sh] == [1, 2, 3, 4, 5], f"idea {i['id']}: 5 shots at 1..5")
    a = svc("GET", f"ideas?id=eq.{ideas[0]['id']}")[0]
    ok(a["title"].endswith("(developed)") and a["est_seconds"] == 40 and a["music"]["mood"] == "upbeat",
       "idea fields updated by develop")
    dreq = [r for r in mock_requests()
            if "idea" in r["body"].get("output_config", {}).get("format", {}).get("schema", {}).get("properties", {})][-1]
    ok(dreq["body"]["output_config"]["effort"] == "high" and dreq["body"]["max_tokens"] == 32000 and
       "thinking" not in dreq["body"], "develop request: effort high, max_tokens 32000, no thinking")
    s, body, _ = fn("plan-develop", {"job_id": job["id"]}, team_tok)
    ok(s == 404, f"organise job id isn't a develop job -> 404 ({s})")

    print("refine keeps filmed shots")
    idea = ideas[0]["id"]
    svc("PATCH", f"ideas?id=eq.{idea}", {"music": {**a["music"], "shortlist": [{"recording_id": "r1"}],
                                                   "chosen": {"recording_id": "r1"}}})
    sh = shots_of(idea)
    svc("PATCH", f"shots?id=eq.{sh[2]['id']}", {"filmed_at": "2026-10-15T01:00:00Z"})  # filmed
    clip_ids = [990000000 + int(RUN) % 1000000 * 10 + k for k in (1, 2)]
    svc("POST", "clips", [{"id": clip_ids[0], "title": "edge retake", "verdict": "retake"},
                          {"id": clip_ids[1], "title": "edge good", "verdict": "good"}])
    svc("POST", "takes", [{"shot_id": sh[3]["id"], "clip_id": clip_ids[0], "source": "auto"},  # retake
                          {"shot_id": sh[4]["id"], "clip_id": clip_ids[1], "source": "auto"}])  # got
    svc("POST", "comments", {"idea_id": idea, "author": team_id, "body": "Can we open on the flood test?"})
    protected = [sh[2]["id"], sh[3]["id"], sh[4]["id"]]
    s, body, _ = fn("plan-develop", {"idea_id": idea, "request": "MOCK:dropall make it punchier"}, team_tok)
    ok(s == 200, f"refine request accepted ({s} {body})")
    j = wait_job(body["job_id"])
    ok(j["state"] == "done" and j["payload"] == {"request": "MOCK:dropall make it punchier"}, f"refine done ({j['error']})")
    after = shots_of(idea)
    ok(len(after) == 8 and [x["position"] for x in after] == list(range(1, 9)) and
       [x["id"] for x in after[5:]] == protected, f"unkept filmed/retake/got shots appended: {[(x['id'], x['position']) for x in after]}")
    ok(not ({x["id"] for x in sh[:2]} & {x["id"] for x in after}), "unkept unfilmed shots deleted")
    prompt = [r for r in mock_requests() if "dropall" in json.dumps(r["body"]["messages"])][-1]["body"]["messages"][0]["content"]
    ok(f"id {sh[2]['id']} [filmed]" in prompt and f"id {sh[3]['id']} [retake]" in prompt and
       f"id {sh[4]['id']} [got]" in prompt and "Edge Team: Can we open on the flood test?" in prompt,
       "prompt marks shot states and includes comments")
    m = svc("GET", f"ideas?id=eq.{idea}")[0]["music"]
    ok(m.get("shortlist") == [{"recording_id": "r1"}] and m.get("chosen") == {"recording_id": "r1"} and
       m.get("mood") == "upbeat", "music shortlist/chosen kept")

    # Default mock keeps the filmed/retake/got shots and one unfilmed one by keep_id.
    s, body, _ = fn("plan-develop", {"idea_id": idea}, team_tok)
    j = wait_job(body["job_id"])
    after2 = shots_of(idea)
    ok(j["state"] == "done" and len(after2) == 5 and [x["id"] for x in after2[:3]] == protected and
       after2[3]["id"] == after[0]["id"] and [x["position"] for x in after2] == [1, 2, 3, 4, 5] and
       after2[3]["filmed_at"] is None and after2[0]["description"].startswith("Mock shot 1 (kept"),
       f"keep_id reuses shots in place: {[(x['id'], x['position']) for x in after2]}")
    ok(svc("GET", f"shots?id=eq.{protected[0]}")[0]["filmed_at"] is not None, "filmed_at untouched")

    print("busy / stale / failures")
    s, body, _ = fn("plan-develop", {"idea_id": idea, "request": "MOCK:slow=4"}, team_tok)
    s2, body2, _ = fn("plan-develop", {"idea_id": idea}, team_tok)
    ok(s2 == 409 and body2.get("job_id") == body["job_id"], f"second request while running -> 409 ({s2} {body2})")
    wait_job(body["job_id"])
    stale = svc("POST", "jobs", {"kind": "develop", "runner": "cloud", "project_id": project_id, "idea_id": idea,
                                 "state": "running", "started_at": "2026-10-15T00:00:00Z", "requested_by": team_id})[0]
    s, body, _ = fn("plan-develop", {"idea_id": idea}, team_tok)
    ok(s == 200, f"stale running job doesn't block ({s} {body})")
    st = svc("GET", f"jobs?id=eq.{stale['id']}")[0]
    ok(st["state"] == "failed" and st["error"] == "took too long, try again", f"stale job failed: {st['error']}")
    wait_job(body["job_id"])

    s, body, _ = fn("plan-develop", {"idea_id": ideas[1]["id"], "request": "MOCK:refuse"}, team_tok)
    j = wait_job(body["job_id"])
    ok(j["state"] == "failed" and j["error"] == "Claude declined this request" and
       j["result"]["usage"]["input_tokens"] > 0, f"refusal -> failed with message + usage ({j['error']}, {j['result']})")
    n_shots = len(shots_of(ideas[1]["id"]))
    ok(n_shots == 5, "refusal leaves the storyboard alone")
    s, body, _ = fn("plan-develop", {"idea_id": ideas[1]["id"], "request": "MOCK:cutoff"}, team_tok)
    j = wait_job(body["job_id"])
    ok(j["state"] == "failed" and "cut off" in j["error"], f"max_tokens -> failed ({j['error']})")
    s, body, _ = fn("plan-organise", {"project_id": project_id, "text": "MOCK:refuse something"}, team_tok)
    j = wait_job(body["job_id"])
    ok(j["state"] == "failed" and j["error"] == "Claude declined this request", f"organise refusal ({j['error']})")
    s, body, _ = fn("plan-develop", {"idea_id": ideas[1]["id"], "request": "MOCK:error"}, team_tok)
    j = wait_job(body["job_id"], 90)
    ok(j["state"] == "failed" and "unavailable" in j["error"], f"API 500 -> failed ({j['error']})")
    s, body, _ = fn("plan-develop", {"idea_id": ideas[1]["id"], "request": "MOCK:fallback"}, team_tok)
    j = wait_job(body["job_id"])
    u = (j["result"] or {}).get("usage", {})
    ok(j["state"] == "done" and u.get("model") == "claude-opus-5" and u.get("output_tokens", 0) > 10,
       f"mid-stream fallback: answer after the fallback block, usage over iterations ({j['error']}, {u})")

    print("second organise reuses arcs")
    s, body, _ = fn("plan-organise", {"project_id": project_id, "text": "more ideas"}, team_tok)
    j = wait_job(body["job_id"])
    arcs2 = svc("GET", f"arcs?project_id=eq.{project_id}&order=position")
    new_ideas = svc("GET", f"ideas?id=in.({','.join(map(str, j['result']['idea_ids']))})&order=id")
    req = [r for r in mock_requests() if "more ideas" in json.dumps(r["body"]["messages"])][-1]["body"]
    ok(f'arc:{arcs[0]["id"]} "Waterproofing, done right"' in req["messages"][0]["content"] and
       "Why we flood-test every bathroom" in req["messages"][0]["content"], "board (arcs + idea titles) in the prompt")
    ok(len(arcs2) == 4 and [a["position"] for a in arcs2] == [1, 2, 3, 4], "new arcs after existing")
    ok(new_ideas[2]["arc_id"] == arcs[0]["id"] and new_ideas[2]["position"] == 2,
       f"existing arc reused by key, idea positioned at its end ({new_ideas[2]['arc_id']}, {new_ideas[2]['position']})")

    print("team")
    s, body, _ = fn("team", None, team_tok, "GET")
    ok(s == 403, f"team member -> 403 on team ({s} {body})")
    s, body, _ = fn("team", None, owner_tok, "GET")
    me = [m for m in body.get("members", []) if m["id"] == team_id]
    ok(s == 200 and me and me[0]["last_sign_in_at"] and me[0]["role"] == "team" and me[0]["name"] == "Edge Team",
       f"owner lists members with last_sign_in_at ({s}, {me})")
    inv_email = f"edge-invite-{RUN}@edge.test"
    s, body, _ = fn("team", {"action": "invite", "email": inv_email, "name": "Invited Person",
                             "redirect_to": "http://127.0.0.1:3000/planner/"}, owner_tok)
    ok(s == 200 and body["member"]["role"] == "team" and body["member"]["name"] == "Invited Person",
       f"invite ({s} {body})")
    inv_id = body.get("member", {}).get("id")
    if inv_id:
        created_users.append(inv_id)
    s, body2, _ = fn("team", {"action": "invite", "email": inv_email, "name": "x"}, owner_tok)
    ok(s == 409, f"invite again -> 409 ({s} {body2})")
    mail = None
    for _ in range(20):
        _, found, _ = http("GET", f"{ENV['MAILPIT_URL']}/api/v1/search?query=to:{inv_email}")
        if isinstance(found, dict) and found.get("messages"):
            mail = found["messages"][0]
            break
        time.sleep(0.5)
    ok(mail is not None, "invite email arrived in Mailpit")
    if mail:
        _, msg, _ = http("GET", f"{ENV['MAILPIT_URL']}/api/v1/message/{mail['ID']}")
        ok("redirect_to=http://127.0.0.1:3000/planner/" in (msg.get("HTML", "") + msg.get("Text", "")),
           "invite link redirects to the planner")
    s, body, _ = fn("team", {"action": "remove", "user_id": owner["id"]}, owner_tok)
    ok(s == 400, f"can't remove yourself ({s} {body})")
    s, body, _ = fn("team", {"action": "remove", "user_id": inv_id}, owner_tok)
    ok(s == 200 and not svc("GET", f"profiles?id=eq.{inv_id}"), f"remove ({s} {body})")
    s, body, _ = fn("team", {"action": "remove", "user_id": team_id}, team_tok)
    ok(s == 403, "team member can't remove")
finally:
    if project_id:
        svc("DELETE", f"projects?id=eq.{project_id}")
    for c in clip_ids:
        svc("DELETE", f"clips?id=eq.{c}")
    for u in created_users:
        admin("DELETE", f"users/{u}")

print(f"\n{len(failures)} failure(s)" if failures else "\nall passed")
sys.exit(1 if failures else 0)

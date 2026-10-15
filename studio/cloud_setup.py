"""
One-time setup of the online planner's backend (Supabase), run on the laptop:

    .\\venv\\Scripts\\python cloud_setup.py

It walks through, asking only for what it can't find itself:
  1. signs the Supabase CLI in (opens the browser),
  2. links this folder to your Supabase project,
  3. creates the planning tables, security rules and storage (studio/cloud/supabase/migrations),
  4. stores the Anthropic key as a Supabase secret (read from .env, never printed),
  5. deploys the Edge Functions (Organise / Refine with Claude, team invites); no Docker needed,
  6. saves the project address and secret key in .env so the worker syncs,
  7. makes your owner account if there isn't one yet,
then prints the two things left to click: Supabase sign-in settings and the Netlify site.
Safe to run again; finished steps are quick no-ops. See studio/cloud/SETUP.md.
"""
import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CLOUD = HERE / "cloud"
ENV_FILE = HERE / ".env"
CLI = "supabase@2.121.0"
FUNCTIONS = ("plan-organise", "plan-develop", "team")  # supabase/functions/<name>; tests/ isn't one


def say(text: str = "") -> None:
    print(text, flush=True)


def step(n: int, text: str) -> None:
    say(f"\n[{n}/7] {text}")


def key_headers(key: str) -> dict:
    """A new secret key (sb_secret_...) isn't a JWT: it goes in apikey only.
    A legacy service_role key is a JWT and also goes in Authorization."""
    headers = {"apikey": key}
    if not key.startswith(("sb_secret_", "sb_publishable_")):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def project_ref(text: str) -> str:
    """Accept a bare ref, a dashboard URL or a https://<ref>.supabase.co address."""
    text = text.strip()
    m = (re.search(r"/project/([a-z0-9]{20})", text) or re.search(r"https?://([a-z0-9]{20})\.supabase\.co", text)
         or re.fullmatch(r"([a-z0-9]{20})", text))
    if not m:
        raise ValueError("that doesn't look like a Supabase project (20 lowercase letters/digits)")
    return m.group(1)


def set_env(path: Path, values: dict[str, str]) -> None:
    """Set KEY=value lines in .env, keeping everything else (comments, order) as is."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in values and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={values[key]}"
            seen.add(key)
    missing = [k for k in values if k not in seen]
    if missing:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Online planner (cloud_setup.py)")
        lines += [f"{k}={values[k]}" for k in missing]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_env(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"')
    return out


def npx(*args: str, check: bool = True) -> int:
    exe = shutil.which("npx")
    if not exe:
        raise SystemExit("Node.js isn't installed (npx not found). Install it: winget install OpenJS.NodeJS.LTS")
    result = subprocess.run([exe, "-y", CLI, *args], cwd=CLOUD)
    if check and result.returncode != 0:
        raise SystemExit(f"Stopped: `supabase {' '.join(args[:2])}` failed (see above). Fix that and run me again.")
    return result.returncode


def check_key(url: str, key: str) -> None:
    r = requests.get(f"{url}/rest/v1/laptop_status?select=id", headers=key_headers(key), timeout=20)
    if r.status_code != 200:
        raise ValueError(f"Supabase refused that key (HTTP {r.status_code}: {r.text[:120]})")


def owner_exists(url: str, key: str) -> bool:
    r = requests.get(f"{url}/rest/v1/profiles?select=id&role=eq.owner", headers=key_headers(key), timeout=20)
    r.raise_for_status()
    return bool(r.json())


def make_owner(url: str, key: str, email: str, name: str, password: str) -> None:
    """The first account created becomes the owner (database trigger)."""
    r = requests.post(f"{url}/auth/v1/admin/users", headers={**key_headers(key), "Content-Type": "application/json"},
                      json={"email": email, "password": password, "email_confirm": True,
                            "user_metadata": {"name": name}}, timeout=30)
    if r.status_code >= 300:
        raise ValueError(f"couldn't create the account (HTTP {r.status_code}: {r.text[:160]})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up the online planner's Supabase backend.")
    parser.add_argument("--skip-cli", action="store_true", help="skip steps 1-5 (already done)")
    args = parser.parse_args()
    env = read_env(ENV_FILE)
    say("Studio online planner setup. You need: a Supabase project (supabase.com, free plan,\n"
        "region Southeast Asia (Singapore)) and its dashboard open in your browser.")

    ref = None
    if env.get("STUDIO_SUPABASE_URL"):
        try:
            ref = project_ref(env["STUDIO_SUPABASE_URL"])
        except ValueError:
            ref = None
    while not ref:
        try:
            ref = project_ref(input("\nPaste your Supabase project's URL from the browser's address bar\n"
                                    "(https://supabase.com/dashboard/project/...): "))
        except ValueError as e:
            say(f"  {e}")
    url = f"https://{ref}.supabase.co"

    if not args.skip_cli:
        step(1, "Signing the Supabase CLI in (a browser window opens; approve it).")
        npx("login")
        step(2, f"Linking to project {ref} (it asks for the database password you chose when creating it).")
        npx("link", "--project-ref", ref)
        step(3, "Creating the planning tables, security rules and storage.")
        npx("db", "push")
        step(4, "Storing the Anthropic key as a Supabase secret.")
        anthropic = env.get("ANTHROPIC_API_KEY") or getpass.getpass("  Anthropic API key (not shown): ").strip()
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as f:
            f.write(f"ANTHROPIC_API_KEY={anthropic}\n")
        try:
            npx("secrets", "set", "--env-file", f.name)
        finally:
            os.unlink(f.name)
        step(5, "Deploying the Edge Functions (Organise / Refine with Claude, team invites).")
        npx("functions", "deploy", *FUNCTIONS, "--use-api")

    step(6, "Connecting the laptop.")
    say("  In the Supabase dashboard: Project Settings > API Keys. Copy the SECRET key\n"
        "  (sb_secret_..., press Reveal) or, under Legacy API keys, the service_role key.")
    key = env.get("STUDIO_SUPABASE_SERVICE_KEY", "")
    while True:
        if key:
            try:
                check_key(url, key)
                break
            except (ValueError, requests.RequestException) as e:
                say(f"  {e}")
        key = getpass.getpass("  Secret key (not shown): ").strip()
    planner = env.get("STUDIO_PLANNER_URL") or input(
        "  The planner's web address, if Netlify is already set up (Enter to skip for now): ").strip()
    values = {"STUDIO_SUPABASE_URL": url, "STUDIO_SUPABASE_SERVICE_KEY": key}
    if planner:
        values["STUDIO_PLANNER_URL"] = planner.rstrip("/")
    set_env(ENV_FILE, values)
    say(f"  Saved to {ENV_FILE}.")

    step(7, "Your owner account.")
    if owner_exists(url, key):
        say("  There's already an owner account. Skipping.")
    else:
        while True:
            email = input("  Your email: ").strip()
            name = input("  Your name: ").strip() or email.split("@")[0]
            password = getpass.getpass("  Choose a password (10+ characters, not shown): ")
            if len(password) < 10:
                say("  Too short.")
                continue
            try:
                make_owner(url, key, email, name, password)
                say(f"  Made the owner account for {email}.")
                break
            except (ValueError, requests.RequestException) as e:
                say(f"  {e}")

    say("""
Done with the backend. Two things left to click (details in studio/cloud/SETUP.md):

  A. Supabase dashboard > Authentication:
     - Sign In / Providers > Email: turn OFF "Allow new users to sign up" (invite only).
     - URL Configuration: Site URL = your planner address; add it to Redirect URLs too.
  B. Netlify: Add new site > Import from GitHub > eskm1/personal_assistant,
     branch claude/sleepy-thompson-iewk09, base directory studio/cloud/planner.
     Environment variables: SUPABASE_URL = """ + url + """
                            SUPABASE_ANON_KEY = the publishable key (sb_publishable_...) or legacy anon key.
     Then put the site's address in .env as STUDIO_PLANNER_URL (or run me again).

Restart Studio (account menu > Restart Studio). To move a plan you already made on the
laptop online: .\\venv\\Scripts\\python worker.py --cloud-import""")
    return 0


if __name__ == "__main__":
    sys.exit(main())

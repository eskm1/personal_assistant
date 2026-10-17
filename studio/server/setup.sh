#!/usr/bin/env bash
# Sets up a fresh Ubuntu 24.04 server (Hetzner Cloud) to run Studio: the worker
# and its dashboard, behind HTTPS. Run as root, in this order:
#
#   sudo bash setup.sh install studio.theurbanmakers.com   # system, firewall, HTTPS, deploy key
#   sudo bash setup.sh code claude/sleepy-thompson-iewk09  # get the code, Python and the renderer
#   sudo bash setup.sh start                               # run Studio as a service
#   sudo bash setup.sh status                              # is it running? recent log, disk use
#
# Every step is safe to run again: what's already done is checked and skipped.
set -Eeuo pipefail

STUDIO_USER=studio
STUDIO_HOME=/home/studio
REPO_DIR=$STUDIO_HOME/personal_assistant
APP_DIR=$REPO_DIR/studio
DATA_DIR=$STUDIO_HOME/Studio
DEPLOY_KEY=$STUDIO_HOME/.ssh/id_ed25519
KNOWN_HOSTS=$STUDIO_HOME/.ssh/known_hosts
# STUDIO_REPO_URL is for testing this script only (clone from a local copy).
REPO_URL=${STUDIO_REPO_URL:-git@github.com:eskm1/personal_assistant.git}
DEPLOY_KEY_PAGE=https://github.com/eskm1/personal_assistant/settings/keys/new
# GitHub's published ed25519 host key fingerprint (docs.github.com, "GitHub's SSH key fingerprints").
GITHUB_ED25519_FP=SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU
TIMEZONE=Asia/Singapore
# Set by server_move.py, which runs the next steps itself: leave out the "next, run ..." hints.
DRIVEN=${STUDIO_SETUP_DRIVEN:-}

NODE_MAJOR=22
# rclone: official release from GitHub, checked against its published SHA256SUMS.
RCLONE_VERSION=1.75.2
RCLONE_SHA256_amd64=349ac8fba6ff65d6247043f1750cdcb518ec5d500ef91463a10d37c0ccdf3702
RCLONE_SHA256_arm64=7e1e8d69654941b7b7df84ee74c5f7fb09cce7d5496947fb7bf16b55ff427d10

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

PACKAGES=(
  # basics
  ca-certificates curl gnupg unzip git openssh-client sudo ufw unattended-upgrades tzdata
  # video, Python (venv + headers and compilers in case a package has no ready-made wheel)
  ffmpeg python3 python3-venv python3-dev build-essential pkg-config
  # fonts for captions and graphics, including Chinese/Japanese/Korean and emoji
  fontconfig fonts-noto-core fonts-noto-cjk fonts-noto-color-emoji fonts-liberation
  # libraries the headless Chrome that HyperFrames renders with needs (Ubuntu 24.04 names)
  libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 libcairo2 libcups2t64
  libdbus-1-3 libdrm2 libexpat1 libgbm1 libglib2.0-0t64 libnspr4 libnss3 libpango-1.0-0
  libudev1 libvulkan1 libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3
  libxkbcommon0 libxrandr2 libxshmfence1
)

# ── Messages ─────────────────────────────────────────────────────────────────

CURRENT="starting"
step() { CURRENT=$*; echo "==> $*"; }
info() { echo "    $*"; }
warn() { echo "    NOTE: $*"; }
fail() { echo >&2; echo "ERROR: $*" >&2; exit 1; }
on_error() {
  echo >&2
  echo "ERROR: setup stopped at line $1 while: $CURRENT." >&2
  echo "The output above says what went wrong. Once it's fixed, run the same command again;" >&2
  echo "everything already done is skipped." >&2
}
trap 'on_error $LINENO' ERR

usage() {
  cat <<EOF
Studio server setup. Run as root, in this order:

  sudo bash $0 install <domain>    system packages, user, firewall, swap, HTTPS (Caddy),
                                   rclone, Node.js, and the GitHub deploy key
  sudo bash $0 code <branch>       get or update the code on that branch, Python packages,
                                   the renderer and its Chrome, then a test render
  sudo bash $0 start               install and (re)start the Studio service
  sudo bash $0 status              service state, last 30 log lines, disk use
EOF
  exit 1
}

# ── Helpers ──────────────────────────────────────────────────────────────────

has_systemd() { [ -d /run/systemd/system ]; }
in_container() { [ -f /.dockerenv ] || [ -f /run/.containerenv ] || grep -qa container= /proc/1/environ 2>/dev/null; }

# Run a command as the studio user, from a given folder.
as_studio_in() {
  local dir=$1; shift
  (cd "$dir" && runuser -u "$STUDIO_USER" -- env HOME="$STUDIO_HOME" USER="$STUDIO_USER" LOGNAME="$STUDIO_USER" "$@")
}
as_studio() { as_studio_in "$STUDIO_HOME" "$@"; }

apt_install() {
  DEBIAN_FRONTEND=noninteractive DEBCONF_NOWARNINGS=yes apt-get -o DPkg::Lock::Timeout=600 install -y -qq --no-install-recommends "$@" >/dev/null
}
apt_update() {
  apt-get -o DPkg::Lock::Timeout=600 update -qq >/dev/null || fail "apt-get update failed (no internet, or a package source is down). Try again in a few minutes."
}

# Write a file only when its content changed; says whether it did (0 = changed).
put_file() {
  local target=$1 mode=$2 tmp
  tmp=$(mktemp)
  cat >"$tmp"
  if [ -f "$target" ] && cmp -s "$tmp" "$target"; then rm -f "$tmp"; return 1; fi
  install -m "$mode" "$tmp" "$target"
  rm -f "$tmp"
}

need_root() { [ "$(id -u)" -eq 0 ] || fail "run this as root: sudo bash $0 $*"; }
need_user() { id "$STUDIO_USER" >/dev/null 2>&1 || fail "the studio user doesn't exist yet. Run first: sudo bash $0 install <domain>"; }

node_major() {
  local v
  v=$(node --version 2>/dev/null) || { echo 0; return; }
  v=${v#v}; echo "${v%%.*}"
}

# GitHub's host key in the studio user's known_hosts, checked against the published fingerprint.
github_host_key() {
  if [ -f "$KNOWN_HOSTS" ] && ssh-keygen -lF github.com -f "$KNOWN_HOSTS" 2>/dev/null | grep -qF "$GITHUB_ED25519_FP"; then
    info "github.com's host key is already trusted"
    return
  fi
  local scan fp
  scan=$(ssh-keyscan -T 15 -t ed25519 github.com 2>/dev/null | grep -v '^#' || true)
  [ -n "$scan" ] || fail "couldn't reach github.com (port 22) to read its SSH host key. Check the server's internet connection, then run this again."
  fp=$(printf '%s\n' "$scan" | ssh-keygen -lf - | awk '{print $2}')
  [ "$fp" = "$GITHUB_ED25519_FP" ] || fail "github.com answered with SSH key $fp, not GitHub's published $GITHUB_ED25519_FP. Not trusting it. Try again later; if it keeps happening, something between this server and GitHub is wrong."
  [ -f "$KNOWN_HOSTS" ] && ssh-keygen -R github.com -f "$KNOWN_HOSTS" >/dev/null 2>&1 || true
  printf '%s\n' "$scan" >>"$KNOWN_HOSTS"
  chown "$STUDIO_USER:$STUDIO_USER" "$KNOWN_HOSTS"
  chmod 644 "$KNOWN_HOSTS"
  rm -f "$KNOWN_HOSTS.old"
  info "github.com's host key checked ($GITHUB_ED25519_FP) and trusted"
}

print_deploy_key() {
  echo
  echo "    $(cat "$DEPLOY_KEY.pub")"
  echo
}

# ── install <domain> ─────────────────────────────────────────────────────────

cmd_install() {
  local domain=${1:-}
  [ -n "$domain" ] || usage
  [[ $domain =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$ ]] \
    || fail "'$domain' doesn't look like a domain name (expected something like studio.theurbanmakers.com)."
  need_root install "$domain"
  # shellcheck disable=SC1091
  . /etc/os-release
  [ "${VERSION_ID:-}" = "24.04" ] || fail "this script is written for Ubuntu 24.04; this server runs ${PRETTY_NAME:-something else}. In the Hetzner console: open the server > Rebuild > Ubuntu 24.04 (it's empty, nothing is lost), then run server_move.py again."

  step "1/11 System packages (ffmpeg, Python, fonts, Chrome libraries)"
  local missing=() p
  for p in "${PACKAGES[@]}"; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || missing+=("$p")
  done
  if [ ${#missing[@]} -eq 0 ]; then
    info "all ${#PACKAGES[@]} already installed"
  else
    info "installing ${#missing[@]} packages; the first time this takes a few minutes"
    apt_update
    apt_install "${missing[@]}"
  fi

  step "2/11 Time zone ($TIMEZONE, so folder names and logs match Singapore time)"
  if [ "$(readlink -f /etc/localtime)" != "/usr/share/zoneinfo/$TIMEZONE" ]; then
    ln -sf "/usr/share/zoneinfo/$TIMEZONE" /etc/localtime
    echo "$TIMEZONE" >/etc/timezone
    info "set"
  else
    info "already set"
  fi

  step "3/11 Node.js $NODE_MAJOR (NodeSource)"
  if [ "$(node_major)" = "$NODE_MAJOR" ]; then
    info "Node $(node --version) already installed"
  else
    install -d -m 755 /etc/apt/keyrings
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg \
      || fail "couldn't download NodeSource's signing key from deb.nodesource.com. Check the internet connection and try again."
    chmod 644 /etc/apt/keyrings/nodesource.gpg
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" \
      | put_file /etc/apt/sources.list.d/nodesource.list 644 || true
    # Prefer NodeSource's nodejs over Ubuntu's older one.
    printf 'Package: nodejs\nPin: origin deb.nodesource.com\nPin-Priority: 600\n' | put_file /etc/apt/preferences.d/nodejs 644 || true
    apt_update
    apt_install nodejs
    [ "$(node_major)" = "$NODE_MAJOR" ] || fail "Node.js $NODE_MAJOR didn't install (node --version says: $(node --version 2>&1))."
    info "Node $(node --version) installed"
  fi

  step "4/11 rclone $RCLONE_VERSION (for OneDrive)"
  if [ "$(rclone version 2>/dev/null | head -n1 || true)" = "rclone v$RCLONE_VERSION" ]; then
    info "already installed"
  else
    local arch sha tmp
    arch=$(dpkg --print-architecture)
    case $arch in
      amd64) sha=$RCLONE_SHA256_amd64 ;;
      arm64) sha=$RCLONE_SHA256_arm64 ;;
      *) fail "no rclone checksum recorded for this processor type ($arch)." ;;
    esac
    tmp=$(mktemp -d)
    curl -fsSL -o "$tmp/rclone.zip" "https://github.com/rclone/rclone/releases/download/v$RCLONE_VERSION/rclone-v$RCLONE_VERSION-linux-$arch.zip" \
      || { rm -rf "$tmp"; fail "couldn't download rclone from github.com. Check the internet connection and try again."; }
    echo "$sha  $tmp/rclone.zip" | sha256sum -c --quiet - >/dev/null 2>&1 \
      || { rm -rf "$tmp"; fail "the rclone download doesn't match its published checksum; not installing it. Try again later."; }
    unzip -q -j "$tmp/rclone.zip" "*/rclone" -d "$tmp"
    install -m 755 "$tmp/rclone" /usr/local/bin/rclone
    rm -rf "$tmp"
    info "$(rclone version | head -n1) installed (checksum verified)"
  fi

  step "5/11 The studio user and its folders"
  if id "$STUDIO_USER" >/dev/null 2>&1; then
    info "user $STUDIO_USER already exists"
  else
    useradd --create-home --shell /bin/bash "$STUDIO_USER"   # no password: SSH keys only
    info "user $STUDIO_USER created (no password; sign in with your SSH key)"
  fi
  install -d -m 700 -o "$STUDIO_USER" -g "$STUDIO_USER" "$STUDIO_HOME/.ssh" "$STUDIO_HOME/.config" "$STUDIO_HOME/.config/rclone"
  install -d -m 755 -o "$STUDIO_USER" -g "$STUDIO_USER" "$DATA_DIR" "$DATA_DIR/Inbox"
  [ -f "$STUDIO_HOME/.config/rclone/rclone.conf" ] && chown "$STUDIO_USER:$STUDIO_USER" "$STUDIO_HOME/.config/rclone/rclone.conf" \
    && chmod 600 "$STUDIO_HOME/.config/rclone/rclone.conf"
  local ak=$STUDIO_HOME/.ssh/authorized_keys added=0 line
  if [ -s /root/.ssh/authorized_keys ]; then
    touch "$ak"
    while IFS= read -r line || [ -n "$line" ]; do
      { [ -z "$line" ] || [[ $line == \#* ]]; } && continue
      grep -qxF -- "$line" "$ak" || { echo "$line" >>"$ak"; added=$((added + 1)); }
    done </root/.ssh/authorized_keys
    chown "$STUDIO_USER:$STUDIO_USER" "$ak"
    chmod 600 "$ak"
    info "SSH keys copied from root ($added new)"
  else
    warn "root has no SSH keys (/root/.ssh/authorized_keys), so none were copied to $STUDIO_USER"
  fi

  step "6/11 GitHub deploy key and github.com's host key"
  if [ -f "$DEPLOY_KEY" ]; then
    info "deploy key already exists"
  else
    as_studio ssh-keygen -q -t ed25519 -N "" -C "studio-server-deploy-key" -f "$DEPLOY_KEY"
    info "deploy key created"
  fi
  github_host_key

  step "7/11 Caddy (HTTPS for $domain)"
  if command -v caddy >/dev/null 2>&1; then
    info "$(caddy version | cut -d' ' -f1) already installed"
  else
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
      || fail "couldn't download Caddy's signing key from dl.cloudsmith.io. Check the internet connection and try again."
    chmod 644 /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    echo "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" \
      | put_file /etc/apt/sources.list.d/caddy-stable.list 644 || true
    apt_update
    apt_install caddy
    info "$(caddy version | cut -d' ' -f1) installed"
  fi
  [ -f "$SCRIPT_DIR/Caddyfile" ] || fail "the Caddyfile template is missing; keep it next to setup.sh ($SCRIPT_DIR)."
  local site
  site=$(mktemp)
  sed "s/__STUDIO_DOMAIN__/$domain/g" "$SCRIPT_DIR/Caddyfile" >"$site"
  caddy validate --adapter caddyfile --config "$site" >/dev/null 2>&1 \
    || { caddy validate --adapter caddyfile --config "$site" 2>&1 | tail -n 3 >&2; rm -f "$site"; fail "the Caddy site file isn't valid (see above)."; }
  install -d -m 755 /etc/caddy
  if put_file /etc/caddy/Caddyfile 644 <"$site"; then
    if has_systemd; then
      systemctl enable --quiet caddy
      systemctl reload caddy 2>/dev/null || systemctl restart caddy
      info "site $domain -> dashboard (127.0.0.1:8765) configured; Caddy reloaded"
    else
      info "site $domain configured (no systemd here, so Caddy wasn't started)"
    fi
  else
    info "site $domain already configured"
  fi
  rm -f "$site"

  step "8/11 Firewall (only SSH and web traffic in)"
  if in_container; then
    warn "skipped: this is a container, which can't run a firewall"
  else
    if ufw app info OpenSSH >/dev/null 2>&1; then ufw allow OpenSSH >/dev/null; else ufw allow 22/tcp >/dev/null; fi
    ufw allow 80/tcp >/dev/null
    ufw allow 443 >/dev/null  # TCP and UDP (HTTP/3)
    ufw default deny incoming >/dev/null
    ufw default allow outgoing >/dev/null
    ufw --force enable >/dev/null
    info "on: SSH, 80 and 443 open; everything else blocked"
  fi

  step "9/11 SSH: keys only, no password logins"
  if [ ! -d /etc/ssh/sshd_config.d ] || ! command -v sshd >/dev/null 2>&1; then
    warn "skipped: no SSH server on this machine"
  elif [ ! -s /root/.ssh/authorized_keys ] && [ ! -s "$STUDIO_HOME/.ssh/authorized_keys" ]; then
    warn "skipped: no SSH key is set up, and turning passwords off now would lock you out"
  else
    # 01- so it wins over cloud-init's 50-cloud-init.conf (sshd keeps the first value it reads).
    if printf '%s\n' "# Studio (setup.sh): sign in with SSH keys only." "PasswordAuthentication no" \
        "KbdInteractiveAuthentication no" "PermitRootLogin prohibit-password" \
        | put_file /etc/ssh/sshd_config.d/01-studio.conf 644; then
      install -d -m 755 /run/sshd
      sshd -t || { rm -f /etc/ssh/sshd_config.d/01-studio.conf; fail "the SSH settings didn't pass sshd's check; left SSH as it was."; }
      has_systemd && systemctl try-reload-or-restart ssh
      info "password logins turned off"
    else
      info "already keys only"
    fi
  fi

  step "10/11 Automatic security updates"
  if printf '%s\n' 'APT::Periodic::Update-Package-Lists "1";' 'APT::Periodic::Unattended-Upgrade "1";' \
      | put_file /etc/apt/apt.conf.d/20auto-upgrades 644; then
    info "turned on"
  else
    info "already on"
  fi

  step "11/11 Swap (4 GB, a safety net for memory peaks)"
  if in_container; then
    warn "skipped: this is a container, which can't add swap"
  elif [ -n "$(swapon --show=NAME --noheadings 2>/dev/null)" ]; then
    info "swap already on ($(swapon --show=NAME,SIZE --noheadings | tr -s ' ' | paste -sd, -))"
  else
    [ -f /swapfile ] || { fallocate -l 4G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=4096 status=none; chmod 600 /swapfile; mkswap /swapfile >/dev/null; }
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
    info "4 GB swap file on"
  fi

  echo
  echo "Done. The server is ready for the code."
  [ -z "$DRIVEN" ] || return 0
  echo
  echo "Give GitHub this server's deploy key (read-only access to the code):"
  print_deploy_key
  echo "1. Open $DEPLOY_KEY_PAGE"
  echo "2. Title: Studio server. Paste the key above. Leave \"Allow write access\" unticked. Add key."
  echo "3. Then run: sudo bash $0 code <branch>"
  echo
  echo "Also point $domain at this server: a GoDaddy A record to $(hostname -I 2>/dev/null | awk '{print $1}')."
}

# ── code <branch> ────────────────────────────────────────────────────────────

cmd_code() {
  local branch=${1:-}
  [ -n "$branch" ] || usage
  git check-ref-format --branch "$branch" >/dev/null 2>&1 || fail "'$branch' isn't a valid branch name."
  need_root code "$branch"
  need_user
  [ -f "$DEPLOY_KEY.pub" ] || fail "there's no deploy key yet. Run first: sudo bash $0 install <domain>"
  command -v node >/dev/null 2>&1 || fail "Node.js isn't installed. Run first: sudo bash $0 install <domain>"
  local git_env=(env GIT_TERMINAL_PROMPT=0
    "GIT_SSH_COMMAND=ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=20")

  step "1/6 Checking GitHub accepts this server's deploy key"
  [[ $REPO_URL == git@github.com:* ]] && github_host_key
  local out
  if ! out=$(as_studio "${git_env[@]}" git ls-remote --heads "$REPO_URL" "$branch" 2>&1); then
    local net="could not resolve|timed out|connection refused|network is unreachable|no route to host|connection closed"
    if grep -qiE "$net" <<<"$out"; then
      grep -iE "$net" <<<"$out" | head -n 2 >&2
      fail "couldn't connect to GitHub (see above). Check the server's internet connection, then run this again."
    fi
    echo >&2
    echo "ERROR: GitHub didn't accept this server's deploy key. The key is:" >&2
    echo "    $(cat "$DEPLOY_KEY.pub")" >&2
    echo >&2
    echo "Copy that whole line, add it at $DEPLOY_KEY_PAGE (read-only), then run this again." >&2
    exit 2
  fi
  grep -q "refs/heads/$branch\$" <<<"$out" || fail "GitHub has no branch called '$branch'. Check the name and run this again."
  info "access OK; branch $branch found"

  step "2/6 Getting the code ($branch)"
  if [ ! -d "$REPO_DIR/.git" ]; then
    [ ! -e "$REPO_DIR" ] || [ -z "$(ls -A "$REPO_DIR")" ] \
      || fail "$REPO_DIR exists but isn't a copy of the code. Move it aside, then run this again."
    as_studio "${git_env[@]}" git clone --quiet --branch "$branch" "$REPO_URL" "$REPO_DIR"
    info "downloaded"
  else
    local git=(as_studio_in "$REPO_DIR" "${git_env[@]}" git)
    [ -z "$("${git[@]}" status --porcelain --untracked-files=no)" ] \
      || fail "files in $REPO_DIR were changed by hand (git status shows them), so it can't update safely. Undo those changes, then run this again."
    "${git[@]}" fetch --quiet origin "$branch"
    if "${git[@]}" show-ref --verify --quiet "refs/heads/$branch"; then
      "${git[@]}" checkout --quiet "$branch"
    else
      "${git[@]}" checkout --quiet -b "$branch" --track "origin/$branch"
    fi
    "${git[@]}" merge --ff-only --quiet "origin/$branch" \
      || fail "the server's copy of $branch can't move forward cleanly to GitHub's (was the branch rewritten?)."
    "${git[@]}" branch --quiet --set-upstream-to="origin/$branch"
    info "up to date"
  fi
  info "at $(as_studio_in "$REPO_DIR" git log -1 --format='%h %s' | cut -c1-90)"
  if [ -f "$APP_DIR/.env" ]; then
    chown "$STUDIO_USER:$STUDIO_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
  fi

  step "3/6 Python packages (venv)"
  [ -x "$APP_DIR/venv/bin/python" ] || as_studio_in "$APP_DIR" python3 -m venv venv
  local req_hash marker=$APP_DIR/venv/.studio-requirements.sha256
  req_hash=$(sha256sum "$APP_DIR/requirements.txt" | cut -d' ' -f1)
  if [ "$(cat "$marker" 2>/dev/null || true)" = "$req_hash" ]; then
    info "already installed"
  else
    info "installing; the first time this takes a few minutes"
    as_studio_in "$APP_DIR" venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt
    echo "$req_hash" | as_studio tee "$marker" >/dev/null
    info "installed"
  fi

  step "4/6 Renderer (HyperFrames, npm ci)"
  local lock_hash npm_marker=$APP_DIR/render/node_modules/.studio-package-lock.sha256
  lock_hash=$(sha256sum "$APP_DIR/render/package-lock.json" | cut -d' ' -f1)
  if [ "$(cat "$npm_marker" 2>/dev/null || true)" = "$lock_hash" ]; then
    info "already installed"
  else
    as_studio_in "$APP_DIR/render" npm ci --no-audit --no-fund --update-notifier=false --loglevel=error >/dev/null
    echo "$lock_hash" | as_studio tee "$npm_marker" >/dev/null
    info "installed"
  fi

  step "5/6 Chrome for HyperFrames"
  local hf=(as_studio_in "$APP_DIR/render" env HYPERFRAMES_NO_UPDATE_CHECK=1 HYPERFRAMES_NO_TELEMETRY=1 node_modules/.bin/hyperframes)
  local log chrome
  log=$(mktemp)
  "${hf[@]}" browser ensure >"$log" 2>&1 || { tail -n 15 "$log" >&2; rm -f "$log"; fail "couldn't download HyperFrames' Chrome (see above). Run this again in a few minutes."; }
  rm -f "$log"
  chrome=$("${hf[@]}" browser path 2>/dev/null | tail -n1)
  [ -x "$chrome" ] || fail "HyperFrames didn't report a usable Chrome (got: '$chrome')."
  local libs
  libs=$(ldd "$chrome" 2>/dev/null | awk '/not found/ {print $1}' | paste -sd' ' - || true)
  [ -z "$libs" ] || fail "Chrome is missing system libraries: $libs. Run: sudo bash $0 install <domain>"
  info "$chrome"

  step "6/6 Checking it all works (imports, then a 2-second test render)"
  as_studio_in "$APP_DIR" venv/bin/python -c "import worker, dashboard, editor, render, transcribe, quality, cloud_sync" \
    || fail "the code doesn't load (see above)."
  local secs
  secs=$(as_studio_in "$APP_DIR" env HYPERFRAMES_NO_UPDATE_CHECK=1 HYPERFRAMES_NO_TELEMETRY=1 \
    venv/bin/python -c "import render; print(round(render.smoke_test()))" 2>&1 | tail -n1) \
    || fail "the test render failed: $secs"
  info "test render OK (${secs}s)"

  echo
  echo "Done. The code is ready on branch $branch."
  [ -z "$DRIVEN" ] || return 0
  [ -f "$APP_DIR/.env" ] || echo "Settings (.env) aren't on the server yet; put them in $APP_DIR/.env before starting."
  echo "Next: sudo bash $0 start"
}

# ── start ────────────────────────────────────────────────────────────────────

cmd_start() {
  need_root start
  need_user
  [ -x "$APP_DIR/venv/bin/python" ] && [ -f "$APP_DIR/worker.py" ] || fail "the code isn't set up yet. Run first: sudo bash $0 code <branch>"
  if ! has_systemd; then
    echo "This machine isn't running systemd (it's probably a test container), so Studio can't be"
    echo "installed as a service here. To run it by hand instead:"
    echo "    runuser -u $STUDIO_USER -- bash -c 'cd $APP_DIR && venv/bin/python worker.py'"
    exit 3
  fi
  [ -f "$APP_DIR/.env" ] || warn "no settings file yet ($APP_DIR/.env); Studio will start without API keys"

  step "1/2 Installing the Studio service"
  if put_file /etc/systemd/system/studio.service 644 <"$SCRIPT_DIR/studio.service"; then
    systemctl daemon-reload
    info "installed"
  else
    info "already up to date"
  fi
  systemctl enable --quiet studio

  step "2/2 (Re)starting Studio"
  systemctl restart studio
  local _
  for _ in $(seq 1 30); do
    curl -fs -o /dev/null --max-time 2 http://127.0.0.1:8765/ && break
    systemctl is-active --quiet studio || break
    sleep 1
  done
  if ! systemctl is-active --quiet studio; then
    journalctl -u studio -n 30 --no-pager >&2 || true
    fail "Studio didn't stay running; its last log lines are above."
  fi
  if curl -fs -o /dev/null --max-time 2 http://127.0.0.1:8765/; then
    info "running; the dashboard answers"
  else
    warn "running, but the dashboard isn't answering yet; check again with: sudo bash $0 status"
  fi
  echo
  cmd_status
}

# ── status ───────────────────────────────────────────────────────────────────

cmd_status() {
  need_root status
  if has_systemd; then
    echo "Studio service : $(systemctl is-active studio 2>/dev/null || true) (since $(systemctl show -p ActiveEnterTimestamp --value studio 2>/dev/null || echo '?'))"
    echo "HTTPS (Caddy)  : $(systemctl is-active caddy 2>/dev/null || true)"
  else
    echo "Studio service : no systemd on this machine (a test container?), so no service"
  fi
  if [ -d "$REPO_DIR/.git" ]; then
    echo "Code           : $(as_studio_in "$REPO_DIR" git log -1 --format='%h %s' | cut -c1-70)"
    echo "                 on $(as_studio_in "$REPO_DIR" git rev-parse --abbrev-ref HEAD), $(as_studio_in "$REPO_DIR" git log -1 --format='%cr')"
  fi
  echo "Disk           : $(df -h --output=used,size,pcent / | tail -n1 | awk '{print $1 " used of " $2 " (" $3 ")"}')"
  [ -d "$DATA_DIR" ] && echo "Studio data    : $(du -sh "$DATA_DIR" 2>/dev/null | cut -f1) in $DATA_DIR"
  if has_systemd; then
    echo
    echo "Last 30 log lines:"
    journalctl -u studio -n 30 --no-pager -o short-iso 2>/dev/null || true
  fi
}

# ── main ─────────────────────────────────────────────────────────────────────

case ${1:-} in
  install) shift; cmd_install "$@" ;;
  code) shift; cmd_code "$@" ;;
  start) cmd_start ;;
  status) cmd_status ;;
  *) usage ;;
esac

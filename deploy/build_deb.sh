#!/usr/bin/env bash
# Build a self-contained Debian package for the OpenTyper server.
#
# The deb ships the source, a uv-built venv AND its Python interpreter
# (/opt/opentyper/.uv-python — the same layout install.sh proves works under
# ProtectHome=true), so the target machine needs no network, no uv, no python3.
# State lives in /opt/opentyper/data (created on install, never removed by
# the package) and /etc/opentyper/opentyper.env is a conffile for overrides.
#
# Usage: ./deploy/build_deb.sh [--out DIR]     (no root needed; dpkg-deb only)
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$(pwd)"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
[[ -n "$VERSION" ]] || { echo "cannot read version from pyproject.toml" >&2; exit 1; }

OUT="dist"
[[ "${1:-}" == "--out" && -n "${2:-}" ]] && { OUT="$2"; shift 2; }
command -v uv >/dev/null 2>&1 || { echo "uv is required on the build machine" >&2; exit 1; }
command -v dpkg-deb >/dev/null 2>&1 || { echo "dpkg-deb is required" >&2; exit 1; }

STAGING="$(mktemp -d /tmp/opentyper-deb-XXXXXX)"
trap 'rm -rf "$STAGING"' EXIT
echo "[deb] staging: $STAGING (version $VERSION)"

# --- payload: source tree, same excludes as install.sh -------------------------
mkdir -p "$STAGING/opt/opentyper"
rsync -a \
  --exclude "data/" --exclude ".venv/" --exclude ".uv-python/" \
  --exclude "logs/" --exclude "__pycache__/" --exclude ".pytest_cache/" \
  --exclude ".ruff_cache/" --exclude "dist/" --exclude ".git/" \
  --exclude ".env" \
  "$ROOT/" "$STAGING/opt/opentyper/"

# --- self-contained runtime: interpreter + venv inside the package -------------
# interpreter source, in order: the deb build cache (known-good), any uv-managed
# cpython-3.12 already on the machine, else one download into the cache. Never
# download per build — uv re-fetches into every fresh UV_PYTHON_INSTALL_DIR.
# Note: ship a recent 3.12 patch build; the old 3.12.0 standalone build fails
# thread creation under the unit's hardening (RuntimeError: can't start new
# thread), while 3.12.15+ works.
UV_MANAGED="${XDG_DATA_HOME:-$HOME/.local/share}/uv/python"
PYCACHE="${XDG_CACHE_HOME:-$HOME/.cache}/opentyper-deb/python"
if compgen -G "$PYCACHE/cpython-3.12*" >/dev/null; then
  SRC_PYTHON="$PYCACHE"
  echo "[deb] seeding interpreter from build cache $PYCACHE"
elif compgen -G "$UV_MANAGED/cpython-3.12*" >/dev/null; then
  SRC_PYTHON="$UV_MANAGED"
  echo "[deb] seeding interpreter from $UV_MANAGED"
else
  echo "[deb] downloading interpreter (cached in $PYCACHE)"
  UV_PYTHON_INSTALL_DIR="$PYCACHE" uv python install --quiet
  SRC_PYTHON="$PYCACHE"
fi
mkdir -p "$STAGING/opt/opentyper/.uv-python"
rsync -a "$SRC_PYTHON/" "$STAGING/opt/opentyper/.uv-python/"
echo "[deb] building venv (uv sync --frozen --no-dev)"
(cd "$STAGING/opt/opentyper" \
  && uv sync --frozen --no-dev --quiet \
       --python "$(find "$STAGING/opt/opentyper/.uv-python" \
                    -path "*cpython-3.12*" \( -type f -o -type l \) \
                    -name python3 | sort | head -1)")
# guard: uv sync silently reuses an existing interpreter if allowed to — the
# whole point here is a venv that works offline on the target machine
case "$(readlink -f "$STAGING/opt/opentyper/.venv/bin/python")" in
  "$STAGING"/*) ;;
  *) echo "ERROR: venv interpreter is not inside the package:" >&2
     readlink -f "$STAGING/opt/opentyper/.venv/bin/python" >&2; exit 1 ;;
esac
"$STAGING/opt/opentyper/.venv/bin/python" \
  -c "import fastapi, uvicorn, httpx, cryptography, pydantic, edge_tts" \
  || { echo "ERROR: staged venv import check failed" >&2; exit 1; }
APP_VENV="$STAGING/opt/opentyper/.venv"
# console-script shebangs, activate scripts and pyvenv.cfg bake in the staging
# path — rewrite every text file to the real install path, then re-point links
# anywhere in the payload (uv's version-alias dirs in .uv-python are symlinks
# too). The payload then only resolves under /opt/opentyper, i.e. on the target.
grep -rIlZ "$STAGING/opt/opentyper" "$APP_VENV" 2>/dev/null \
  | xargs -0 -r sed -i "s|$STAGING/opt/opentyper|/opt/opentyper|g"
find "$STAGING/opt/opentyper" -type l | while read -r link; do
  target="$(readlink "$link")"
  case "$target" in
    "$STAGING"/*) ln -sfn "${target/"$STAGING"/}" "$link" ;;
  esac
done
find "$STAGING/opt/opentyper" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
mkdir -p "$STAGING/DEBIAN"

# --- systemd unit (hardened; mirrors deploy/install.sh) ------------------------
mkdir -p "$STAGING/usr/lib/systemd/system" "$STAGING/etc/opentyper" \
         "$STAGING/usr/share/doc/opentyper"
cat > "$STAGING/usr/lib/systemd/system/opentyper.service" <<'EOF'
# OpenTyper systemd unit (shipped by the opentyper package)
[Unit]
Description=OpenTyper - self-hosted BYOK chat UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=opentyper
Group=opentyper
WorkingDirectory=/opt/opentyper
Environment=OPENTYPER_DATA_DIR=/opt/opentyper/data
Environment=OPENTYPER_HOST=127.0.0.1
Environment=OPENTYPER_PORT=8000
EnvironmentFile=-/etc/opentyper/opentyper.env
ExecStart=/opt/opentyper/.venv/bin/python -m uvicorn app.main:app --host ${OPENTYPER_HOST} --port ${OPENTYPER_PORT}
Restart=on-failure
RestartSec=3

# hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/opt/opentyper/data /var/log/opentyper
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryDenyWriteExecute=true
RestrictNamespaces=true
SystemCallArchitectures=native
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
EOF

# conffile: survives package upgrades; admin overrides live here
cat > "$STAGING/etc/opentyper/opentyper.env" <<'EOF'
# OpenTyper service environment overrides (conffile — edits survive upgrades).
# OPENTYPER_DATA_DIR=/opt/opentyper/data
# Examples:
#   OPENTYPER_PORT=8010       (service bind port — 8000 is the default)
#   OPENTYPER_HOST=127.0.0.1  (bind address; 0.0.0.0 to serve the LAN)
#   OPENAI_API_KEY=...        (alternatively set keys in the UI; stored encrypted)
#   OPENTYPER_FERNET_KEY=...  (else data/secret.key is generated — back it up!)
EOF

install -m 0644 README.md "$STAGING/usr/share/doc/opentyper/README.md"
cat > "$STAGING/usr/share/doc/opentyper/copyright" <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0
Upstream-Name: OpenTyper
Source: local build

Files: *
Copyright: 2026 OpenTyper authors
License: Distributed with the OpenTyper source; see the upstream repository.
EOF

# --- maintainer scripts ---------------------------------------------------------
cat > "$STAGING/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
APP_DIR=/opt/opentyper
WAS_ACTIVE=0
if [ -d /run/systemd/system ] && systemctl is-active --quiet opentyper.service; then
  WAS_ACTIVE=1
fi
case "$1" in
  configure)
    # ensure user+group exist with the group as primary (install.sh may have
    # left a user whose primary group is nogroup — chown opentyper:opentyper
    # would fail and abort the whole install)
    if ! getent group opentyper >/dev/null; then
      groupadd --system opentyper
    fi
    if ! id -u opentyper >/dev/null 2>&1; then
      if command -v adduser >/dev/null 2>&1; then
        adduser --system --quiet --ingroup opentyper \
          --shell /usr/sbin/nologin --home "$APP_DIR" opentyper
      else
        useradd --system --gid opentyper --shell /usr/sbin/nologin \
          --home-dir "$APP_DIR" opentyper
      fi
    elif [ "$(id -g opentyper)" != "$(getent group opentyper | cut -d: -f3)" ]; then
      usermod -g opentyper opentyper
    fi
    mkdir -p "$APP_DIR/data" /var/log/opentyper
    chown opentyper:opentyper "$APP_DIR/data" /var/log/opentyper
    chmod 700 "$APP_DIR/data"
    for d in "$APP_DIR/.venv" "$APP_DIR/.uv-python"; do
      [ -d "$d" ] && chown -R opentyper:opentyper "$d"
    done
    if [ -d /run/systemd/system ]; then
      # health-check the configured port (conffile may override the default)
      PORT="$(grep -E '^OPENTYPER_PORT=' /etc/opentyper/opentyper.env 2>/dev/null \
        | tail -1 | cut -d= -f2 | tr -d ' \"')"
      PORT="${PORT:-8000}"
      HOST="$(grep -E '^OPENTYPER_HOST=' /etc/opentyper/opentyper.env 2>/dev/null \
        | tail -1 | cut -d= -f2 | tr -d ' \"')"
      HOST="${HOST:-127.0.0.1}"
      # an unowned legacy /etc unit (e.g. from deploy/install.sh) shadows the
      # packaged one — retire it so the deb's hardened unit actually runs
      if [ -f /etc/systemd/system/opentyper.service ] \
         && ! dpkg -S /etc/systemd/system/opentyper.service >/dev/null 2>&1; then
        mv /etc/systemd/system/opentyper.service \
           /etc/systemd/system/opentyper.service.pre-deb
        echo "opentyper: legacy /etc unit moved to opentyper.service.pre-deb"
      fi
      systemctl daemon-reload || true
      if [ "$WAS_ACTIVE" = 1 ]; then
        # upgrade: enable --now is a no-op on a running unit — restart so the
        # service actually picks up the new code and interpreter
        systemctl enable opentyper.service || true
        systemctl restart opentyper.service || true
      else
        systemctl enable --now opentyper.service || true
      fi
      sleep 2
      if "$APP_DIR/.venv/bin/python" -c \
        "import urllib.request;urllib.request.urlopen('http://$HOST:$PORT/api/health',timeout=2)" \
        2>/dev/null; then
        echo "opentyper: health check OK (http://$HOST:$PORT)"
      else
        echo "opentyper: service enabled; health check did not answer yet" >&2
        echo "  check: systemctl status opentyper · journalctl -u opentyper -n 50" >&2
      fi
    fi
    ;;
esac
EOF

cat > "$STAGING/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ -d /run/systemd/system ] && [ "$1" = remove ]; then
  systemctl stop opentyper.service || true
fi
# runtime-generated .pyc make dpkg's directory cleanup warn on purge
find /opt/opentyper -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
EOF

cat > "$STAGING/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = purge ]; then
  # deliberate: chats and the Fernet key are irreplaceable — print, never delete
  echo "opentyper: chat data kept at /opt/opentyper/data (delete manually if intended)."
fi
if [ -d /run/systemd/system ]; then
  systemctl daemon-reload || true
fi
EOF

# package metadata
cat > "$STAGING/DEBIAN/control" <<EOF
Package: opentyper
Version: $VERSION
Section: web
Priority: optional
Architecture: amd64
Depends: libc6 (>= 2.35)
Maintainer: OpenTyper <opentyper@localhost>
Description: Self-hosted bring-your-own-API-key chat UI for LLMs
 FastAPI + SQLite backend, vanilla JS frontend, SSE streaming, provider
 keys encrypted at rest. Installs a hardened systemd service bound to
 127.0.0.1:8000. Self-contained: ships its own Python runtime; the target
 machine needs no network access or Python install.
 .
 Chat state lives in /opt/opentyper/data (never touched by upgrades or
 purge) and /etc/opentyper/opentyper.env is a conffile for overrides.
EOF

chmod 755 "$STAGING/DEBIAN/postinst" "$STAGING/DEBIAN/prerm" "$STAGING/DEBIAN/postrm"

mkdir -p "$OUT"
DEB="$OUT/opentyper_${VERSION}_amd64.deb"
dpkg-deb --build --root-owner-group "$STAGING" "$DEB" >/dev/null
echo "[deb] built: $DEB ($(du -h "$DEB" | cut -f1))"
dpkg-deb --info "$DEB" | sed -n '1,12p'

#!/usr/bin/env bash
# Update the Django stack and compile/install the supported command EA only.
# This script preserves Docker volumes and never attaches an EA or places trades.
set -Eeuo pipefail

REPO_DIR="${RAMON_REPO_DIR:-$HOME/Documents/Presentation/ramon-ai-trader-bridge}"
BRANCH="${RAMON_GIT_BRANCH:-main}"
WINEPREFIX="${RAMON_MT5_PREFIX:-$HOME/.mt5}"
TERMINAL_ID="${RAMON_MT5_TERMINAL_ID:-D0E8209F77C8CF37AD8BF550E51FF075}"
EXPERTS_DIR="$WINEPREFIX/drive_c/users/amir/AppData/Roaming/MetaQuotes/Terminal/$TERMINAL_ID/MQL5/Experts"
COMPOSE_FILE=docker-compose.yaml
HEALTH_URL="${RAMON_HEALTH_URL:-http://127.0.0.1:8017/healthz}"
EA_SOURCES=(mt5/RamonCommandEA_v1.03.mq5)

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
for cmd in git docker curl wine winepath timeout tr grep find cp seq sleep date mkdir basename cat rm; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd is required"
done
[[ -d "$REPO_DIR/.git" ]] || die "Repository not found: $REPO_DIR"
cd "$REPO_DIR"
[[ "$(git branch --show-current)" == "$BRANCH" ]] || die "Switch to $BRANCH before running."
[[ -f "$COMPOSE_FILE" && -f .env ]] || die "Missing docker-compose.yaml or local .env."
for source in "${EA_SOURCES[@]}"; do [[ -s "$source" ]] || die "Missing EA source: $source"; done
[[ -z "$(git status --porcelain)" ]] || die "Working tree is not clean; commit or back up changes first."

info "Fast-forwarding $BRANCH"
before_update_sha="$(git rev-parse HEAD)"
git fetch origin "$BRANCH"
git pull --ff-only origin "$BRANCH"
after_update_sha="$(git rev-parse HEAD)"
if [[ "$before_update_sha" != "$after_update_sha" ]]; then
  info "Repository updated; restarting with the freshly pulled deployment script"
  exec bash "$0"
fi

info "Checking Docker and MT5 prerequisites"
docker info >/dev/null 2>&1 || die "Docker daemon is not reachable"
[[ -d "$WINEPREFIX/drive_c" ]] || die "Wine prefix not found: $WINEPREFIX"
METAEDITOR="${RAMON_METAEDITOR:-}"
if [[ -z "$METAEDITOR" ]]; then
  for candidate in "$WINEPREFIX/drive_c/Program Files/MetaTrader 5/metaeditor64.exe" "$WINEPREFIX/drive_c/Program Files/MetaTrader 5/metaeditor.exe" "$WINEPREFIX/drive_c/Program Files (x86)/MetaTrader 5/metaeditor64.exe"; do
    if [[ -f "$candidate" ]]; then METAEDITOR="$candidate"; break; fi
  done
fi
[[ -n "$METAEDITOR" && -f "$METAEDITOR" ]] || METAEDITOR="$(find "$WINEPREFIX/drive_c" -type f \( -iname metaeditor64.exe -o -iname metaeditor.exe \) -print -quit 2>/dev/null || true)"
[[ -n "$METAEDITOR" && -f "$METAEDITOR" ]] || die "MetaEditor not found; set RAMON_METAEDITOR."
[[ -d "$EXPERTS_DIR" ]] || die "Active MT5 Experts directory not found: $EXPERTS_DIR"

info "Validating and deploying Compose stack"
docker compose -f "$COMPOSE_FILE" config --quiet
docker compose -f "$COMPOSE_FILE" up -d --build
docker compose -f "$COMPOSE_FILE" exec -T django python manage.py migrate --noinput
docker compose -f "$COMPOSE_FILE" exec -T django python manage.py check
stack_ready=0
for attempt in $(seq 1 40); do
  django_state="$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' ramon-django 2>/dev/null || true)"
  redis_state="$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' ramon-redis 2>/dev/null || true)"
  db_state="$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' ramon-timescaledb 2>/dev/null || true)"
  worker_state="$(docker inspect -f '{{.State.Status}}' ramon-worker 2>/dev/null || true)"
  if [[ "$django_state" == "running healthy" && "$redis_state" == "running healthy" && "$db_state" == "running healthy" && "$worker_state" == "running" ]] && curl --fail --silent --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
    stack_ready=1
    break
  fi
  sleep 3
done
if [[ "$stack_ready" != 1 ]]; then
  docker compose -f "$COMPOSE_FILE" ps
  docker compose -f "$COMPOSE_FILE" logs --tail=100 django worker redis timescaledb
  die "Compose stack did not reach healthy/running state (Django=$django_state Redis=$redis_state TimescaleDB=$db_state Worker=$worker_state)"
fi
docker compose -f "$COMPOSE_FILE" exec -T redis redis-cli ping | grep -qx PONG || die "Redis ping failed"
docker compose -f "$COMPOSE_FILE" exec -T timescaledb sh -lc 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' || die "TimescaleDB readiness check failed"
docker compose -f "$COMPOSE_FILE" ps

# The command EA uses only the standard MQL5 Trade include.

STAMP="$(date '+%Y%m%d-%H%M%S')"
BACKUP_DIR="$HOME/.local/share/ramon-ea-backups/$STAMP"
mkdir -p "$BACKUP_DIR"
for source in "${EA_SOURCES[@]}"; do
  base="$(basename "$source")"
  for old in "$EXPERTS_DIR/$base" "$EXPERTS_DIR/${base%.mq5}.ex5"; do [[ ! -f "$old" ]] || cp -p "$old" "$BACKUP_DIR/"; done
done

compile_install() {
  local source="$1" base target log_file ex5 winpath status normalized
  base="$(basename "$source")"; target="$EXPERTS_DIR/$base"
  log_file="${target%.mq5}.log"; ex5="${target%.mq5}.ex5"
  cp "$source" "$target"
  rm -f "$log_file" "$ex5"
  winpath="$(WINEPREFIX="$WINEPREFIX" winepath -w "$target")"
  [[ -n "$winpath" ]] || die "winepath returned an empty path for $target"
  [[ "$winpath" != *" "* ]] || die "Wine compiler path contains spaces: $winpath"
  info "Compiling $base"
  printf 'MetaEditor: %s\nSource: %s\n' "$METAEDITOR" "$winpath"
  set +e
  WINEPREFIX="$WINEPREFIX" timeout 180s wine "$METAEDITOR" "/compile:$winpath" /log
  status=$?
  set -e
  [[ -s "$log_file" ]] || die "No compiler log for $base (exit=$status). Backup: $BACKUP_DIR"
  cat "$log_file"
  normalized="$(tr -d '\000' < "$log_file")"
  if ! printf '%s\n' "$normalized" | grep -Eiq '^[[:space:]]*Result:[[:space:]]*0 errors,[[:space:]]*0 warnings([,[:space:]]|$)'; then
    printf '%s\n' "$normalized" | grep -Ei 'Result:|error|warning' || true
    die "Compiler validation failed for $base (MetaEditor exit=$status). Backup: $BACKUP_DIR"
  fi
  [[ -s "$ex5" ]] || die "No output binary for $base despite success summary. Backup: $BACKUP_DIR"
  printf 'PASS: %s\nBinary: %s\n' "$base" "$ex5"
}

for source in "${EA_SOURCES[@]}"; do compile_install "$source"; done
printf '\nDEPLOYMENT CHECKS PASSED\nDjango: %s\nExperts: %s\nBackups: %s\n' "$HEALTH_URL" "$EXPERTS_DIR" "$BACKUP_DIR"
printf 'Installed binary: RamonCommandEA_v1.03.ex5\n'
printf 'MT5 was not launched, no EA was attached, and no trades were placed. Server command flags are not changed.\n'

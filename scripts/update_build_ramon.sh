#!/usr/bin/env bash
# Pull Ramon main, rebuild the single Compose stack, migrate/check Django, and
# compile/install the repository's current command EA into the active MT5 terminal.
# This script never enables command execution and never removes Docker volumes.
set -Eeuo pipefail

REPO_DIR="${RAMON_REPO_DIR:-$HOME/Documents/Presentation/ramon-ai-trader-bridge}"
BRANCH="${RAMON_GIT_BRANCH:-main}"
WINEPREFIX="${RAMON_MT5_PREFIX:-$HOME/.mt5}"
TERMINAL_ID="${RAMON_MT5_TERMINAL_ID:-D0E8209F77C8CF37AD8BF550E51FF075}"
EXPERTS_DIR="$WINEPREFIX/drive_c/users/amir/AppData/Roaming/MetaQuotes/Terminal/$TERMINAL_ID/MQL5/Experts"
COMPOSE_FILE="docker-compose.yaml"
EA_SOURCE="mt5/RamonCommandEA_v1.02.mq5"
EA_TARGET="$EXPERTS_DIR/RamonCommandEA_v1.02.mq5"
HEALTH_URL="${RAMON_HEALTH_URL:-http://127.0.0.1:8017/healthz}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

command -v git >/dev/null || die "git is required"
command -v docker >/dev/null || die "docker is required"
command -v curl >/dev/null || die "curl is required"
command -v wine >/dev/null || die "wine is required to compile MQL5 on this host"
command -v winepath >/dev/null || die "winepath is required to convert the EA path for MetaEditor"

[[ -d "$REPO_DIR/.git" ]] || die "Repository not found at $REPO_DIR. Clone it first or set RAMON_REPO_DIR."
cd "$REPO_DIR"
[[ "$(git branch --show-current)" == "$BRANCH" ]] || die "Switch to '$BRANCH' before running (current: $(git branch --show-current))."
[[ -f "$COMPOSE_FILE" ]] || die "Missing $COMPOSE_FILE."
[[ -f .env ]] || die "Missing .env. Create it locally; this script will never generate or overwrite secrets."
[[ -f "$EA_SOURCE" ]] || die "Missing current command EA source: $EA_SOURCE"

if [[ -n "$(git status --porcelain)" ]]; then
  die "Working tree is not clean. Commit or back up your local changes before pulling; nothing has been changed."
fi

info "Fetching and fast-forwarding $BRANCH"
git fetch origin "$BRANCH"
git pull --ff-only origin "$BRANCH"

info "Validating the single Docker Compose configuration"
docker compose -f "$COMPOSE_FILE" config --quiet

info "Building and starting services (persistent volumes are preserved)"
docker compose -f "$COMPOSE_FILE" up -d --build

info "Applying forward Django migrations"
docker compose -f "$COMPOSE_FILE" exec -T django python manage.py migrate --noinput
docker compose -f "$COMPOSE_FILE" exec -T django python manage.py check

info "Waiting for Django health endpoint"
healthy=0
for attempt in $(seq 1 40); do
  if curl --fail --silent --show-error --max-time 3 "$HEALTH_URL" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 3
done
[[ "$healthy" == 1 ]] || {
  docker compose -f "$COMPOSE_FILE" ps
  docker compose -f "$COMPOSE_FILE" logs --tail=100 django
  die "Django did not become healthy at $HEALTH_URL"
}
docker compose -f "$COMPOSE_FILE" ps

info "Locating MetaEditor inside WINEPREFIX=$WINEPREFIX"
[[ -d "$WINEPREFIX/drive_c" ]] || die "Wine prefix not found: $WINEPREFIX"
METAEDITOR="${RAMON_METAEDITOR:-}"
if [[ -z "$METAEDITOR" ]]; then
  for candidate in \
    "$WINEPREFIX/drive_c/Program Files/MetaTrader 5/metaeditor64.exe" \
    "$WINEPREFIX/drive_c/Program Files/MetaTrader 5/metaeditor.exe" \
    "$WINEPREFIX/drive_c/Program Files (x86)/MetaTrader 5/metaeditor64.exe"; do
    if [[ -f "$candidate" ]]; then METAEDITOR="$candidate"; break; fi
  done
fi
if [[ -z "$METAEDITOR" ]]; then
  METAEDITOR="$(find "$WINEPREFIX/drive_c" -type f \( -iname metaeditor64.exe -o -iname metaeditor.exe \) -print -quit 2>/dev/null || true)"
fi
[[ -n "$METAEDITOR" && -f "$METAEDITOR" ]] || die "MetaEditor was not found. Set RAMON_METAEDITOR to its exact .exe path."
[[ -d "$EXPERTS_DIR" ]] || die "Active MT5 Experts directory not found: $EXPERTS_DIR. Set RAMON_MT5_TERMINAL_ID if needed."
mkdir -p "$EXPERTS_DIR"

STAMP="$(date '+%Y%m%d-%H%M%S')"
BACKUP_DIR="$HOME/.local/share/ramon-ea-backups/$STAMP"
mkdir -p "$BACKUP_DIR"
for old in "$EA_TARGET" "${EA_TARGET%.mq5}.ex5"; do
  if [[ -f "$old" ]]; then cp -p "$old" "$BACKUP_DIR/"; fi
done

info "Installing the exact repository source for RamonCommandEA v1.02"
cp "$EA_SOURCE" "$EA_TARGET"
rm -f "${EA_TARGET%.mq5}.log"
LOG_FILE="${EA_TARGET%.mq5}.log"
export WINEPREFIX
set +e
WINDOWS_EA_TARGET="$(winepath -w "$EA_TARGET")"
WINDOWS_LOG_FILE="$(winepath -w "$LOG_FILE")"
compile_arg="/compile:\"$WINDOWS_EA_TARGET\""
log_arg="/log:\"$WINDOWS_LOG_FILE\""
timeout 180s wine "$METAEDITOR" "$compile_arg" "$log_arg"
compile_status=$?
set -e
[[ -f "$LOG_FILE" ]] || die "MetaEditor produced no log (exit=$compile_status). Previous files, if any, are backed up in $BACKUP_DIR."
cat "$LOG_FILE"
if [[ "$compile_status" -ne 0 ]] || ! grep -Eiq '0 errors,[[:space:]]*0 warnings' "$LOG_FILE"; then
  die "EA compilation did not report zero errors and zero warnings. Inspect $LOG_FILE; backup: $BACKUP_DIR"
fi
[[ -s "${EA_TARGET%.mq5}.ex5" ]] || die "Compilation log succeeded but EX5 is missing. Backup: $BACKUP_DIR"

info "Success: Compose is running and RamonCommandEA_v1.02 compiled."
printf 'EA source: %s\nEA binary: %s\nBackup: %s\n' "$EA_TARGET" "${EA_TARGET%.mq5}.ex5" "$BACKUP_DIR"
printf '\nSafety notes:\n'
printf ' - Command execution remains disabled by the source default; this script does not enable server flags or EA inputs.\n'
printf ' - This script does not attach an EA to a chart or place trades.\n'
printf ' - Ramon_v0.62.x strategy sources are not in this GitHub repository, so they are deliberately not overwritten or compiled by this script.\n'
printf ' - Keep the strategy EA detached until its exact source is reviewed for independent OrderSend/CTrade calls and duplicate execution risk.\n'

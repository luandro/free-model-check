#!/usr/bin/env bash
#
# free-models-refresh.sh — the only entry point allowed to touch the free-model
# catalogue.
#
# Caller contract
# ---------------
# Two systemd timers drive this script:
#   * refresh.timer  -> runs the full pipeline (filter + probe)
#   * probe.timer    -> runs with --skip-filter (probe only)
# Manual use is also supported: run it directly to force a refresh.
#
# Lock semantics
# --------------
# The script takes an exclusive flock on $STATE_DIR/refresh.lock (5s wait) before
# doing anything else. If the lock cannot be acquired it gives up with exit 75 so
# a concurrent timer tick never double-runs the filter or the probe. The wait is
# short on purpose: flock returns success after the wait elapses, so a long
# timeout would queue concurrent ticks instead of skipping them.
#
# The lock is taken BEFORE credentials are loaded, and the catalogue is only ever
# written inside $STATE_DIR (staging lives there too, so the final `mv` never
# crosses a filesystem and stays atomic per file).
#
set -euo pipefail

STATE_DIR="${STATE_DIR:-$HOME/.software-factory}"
REPO_DIR="${REPO_DIR:-/home/luandro/Dev/AI/free-model-check}"
LOCK_FILE="$STATE_DIR/refresh.lock"
ENV_FILE="$STATE_DIR/free-models.env"
MANIFEST="$STATE_DIR/installed-manifest"
CATALOGUE="$STATE_DIR/free_models.json"
PROBE="$STATE_DIR/free-models-probe.py"
STATE_FILE="$STATE_DIR/free_models_junior.json"

usage() {
    cat <<'EOF'
Usage: free-models-refresh.sh [--skip-filter] [--dry-run] [-h|--help]

  --skip-filter   Do not run the catalogue filter; only run the probe
  --dry-run       Print the commands that would run and exit 0
  -h, --help      Show this help and exit 0
EOF
}

SKIP_FILTER=0
DRY_RUN=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --skip-filter) SKIP_FILTER=1 ;;
        --dry-run)     DRY_RUN=1 ;;
        -h|--help)     usage; exit 0 ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if [ "$DRY_RUN" -eq 1 ]; then
    echo "mkdir -p \"$STATE_DIR\""
    echo "exec 9>\"$LOCK_FILE\"; flock -w 5 9"
    if [ "$SKIP_FILTER" -eq 0 ]; then
        echo "timeout 300 python3 \"$REPO_DIR/free_model_filter.py\" --require-agent-ready --output-prefix \"$STATE_DIR/staging-\$\$/free_models\""
        echo "# validate staged catalogue, then mv -f each free_models* into \"$STATE_DIR\""
    fi
    echo "python3 \"$PROBE\" --catalogue \"$CATALOGUE\" --state \"$STATE_FILE\" --env-file \"$ENV_FILE\""
    exit 0
fi

# 1. Ensure the state directory exists.
mkdir -p "$STATE_DIR"

# 2. Acquire the exclusive lock before loading credentials.
# A short wait only: flock returns 0 once the wait elapses, so a long timeout
# would queue concurrent ticks instead of skipping them.
# The system flock is preferred: some homebrew builds return 0 without ever
# taking the lock, which would silently let two sweeps run at once.
FLOCK_BIN=""
for candidate in /usr/bin/flock /bin/flock; do
    if [ -x "$candidate" ]; then FLOCK_BIN="$candidate"; break; fi
done
if [ -z "$FLOCK_BIN" ]; then
    FLOCK_BIN="$(command -v flock || true)"
fi
if [ -z "$FLOCK_BIN" ]; then
    echo "flock is required but was not found" >&2
    exit 77
fi

exec 9>"$LOCK_FILE"
if ! "$FLOCK_BIN" -w 5 9; then
    echo "another refresh is running; giving up" >&2
    exit 75
fi

# 3. Installation-state check.
if [ ! -f "$MANIFEST" ]; then
    echo "free-models-junior is not installed (no manifest); nothing to do" >&2
    exit 0
fi
# The installer marks a finished install with a [complete] section holding "1".
if ! grep -qxF '[complete]' "$MANIFEST" || ! grep -qxE '1' "$MANIFEST"; then
    echo "install is partial; refusing to run" >&2
    exit 76
fi

# python3 is required for the filter, the validator and the probe.
if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required but was not found in PATH" >&2
    exit 77
fi

# 4. Load credentials from $ENV_FILE as data (never printed).
if [ -f "$ENV_FILE" ]; then
    while IFS='=' read -r key value; do
        case "$key" in
            ''|\#*) continue ;;
        esac
        # Strip one pair of surrounding single or double quotes from value.
        case "$value" in
            \'*\') value="${value#\'}"; value="${value%\'}" ;;
            \"*\") value="${value#\"}"; value="${value%\"}" ;;
        esac
        if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            export "$key=$value"
        fi
    done < "$ENV_FILE"
else
    echo "warning: env file $ENV_FILE not found; continuing" >&2
fi

# 5. Filter step (unless --skip-filter).
if [ "$SKIP_FILTER" -eq 0 ]; then
    STAGE="$STATE_DIR/staging-$$"
    mkdir -p "$STAGE"
    trap 'rm -rf "$STAGE"' EXIT

    rc=0
    timeout 300 python3 "$REPO_DIR/free_model_filter.py" --require-agent-ready --output-prefix "$STAGE/free_models" || rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "catalogue refresh failed; keeping previous catalogue" >&2
        exit 78
    fi

    # Validate the staged catalogue: JSON, numeric summary.generated_unix, models list.
    vrc=0
    python3 - "$STAGE/free_models.json" <<'PY' || vrc=$?
import json, sys
path = sys.argv[1]
try:
    with open(path) as f:
        data = json.load(f)
except Exception:
    sys.exit(1)
summary = data.get("summary")
if not isinstance(summary, dict):
    sys.exit(1)
gu = summary.get("generated_unix")
if isinstance(gu, bool) or not isinstance(gu, (int, float)):
    sys.exit(1)
if not isinstance(data.get("models"), list):
    sys.exit(1)
PY
    if [ "$vrc" -ne 0 ]; then
        echo "staged catalogue failed validation; keeping previous catalogue" >&2
        exit 79
    fi

    # Publish atomically: mv each staged free_models* regular file into $STATE_DIR.
    for f in "$STAGE"/free_models*; do
        if [ -f "$f" ]; then
            mv -f "$f" "$STATE_DIR/$(basename "$f")"
        fi
    done

    rm -rf "$STAGE"
    trap - EXIT
fi

# 6. Probe.
if [ ! -f "$PROBE" ]; then
    echo "probe script not found at $PROBE" >&2
    exit 77
fi

prc=0
python3 "$PROBE" --catalogue "$CATALOGUE" --state "$STATE_FILE" --env-file "$ENV_FILE" || prc=$?
if [ "$prc" -ne 0 ]; then
    echo "probe failed (exit $prc)" >&2
    exit "$prc"
fi

# 7. Success.
cat_mtime="$(stat -c %Y "$CATALOGUE")"
state_mtime="$(stat -c %Y "$STATE_FILE")"
echo "refresh ok: catalogue mtime $cat_mtime state $state_mtime"
exit 0
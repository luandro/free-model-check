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
# The script takes an exclusive flock on $STATE_DIR/refresh.lock (900s wait) before
# doing anything else. If the lock cannot be acquired it gives up with exit 75 so
# a concurrent timer tick never double-runs the filter or the probe. The wait is
# long on purpose: a probe sweep can hold the lock for up to 600 s, and the
# refresh is the run that must not be starved, or the catalogue ages past the
# probe's 6-hour gate.
#
# The lock is taken BEFORE credentials are loaded, and the catalogue is only ever
# written inside $STATE_DIR (staging lives there too, so the final `mv` never
# crosses a filesystem and stays atomic per file).
#
set -euo pipefail

STATE_DIR="${STATE_DIR:-$HOME/.software-factory}"
# REPO_DIR is only the units' WorkingDirectory. The filter and probe are
# installed into STATE_DIR, so nothing here depends on the checkout surviving:
# installing from a git worktree must not leave the timers pointing at a path
# that disappears when the worktree is pruned.
REPO_DIR="${REPO_DIR:-$STATE_DIR}"
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
    echo "exec 9>\"$LOCK_FILE\"; flock -w 900 9"
    if [ "$SKIP_FILTER" -eq 0 ]; then
        echo "timeout 300 python3 \"$STATE_DIR/free_model_filter.py\" --require-agent-ready --output-prefix \"$STATE_DIR/staging-\$\$/free_models\""
        echo "# validate staged catalogue, then mv -f each free_models* into \"$STATE_DIR\""
    fi
    echo "python3 \"$PROBE\" --catalogue \"$CATALOGUE\" --state \"$STATE_FILE\" --env-file \"$ENV_FILE\""
    exit 0
fi

# 2. Ensure the state directory exists.
# 0700: the publish path below relies on no other account being able to write
# here, which is what makes its check-then-link window unreachable in practice.
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true

# 3. Acquire the exclusive lock before loading credentials.
# X timeout
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
# The wait must comfortably exceed a probe sweep (up to 600 s). Both timers can
# fire on the same minute, and the refresh is the one that must not be starved:
# skipping it lets the catalogue age past the probe's 6-hour gate.
if ! "$FLOCK_BIN" -w 900 9; then
    echo "another refresh is running; giving up" >&2
    exit 75
fi

# 4. Installation-state check.
if [ ! -f "$MANIFEST" ]; then
    echo "free-models-junior is not installed (no manifest); nothing to do" >&2
    exit 0
fi
# The installer marks a finished install with a [complete] section whose only
# line is "1". Accept nothing looser, so a malformed marker cannot pass.
if ! awk '
    /^\[complete\]$/ { in_complete = 1; next }
    /^\[/ { in_complete = 0 }
    in_complete && NF { count++; value = $0 }
    END { exit !(count == 1 && value == "1") }
' "$MANIFEST"; then
    echo "install is partial; refusing to run" >&2
    exit 76
fi

# python3 is required for the filter, the validator and the probe.
if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required but was not found in PATH" >&2
    exit 77
fi

# 5. Load credentials from $ENV_FILE as data (never printed).
if [ -f "$ENV_FILE" ]; then
    # Grammar matches install.sh and the probe: optional "export ", KEY=VALUE,
    # one pair of surrounding quotes stripped. A line whose key is not a valid
    # shell identifier is ignored rather than exported.
    while IFS='=' read -r key value; do
        case "$key" in
            ''|'#'*) continue ;;
        esac
        key="${key#export }"
        key="${key//[[:space:]]/}"
        value="${value%$'\r'}"
        # Strip one matching pair of surrounding quotes.
        first="${value:0:1}"
        last="${value: -1}"
        if [ "${#value}" -ge 2 ] && [ "$first" = "$last" ] && \
           { [ "$first" = '"' ] || [ "$first" = "'" ]; }; then
            value="${value:1:${#value}-2}"
        fi
        if printf '%s' "$key" | grep -qE '^[A-Za-z_][A-Za-z0-9_]*$'; then
            export "$key=$value"
        fi
    done < "$ENV_FILE"
else
    echo "warning: env file $ENV_FILE not found; continuing" >&2
fi

# 6. Filter step (unless --skip-filter).
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
    # A destination this installer does not own is never overwritten: a refresh
    # must not claim a pre-existing catalogue, or uninstall would later delete it.
    # A skipped file is removed from staging so the next run starts clean.
    for f in "$STAGE"/free_models*; do
        if [ -f "$f" ]; then
            dest="$STATE_DIR/$(basename "$f")"
            # -L as well as -e: a dangling symlink reads as absent to -e but is
            # still a user-owned path that must not be replaced.
            if [ -e "$dest" ] || [ -L "$dest" ]; then
                # Overwrite only a file we own AND whose content is unchanged
                # since we recorded it. A user replacement is never clobbered.
                recorded="$(grep -F "$dest " "$MANIFEST" 2>/dev/null | head -1 | sed 's/.*sha256://')" || true
                current="$(sha256sum "$dest" 2>/dev/null | cut -d' ' -f1)" || true
                if [ -z "$recorded" ] || [ "$recorded" != "$current" ]; then
                    echo "warning: $dest is not ours or was modified; not overwriting" >&2
                    rm -f "$f"
                    continue
                fi
            fi
            # Publish in one atomic step. When the destination is already ours
            # (verified above, plain file, unchanged content) a forced link
            # replaces it; when it is absent a plain link creates it. Either way
            # the kernel decides under the lock, so there is no check-then-write
            # window: if the path changed, ln fails and the staged file is kept.
            staged_hash="$(sha256sum "$f" | cut -d' ' -f1)"
            set +e
            if [ -L "$dest" ]; then
                echo "warning: $dest is a symlink; not overwriting" >&2
                rm -f "$f"
                continue
            fi
            if [ -e "$dest" ]; then
                ln -T -f "$f" "$dest" 2>/dev/null
            else
                ln -T "$f" "$dest" 2>/dev/null
            fi
            publish_rc=$?
            set -e
            if [ "$publish_rc" -ne 0 ]; then
                echo "warning: cannot publish $dest; keeping the previous file" >&2
                rm -f "$f"
                continue
            fi
            rm -f "$f"
            # Record or refresh ownership, so uninstall can verify the hash and a
            # later refresh does not treat the file as unowned. The row goes into
            # [files]; appending at the end of the file would land it in whatever
            # section happens to be last.
            dest_hash="$staged_hash"
            tmp_manifest="$(mktemp "$STATE_DIR/manifest.XXXXXX")"
            if grep -qF "$dest " "$MANIFEST" 2>/dev/null; then
                awk -v old="$dest sha256:" -v new="$dest sha256:$dest_hash" '
                    index($0, old) == 1 { print new; next }
                    { print }
                ' "$MANIFEST" >"$tmp_manifest"
            else
                awk -v row="$dest sha256:$dest_hash" '
                    /^\[/ {
                        if (inside) { print row; inside = 0 }
                        inside = ($0 == "[files]")
                        print
                        next
                    }
                    { print }
                    END { if (inside) print row }
                ' "$MANIFEST" >"$tmp_manifest"
            fi
            mv "$tmp_manifest" "$MANIFEST"
        fi
    done

    rm -rf "$STAGE"
    trap - EXIT
fi

# 7. Probe.
if [ ! -f "$PROBE" ]; then
    echo "probe script not found at $PROBE" >&2
    exit 77
fi
# Clear a temp file left by a run that was killed mid-sweep; it is never
# recorded, so nothing else would remove it.
rm -f "$STATE_DIR"/free_models_junior.json.tmp.* 2>/dev/null || true

prc=0
# Never probe a catalogue this installer does not own: without a manifest row it
# was here before us, may be malformed, and may not even be a free-model report.
cat_recorded="$(grep -F "$CATALOGUE " "$MANIFEST" 2>/dev/null | head -1 | sed 's/.*sha256://')" || true
cat_current="$(sha256sum "$CATALOGUE" 2>/dev/null | cut -d' ' -f1)" || true
if [ -z "$cat_recorded" ] || [ "$cat_recorded" != "$cat_current" ]; then
    # No manifest row, or the file changed since we recorded it: it is not ours
    # to probe, it may be malformed, and it may not be a free-model report.
    echo "warning: $CATALOGUE is not owned or was modified; skipping the probe" >&2
    exit 0
fi
# Probe to a staging path, then publish only if the state file is ours or absent.
# A pre-existing state file is never replaced by a scheduled sweep.
if [ -e "$STATE_FILE" ] || [ -L "$STATE_FILE" ]; then
    # Overwrite only when the content is unchanged since we recorded it.
    recorded="$(grep -F "$STATE_FILE " "$MANIFEST" 2>/dev/null | head -1 | sed 's/.*sha256://')" || true
    current="$(sha256sum "$STATE_FILE" 2>/dev/null | cut -d' ' -f1)" || true
    if [ -z "$recorded" ] || [ "$recorded" != "$current" ]; then
        echo "warning: $STATE_FILE is not ours or was modified; not overwriting" >&2
        exit 0
    fi
fi
PROBE_TMP="$STATE_FILE.tmp.$$"
python3 "$PROBE" --catalogue "$CATALOGUE" --state "$PROBE_TMP" --env-file "$ENV_FILE" \
    --expect-sha256 "$cat_recorded" || prc=$?
if [ "$prc" -ne 0 ]; then
    rm -f "$PROBE_TMP"
    echo "probe failed (exit $prc)" >&2
    exit "$prc"
fi
# Re-check ownership after the sweep: the state file may have been created or
# edited while the probe ran, and it must not be overwritten then.
if [ -e "$STATE_FILE" ] || [ -L "$STATE_FILE" ]; then
    recorded_now="$(grep -F "$STATE_FILE " "$MANIFEST" 2>/dev/null | head -1 | sed 's/.*sha256://')" || true
    current_now="$(sha256sum "$STATE_FILE" 2>/dev/null | cut -d' ' -f1)" || true
    if [ -z "$recorded_now" ] || [ "$recorded_now" != "$current_now" ]; then
        echo "warning: $STATE_FILE changed during the sweep; not overwriting" >&2
        rm -f "$PROBE_TMP"
        exit 0
    fi
fi
state_hash="$(sha256sum "$PROBE_TMP" | cut -d' ' -f1)"
# -f: the destination is normally our own state file, verified above. A symlink
# is refused, and the hash above is taken from the staged file, so a link at the
# destination cannot pass as the bytes we recorded.
if [ -L "$STATE_FILE" ]; then
    echo "warning: $STATE_FILE is a symlink; not overwriting" >&2
    rm -f "$PROBE_TMP"
    exit 0
fi
set +e
if [ -e "$STATE_FILE" ]; then
    ln -T -f "$PROBE_TMP" "$STATE_FILE" 2>/dev/null
else
    ln -T "$PROBE_TMP" "$STATE_FILE" 2>/dev/null
fi
publish_rc=$?
set -e
if [ "$publish_rc" -ne 0 ]; then
    echo "warning: cannot publish $STATE_FILE; keeping the previous state" >&2
    rm -f "$PROBE_TMP"
    exit 0
fi
rm -f "$PROBE_TMP"
# Record or refresh the state file's hash so uninstall can verify it.
tmp_manifest="$(mktemp "$STATE_DIR/manifest.XXXXXX")"
if grep -qF "$STATE_FILE " "$MANIFEST" 2>/dev/null; then
    awk -v old="$STATE_FILE sha256:" -v new="$STATE_FILE sha256:$state_hash" '
        index($0, old) == 1 { print new; next }
        { print }
    ' "$MANIFEST" >"$tmp_manifest"
else
    awk -v row="$STATE_FILE sha256:$state_hash" '
        /^\[/ {
            if (inside) { print row; inside = 0 }
            inside = ($0 == "[files]")
            print
            next
        }
        { print }
        END { if (inside) print row }
    ' "$MANIFEST" >"$tmp_manifest"
fi
mv "$tmp_manifest" "$MANIFEST"

# 8. Success.
cat_mtime="$(stat -c %Y "$CATALOGUE")"
state_mtime="$(stat -c %Y "$STATE_FILE")"
echo "refresh ok: catalogue mtime $cat_mtime generated_unix $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["summary"]["generated_unix"])' "$CATALOGUE" 2>/dev/null || echo unknown) state mtime $state_mtime"
exit 0

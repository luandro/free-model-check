#!/usr/bin/env bash
# install.sh — setup, credentials, scripts, units (steps 1-7)
set -euo pipefail

STATE_DIR="${STATE_DIR:-$HOME/.software-factory}"
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="$STATE_DIR/installed-manifest"

INTERVAL_REFRESH="4h"
INTERVAL_PROBE="30m"
DO_UPDATE=0
DO_UNINSTALL=0
DRY_RUN=0
HARNESS_INTEGRATION=1

die() {
    local code="${2:-1}"
    printf 'ERROR: %s\n' "$1" >&2
    exit "$code"
}

usage() {
    cat <<'EOF' >&2
Usage: install.sh [options]
  --interval-refresh X   default 4h; accept 1h..4h, reject longer
  --interval-probe X     default 30m; accept 15m..1h
  --no-harness-integration
  --update
  --uninstall
  --dry-run
  -h | --help
EOF
}

sha_of() { sha256sum "$1" | cut -d' ' -f1; }

# Replaced by part 3; present now so --uninstall is not silently ignored.
do_uninstall() {
    mkdir -p "$STATE_DIR"

    local flock_bin=""
    for candidate in /usr/bin/flock /bin/flock; do
        if [[ -x "$candidate" ]]; then flock_bin="$candidate"; break; fi
    done
    if [[ -z "$flock_bin" ]]; then
        flock_bin="$(command -v flock || true)"
    fi
    [[ -n "$flock_bin" ]] || die "flock not found" 77

    exec 9>"$STATE_DIR/refresh.lock"
    "$flock_bin" -w 120 9 || die "could not acquire lock after 120s" 75

    if [[ ! -f "$MANIFEST" ]]; then
        printf 'nothing to uninstall\n'
        return 0
    fi

    local -a units=()
    local -a unit_hashes=()
    local -a files=()
    local -a file_hashes=()
    local -a file_types=()
    local agents_md_path=""
    local agents_md_block_sha=""
    local agents_md_begin=""
    local agents_md_end=""
    local in_section=""

    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        if [[ "$line" == \[*] ]]; then
            in_section="${line//[\[\]]/}"
            continue
        fi
        case "$in_section" in
            units)
                local unit="${line%% sha256:*}"
                local hash="${line#*sha256:}"
                [[ "$line" == *"sha256:"* ]] || continue
                units+=("$unit")
                unit_hashes+=("$hash")
                ;;
            files)
                local path="${line%% sha256:*}"
                path="${path%% symlink:*}"
                local hash=""
                local type=""
                if [[ "$line" == *"sha256:"* ]]; then
                    hash="${line#*sha256:}"
                    type="file"
                elif [[ "$line" == *"symlink:"* ]]; then
                    hash="${line#*symlink:}"
                    type="symlink"
                else
                    continue
                fi
                files+=("$path")
                file_hashes+=("$hash")
                file_types+=("$type")
                ;;
            agents_md)
                if [[ "$line" == path=* ]]; then
                    agents_md_path="${line#path=}"
                elif [[ "$line" == block_sha256=* ]]; then
                    agents_md_block_sha="${line#block_sha256=}"
                fi
                ;;
        esac
    done < "$MANIFEST"

    local -a removed_units=()
    local -a skipped_units=()
    local -a removed_files=()
    local -a skipped_files=()
    local agents_md_removed=0
    local agents_md_skipped=0

    local i=0
    while [[ $i -lt ${#units[@]} ]]; do
        local unit="${units[i]}"
        local expected_hash="${unit_hashes[i]}"
        local unit_path="$HOME/.config/systemd/user/$unit"

        if [[ -f "$unit_path" ]]; then
            local current_hash
            current_hash="$(sha_of "$unit_path")"
            if [[ "$current_hash" == "$expected_hash" ]]; then
                systemctl --user disable --now "$unit" >/dev/null 2>&1 || true
                removed_units+=("$unit")
            else
                printf 'WARNING: %s was modified after install; leaving it alone\n' "$unit" >&2
                skipped_units+=("$unit")
            fi
        else
            printf 'WARNING: %s not found; leaving it alone\n' "$unit" >&2
            skipped_units+=("$unit")
        fi
        i=$((i + 1))
    done

    systemctl --user daemon-reload >/dev/null 2>&1 || true

    i=0
    while [[ $i -lt ${#units[@]} ]]; do
        local unit="${units[i]}"
        local expected_hash="${unit_hashes[i]}"
        local unit_path="$HOME/.config/systemd/user/$unit"

        if [[ -f "$unit_path" ]]; then
            local current_hash
            current_hash="$(sha_of "$unit_path")"
            if [[ "$current_hash" == "$expected_hash" ]]; then
                rm -f "$unit_path"
            fi
        fi
        i=$((i + 1))
    done

    i=0
    while [[ $i -lt ${#files[@]} ]]; do
        local path="${files[i]}"
        local expected="${file_hashes[i]}"
        local type="${file_types[i]}"

        if [[ -e "$path" || -L "$path" ]]; then
            local match=0
            if [[ "$type" == "file" ]]; then
                if [[ -f "$path" ]]; then
                    local current_hash
                    current_hash="$(sha_of "$path")"
                    [[ "$current_hash" == "$expected" ]] && match=1
                fi
            elif [[ "$type" == "symlink" ]]; then
                if [[ -L "$path" ]]; then
                    local target
                    target="$(readlink "$path")"
                    [[ "$target" == "$expected" ]] && match=1
                fi
            fi
            if [[ $match -eq 1 ]]; then
                rm -f "$path"
                removed_files+=("$path")
            else
                printf 'WARNING: %s was modified after install; leaving it alone\n' "$path" >&2
                skipped_files+=("$path")
            fi
        else
            skipped_files+=("$path")
        fi
        i=$((i + 1))
    done

    if [[ -n "$agents_md_path" && -n "$agents_md_block_sha" ]]; then
        if [[ -f "$agents_md_path" ]]; then
            local begin_mark='<!-- free-models-junior:begin -->'
            local end_mark='<!-- free-models-junior:end -->'
            local begin_count
            begin_count=$(grep -cF "$begin_mark" "$agents_md_path" 2>/dev/null | head -1) || true
            begin_count="${begin_count:-0}"
            end_count=$(grep -cF "$end_mark" "$agents_md_path" 2>/dev/null | head -1) || true
            end_count="${end_count:-0}"

            if [[ $begin_count -eq 1 && $end_count -eq 1 ]]; then
                local tmp_block
                tmp_block="$(mktemp "$STATE_DIR/block.XXXXXX")"
                awk -v begin="$begin_mark" -v end="$end_mark" '
                    $0 ~ begin { in_block=1; next }
                    $0 ~ end   { in_block=0; next }
                    in_block   { print }
                ' "$agents_md_path" >"$tmp_block"
                local block_hash
                block_hash="$(sha_of "$tmp_block")"
                rm -f "$tmp_block"

                if [[ "$block_hash" == "$agents_md_block_sha" ]]; then
                    local tmp_agents
                    tmp_agents="$(mktemp "$STATE_DIR/AGENTS.md.XXXXXX")"
                    awk -v begin="$begin_mark" -v end="$end_mark" '
                        $0 == begin { skipping=1; next }
                        $0 == end   { skipping=0; next }
                        skipping    { next }
                        { print }
                    ' "$agents_md_path" >"$tmp_agents" && mv "$tmp_agents" "$agents_md_path"
                    agents_md_removed=1
                else
                    printf 'WARNING: AGENTS.md block was modified; leaving it alone\n' >&2
                    agents_md_skipped=1
                fi
            else
                printf 'WARNING: AGENTS.md block was modified; leaving it alone\n' >&2
                agents_md_skipped=1
            fi
        else
            agents_md_skipped=1
        fi
    fi

    # Generated outputs are rewritten by every refresh, so their recorded hashes
    # go stale. They are removed by name only when the manifest claims the path
    # AND the file still looks like our own output, so a user replacement at a
    # previously owned path survives.
    local generated
    for generated in free_models.json free_models.csv free_models_stealthmark.json \
                      free_models_stealthmark.csv free_models_published.json \
                      free_models_published.csv free_models_unbenchmarked.json \
                      free_models_unbenchmarked.csv free_models_diagnostics.json \
                      free_models_unmatched.json free_models_junior.json; do
        local target="$STATE_DIR/$generated"
        [[ -f "$target" ]] || continue
        # No manifest row means we never created it: leave it alone.
        local recorded=""
        recorded="$(grep -F "$target " "$MANIFEST" 2>/dev/null | head -1 | sed 's/.*sha256://')" || true
        [[ -n "$recorded" ]] || continue
        local current
        current="$(sha256sum "$target" | cut -d' ' -f1)"
        if [[ "$current" != "$recorded" ]]; then
            # The content changed since we recorded it: the user replaced it.
            printf 'WARNING: %s was modified after install; leaving it alone\n' "$target" >&2
            skipped_files+=("$target")
            continue
        fi
        rm -f "$target"
        removed_files+=("$target")
    done

    rm -f "$MANIFEST"
    # Remove the bootstrap staging dir only when it holds nothing but this
    # installer's own free_models* files. Anything else — a subdirectory, a
    # backup, an unreadable entry — means it is not ours to delete.
    local stage="$STATE_DIR/staging-bootstrap"
    if [[ -d "$stage" ]]; then
        # Only plain regular files named exactly free_models.json/.csv or
        # free_models_<lane>.json/.csv are ours. A free_models_backup file,
        # a directory, or a symlink means the dir is not ours to delete.
        local unexpected
        unexpected="$(find "$stage" -mindepth 1 -maxdepth 1 \
            -regextype posix-extended \
            \( ! -type f -o ! -regex '.*/free_models(_[a-z]+)?\.(json|csv)' \) \
            -print 2>/dev/null)"
        if [[ -n "$unexpected" ]]; then
            printf 'WARNING: %s has unexpected content; leaving it alone\n' "$stage" >&2
        else
            rm -rf "$stage"
        fi
    fi

    printf 'Removed units:\n'
    for u in "${removed_units[@]}"; do printf '  %s\n' "$u"; done
    printf 'Skipped units:\n'
    for u in "${skipped_units[@]}"; do printf '  %s\n' "$u"; done
    printf 'Removed files:\n'
    for f in "${removed_files[@]}"; do printf '  %s\n' "$f"; done
    printf 'Skipped files:\n'
    for f in "${skipped_files[@]}"; do printf '  %s\n' "$f"; done
    if [[ $agents_md_removed -eq 1 ]]; then
        printf 'Removed AGENTS.md block\n'
    elif [[ $agents_md_skipped -eq 1 ]]; then
        printf 'Skipped AGENTS.md block\n'
    fi
}

# Rewrite the manifest with one line added to its named section. Appending to the
# end of the file would drop the row into whatever section happens to be last, so
# the file is rebuilt section by section instead.
manifest_add() {
    local section="${1//[\[\]]/}" line="$2" tmp
    tmp="$(mktemp "$STATE_DIR/manifest.XXXXXX")"
    if [[ ! -f "$MANIFEST" ]]; then
        printf '[meta]\nversion=1\nstate_dir=%s\nrepo=%s\n' "$STATE_DIR" "$REPO_ROOT" >"$tmp"
    else
        cat "$MANIFEST" >"$tmp"
    fi
    if grep -qxF "[$section]" "$tmp" 2>/dev/null; then
        # Insert the row at the end of its own section: after the header until
        # the next header (or EOF).
        awk -v want="[$section]" -v row="$line" '
            /^\[/ {
                if (inside) { print row; inside = 0 }
                inside = ($0 == want)
                print
                next
            }
            { print }
            END { if (inside) print row }
        ' "$tmp" >"$tmp.new" && mv "$tmp.new" "$tmp"
    else
        printf '\n[%s]\n%s\n' "$section" "$line" >>"$tmp"
    fi
    mv "$tmp" "$MANIFEST"
}

manifest_rewrite() {
    local tmp
    tmp="$(mktemp "$STATE_DIR/manifest.XXXXXX")"
    printf '%s' "$1" >"$tmp"
    mv "$tmp" "$MANIFEST"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --interval-refresh) INTERVAL_REFRESH="${2:-}"; shift 2 ;;
        --interval-probe) INTERVAL_PROBE="${2:-}"; shift 2 ;;
        --no-harness-integration) HARNESS_INTEGRATION=0; shift ;;
        --update) DO_UPDATE=1; shift ;;
        --uninstall) DO_UNINSTALL=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage; die "unknown flag: $1" 2 ;;
    esac
done

parse_interval() {
    local interval="$1" min="$2" max="$3" unit="$4" name="$5" value expr
    if [[ "$interval" =~ ^([0-9]+)m$ ]]; then
        value="${BASH_REMATCH[1]}"
        (( value >= min && value <= max )) || die "$name interval ${value}m outside range ${min}m..${max}m"
        (( 60 % value == 0 )) || die "$name interval ${value}m must divide 60"
        expr="*:0/${value}"
    elif [[ "$interval" =~ ^([0-9]+)h$ ]]; then
        value="${BASH_REMATCH[1]}"
        (( value >= min && value <= max )) || die "$name interval ${value}h outside range ${min}h..${max}h"
        (( 24 % value == 0 )) || die "$name interval ${value}h must divide 24"
        expr="*-*-* 00/${value}:00:00"
    else
        die "$name interval must be like 30m or 1h, got: $interval"
    fi
    systemd-analyze calendar "$expr" >/dev/null || die "$name interval validation failed: $expr"
    printf '%s\n' "$expr"
}

# Minutes and hours are different units: 30m is valid while 30h is not, so each
# bound is expressed in the unit the flag actually uses.
REFRESH_EXPR="$(parse_interval "$INTERVAL_REFRESH" 1 4 h refresh)"
PROBE_EXPR="$(parse_interval "$INTERVAL_PROBE" 15 60 m probe)"

if [[ $(id -u) -eq 0 ]]; then die "refusing to run as root"; fi

if [[ $DO_UNINSTALL -eq 1 ]]; then
    # Dry-run is checked first: a combined --uninstall --dry-run must not touch
    # anything, and uninstall would otherwise run for real.
    if [[ $DRY_RUN -eq 1 ]]; then
        printf 'DRY RUN: would uninstall from %s\n' "$STATE_DIR"
        exit 0
    fi
    do_uninstall
    exit $?
fi

if [[ $DRY_RUN -eq 1 ]]; then
    cat <<EOF
DRY RUN — no changes will be made
  state dir:      $STATE_DIR
  repo:           $REPO_ROOT
  refresh:        $INTERVAL_REFRESH ($REFRESH_EXPR)
  probe:          $INTERVAL_PROBE ($PROBE_EXPR)
  opencode bin:   $(command -v opencode 2>/dev/null || echo '<not found>')
  harness AGENTS: $HOME/Dev/agent-harnesses/instructions/AGENTS.md
  harness integration: $([[ $HARNESS_INTEGRATION -eq 1 ]] && echo yes || echo no)
EOF
    exit 0
fi

# Step 1: Prerequisites
python3 --version | grep -qE 'Python 3\.(1[0-9]|[2-9][0-9])' || die "python3 >= 3.10 required"
OPENCODE_BIN="$(command -v opencode)" || die "opencode not found in PATH"
OPENCODE_BIN="$(readlink -f "$OPENCODE_BIN")"
command -v systemctl >/dev/null || die "systemctl not found"
command -v systemd-analyze >/dev/null || die "systemd-analyze not found"
[[ -f "$REPO_ROOT/.env" ]] || die "$REPO_ROOT/.env not found"

AA_API_KEY=""
OPENROUTER_API_KEY=""
while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
    # Strip surrounding quotes and whitespace: the value is data, never sourced.
    key="${key//[[:space:]]/}"
    value="${value%$'\r'}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    case "$value" in
        \'*\') value="${value#\'}"; value="${value%\'}" ;;
        \"*\') value="${value#\"}"; value="${value%\"}" ;;
    esac
    case "$key" in
        AA_API_KEY) AA_API_KEY="$value" ;;
        OPENROUTER_API_KEY) OPENROUTER_API_KEY="$value" ;;
    esac
done < "$REPO_ROOT/.env"
[[ -n "$AA_API_KEY" ]] || die "AA_API_KEY missing from .env"

# Step 2
mkdir -p "$STATE_DIR"

# Step 3: Lock
exec 9>"$STATE_DIR/refresh.lock"
# Prefer the system flock: some homebrew builds return 0 without taking the lock.
FLOCK_BIN=""
for candidate in /usr/bin/flock /bin/flock; do
    if [[ -x "$candidate" ]]; then FLOCK_BIN="$candidate"; break; fi
done
if [[ -z "$FLOCK_BIN" ]]; then
    FLOCK_BIN="$(command -v flock || true)"
fi
[[ -n "$FLOCK_BIN" ]] || die "flock not found" 77
"$FLOCK_BIN" -w 120 9 || die "could not acquire lock after 120s" 75

# Step 4: Preflight
preflight_paths=(
    "$STATE_DIR/free-models.env"
    "$STATE_DIR/free-models-probe.py"
    "$STATE_DIR/free-models-refresh.sh"
    "$HOME/.config/systemd/user/free-models-refresh.service"
    "$HOME/.config/systemd/user/free-models-refresh.timer"
    "$HOME/.config/systemd/user/free-models-probe.service"
    "$HOME/.config/systemd/user/free-models-probe.timer"
)

declare -A owned=()
if [[ -f "$MANIFEST" ]]; then
    # [files] rows hold absolute paths; [units] rows hold bare unit names, so
    # each is normalised to the path preflight compares against.
    while IFS= read -r line; do
        [[ "$line" == /* ]] || continue
        path="${line%% sha256:*}"
        path="${path%% symlink:*}"
        owned["$path"]=1
    done < <(sed -n '/^\[files\]/,/^\[/p' "$MANIFEST" | sed '1d;$d')
    while IFS= read -r line; do
        [[ -n "$line" ]] || continue
        unit_name="${line%% *}"
        [[ "$unit_name" == *.service || "$unit_name" == *.timer ]] || continue
        owned["$HOME/.config/systemd/user/$unit_name"]=1
    done < <(sed -n '/^\[units\]/,/^\[/p' "$MANIFEST" | sed '1d;$d')
fi

# The bootstrap writes into STATE_DIR, so its destinations must be checked in
# preflight too: a non-empty or unreadable staging dir has to abort before any
# script or unit is published.
if [[ -e "$STATE_DIR/staging-bootstrap" && ! -d "$STATE_DIR/staging-bootstrap" ]]; then
    die "$STATE_DIR/staging-bootstrap exists and is not a directory" 1
fi
if [[ -d "$STATE_DIR/staging-bootstrap" ]]; then
    if ! stage_entries="$(ls -A "$STATE_DIR/staging-bootstrap" 2>/dev/null)"; then
        die "$STATE_DIR/staging-bootstrap is not readable" 1
    fi
    if [[ -n "$stage_entries" ]]; then
        die "$STATE_DIR/staging-bootstrap is not empty; refusing to use it" 1
    fi
fi

for path in "${preflight_paths[@]}"; do
    if [[ -e "$path" || -L "$path" ]]; then
        [[ -n "${owned[$path]:-}" ]] || die "refusing to overwrite $path (not owned by this installer)"
        [[ $DO_UPDATE -eq 1 ]] || die "refusing to overwrite $path (use --update)"
    fi
done

AGENTS_MD_PATH="$HOME/Dev/agent-harnesses/instructions/AGENTS.md"
if [[ -L "$HOME/.claude/CLAUDE.md" ]]; then
    resolved="$(readlink -f "$HOME/.claude/CLAUDE.md" || true)"
    [[ -n "$resolved" ]] && AGENTS_MD_PATH="$resolved"
fi
[[ -e "$AGENTS_MD_PATH" ]] || die "AGENTS.md not found at $AGENTS_MD_PATH"

# Step 5: Credentials
umask 077
CRED_FILE="$STATE_DIR/free-models.env"
{
    printf 'AA_API_KEY=%s\n' "$AA_API_KEY"
    if [[ -n "$OPENROUTER_API_KEY" ]]; then printf 'OPENROUTER_API_KEY=%s\n' "$OPENROUTER_API_KEY"; else
        printf 'WARNING: OPENROUTER_API_KEY absent from .env\n' >&2; fi
    printf 'OPENCODE_BIN=%s\n' "$OPENCODE_BIN"
} >"$CRED_FILE"
manifest_add "[files]" "$CRED_FILE sha256:$(sha_of "$CRED_FILE")"

# Step 6: Install scripts
install -m 0755 "$REPO_ROOT/scripts/free-models-probe.py" "$STATE_DIR/free-models-probe.py"
manifest_add "[files]" "$STATE_DIR/free-models-probe.py sha256:$(sha_of "$STATE_DIR/free-models-probe.py")"
install -m 0755 "$REPO_ROOT/scripts/free-models-refresh.sh" "$STATE_DIR/free-models-refresh.sh"
manifest_add "[files]" "$STATE_DIR/free-models-refresh.sh sha256:$(sha_of "$STATE_DIR/free-models-refresh.sh")"

# Step 7: Write four units
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

cat >"$UNIT_DIR/free-models-refresh.service" <<EOF
[Unit]
Description=Refresh free model catalogue and rebuild the junior state file
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=STATE_DIR=$STATE_DIR
Environment=REPO_DIR=$REPO_ROOT
ExecStart=$STATE_DIR/free-models-refresh.sh
WorkingDirectory=$REPO_ROOT
EOF
manifest_add "[units]" "free-models-refresh.service sha256:$(sha_of "$UNIT_DIR/free-models-refresh.service")"

cat >"$UNIT_DIR/free-models-refresh.timer" <<EOF
[Unit]
Description=Refresh free model catalogue ($INTERVAL_REFRESH)

[Timer]
OnCalendar=$REFRESH_EXPR
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF
manifest_add "[units]" "free-models-refresh.timer sha256:$(sha_of "$UNIT_DIR/free-models-refresh.timer")"

cat >"$UNIT_DIR/free-models-probe.service" <<EOF
[Unit]
Description=Probe free model availability
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=STATE_DIR=$STATE_DIR
Environment=REPO_DIR=$REPO_ROOT
ExecStart=$STATE_DIR/free-models-refresh.sh --skip-filter
WorkingDirectory=$REPO_ROOT
EOF
manifest_add "[units]" "free-models-probe.service sha256:$(sha_of "$UNIT_DIR/free-models-probe.service")"

cat >"$UNIT_DIR/free-models-probe.timer" <<EOF
[Unit]
Description=Probe free model availability ($INTERVAL_PROBE)

[Timer]
OnCalendar=$PROBE_EXPR
Persistent=true
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
EOF
manifest_add "[units]" "free-models-probe.timer sha256:$(sha_of "$UNIT_DIR/free-models-probe.timer")"

if [[ $DRY_RUN -eq 1 ]]; then printf 'DRY RUN: steps 8-12 would run\n'; exit 0; fi
# --update: clear [complete] before step 8 if the manifest exists and has it
if [[ $DO_UPDATE -eq 1 ]]; then
    if [[ -f "$MANIFEST" ]] && grep -qxF '[complete]' "$MANIFEST"; then
        # Rewrite the manifest without the [complete] section.
        tmp_manifest="$(mktemp "$STATE_DIR/manifest.XXXXXX")"
        awk '
            BEGIN { skip=0 }
            /^\[complete\]/ { skip=1; next }
            /^\[/ && skip { skip=0 }
            !skip { print }
        ' "$MANIFEST" >"$tmp_manifest" && mv "$tmp_manifest" "$MANIFEST"
    fi
fi

# Step 8: Skill symlink
mkdir -p "$HOME/.agents/skills"
SKILL_LINK="$HOME/.agents/skills/free-models-junior"
SKILL_TARGET="$REPO_ROOT/scripts/skills/free-models-junior"
if [[ -L "$SKILL_LINK" ]]; then
    current_target="$(readlink -f "$SKILL_LINK")"
    if [[ "$current_target" != "$SKILL_TARGET" ]]; then
        die "skill link $SKILL_LINK exists but points to $current_target (expected $SKILL_TARGET)"
    fi
elif [[ -e "$SKILL_LINK" ]]; then
    # Check if owned in manifest
    owned_skill=0
    if [[ -f "$MANIFEST" ]]; then
        while IFS= read -r line; do
            [[ "$line" == /* ]] || continue
            path="${line%% sha256:*}"
            path="${path%% symlink:*}"
            [[ "$path" == "$SKILL_LINK" ]] && owned_skill=1
        done < <(sed -n '/^\[files\]/,/^\[/p' "$MANIFEST" | sed '1d;$d')
    fi
    [[ $owned_skill -eq 1 ]] || die "skill link target $SKILL_LINK exists and is not owned by manifest"
    # Already owned: leave it, and do not re-record it as a fresh claim.
else
    ln -s "$SKILL_TARGET" "$SKILL_LINK"
    manifest_add "[files]" "$SKILL_LINK symlink:$SKILL_TARGET"
fi

# Step 9: AGENTS.md block
if [[ $HARNESS_INTEGRATION -eq 1 ]]; then
    BEGIN_MARK='<!-- free-models-junior:begin -->'
    END_MARK='<!-- free-models-junior:end -->'
    AGENTS_BODY="For free coding models, read \`${STATE_DIR}/free_models_junior.json\` and use the \`free-models-junior\` skill to delegate junior tasks."
    # Count marker occurrences. grep -c prints 0 and exits 1 on no match, which
    # under `set -e` would abort the install; the `|| true` keeps it benign.
    begin_count=$(grep -cF "$BEGIN_MARK" "$AGENTS_MD_PATH" 2>/dev/null | head -1) || true
    end_count=$(grep -cF "$END_MARK" "$AGENTS_MD_PATH" 2>/dev/null | head -1) || true
    begin_count="${begin_count:-0}"
    end_count="${end_count:-0}"
    # True only when this installer created the block. A pre-existing block is
    # left alone and never recorded, so uninstall cannot delete user content.
    agents_block_owned=0
    if [[ $begin_count -eq 1 && $end_count -eq 1 ]]; then
        if grep -qxF "path=$AGENTS_MD_PATH" "$MANIFEST" 2>/dev/null; then
            # We recorded this block at install time. Replace it only while the
            # enclosed text still matches that hash; otherwise the user edited it
            # and it is no longer ours to rewrite.
            tmp_now="$(mktemp "$STATE_DIR/block.XXXXXX")"
            awk -v begin="$BEGIN_MARK" -v end="$END_MARK" '
                $0 ~ begin { inside=1; next }
                $0 ~ end   { inside=0; next }
                inside     { print }
            ' "$AGENTS_MD_PATH" >"$tmp_now"
            now_sha="$(sha_of "$tmp_now")"
            rm -f "$tmp_now"
            recorded_sha="$(sed -n 's/^block_sha256=//p' "$MANIFEST" | tail -1)"
            if [[ -z "$recorded_sha" ]]; then
                # No recorded hash means we cannot prove the block is still ours.
                printf 'WARNING: AGENTS.md block ownership is unknown; leaving it alone\n' >&2
            elif [[ "$now_sha" != "$recorded_sha" ]]; then
                printf 'WARNING: AGENTS.md block was edited; leaving it alone\n' >&2
            else
                # Replace the enclosed lines, keeping the markers. The body is
                # byte-identical to the append path so the recorded hash holds.
                tmp_agents="$(mktemp "$STATE_DIR/AGENTS.md.XXXXXX")"
                awk -v begin="$BEGIN_MARK" -v end="$END_MARK" -v block="$AGENTS_BODY" '
                    $0 == begin { print; print block; skipping=1; next }
                    $0 == end   { skipping=0; print; next }
                    skipping    { next }
                    { print }
                ' "$AGENTS_MD_PATH" >"$tmp_agents" && mv "$tmp_agents" "$AGENTS_MD_PATH"
                agents_block_owned=1
            fi
        else
            printf 'WARNING: AGENTS.md already has a free-models-junior block; leaving it alone\n' >&2
        fi
    else
        # Backup before append
        backup_dir="$(dirname "$AGENTS_MD_PATH")/backups"
        mkdir -p "$backup_dir" || printf 'WARNING: could not create backup dir\n' >&2
        backup_file="$backup_dir/AGENTS.md.$(date +%Y%m%dT%H%M%S).bak"
        cp "$AGENTS_MD_PATH" "$backup_file" 2>/dev/null || printf 'WARNING: backup failed\n' >&2
        # Append block
        {
            printf '\n'
            printf '%s\n' "$BEGIN_MARK"
            printf '%s\n' "$AGENTS_BODY"
            printf '%s\n' "$END_MARK"
        } >>"$AGENTS_MD_PATH"
        agents_block_owned=1
    fi
    # Record hash of enclosed text
    # Extract lines between markers to temp file
    tmp_block="$(mktemp "$STATE_DIR/block.XXXXXX")"
    awk -v begin="$BEGIN_MARK" -v end="$END_MARK" '
        $0 ~ begin { in_block=1; next }
        $0 ~ end { in_block=0; next }
        in_block { print }
    ' "$AGENTS_MD_PATH" >"$tmp_block"
    block_sha="$(sha_of "$tmp_block")"
    rm -f "$tmp_block"
    if [[ $agents_block_owned -eq 1 ]]; then
        # Record path= and block_sha256= under [agents_md], replacing any previous
        # values so a re-run does not append duplicates.
        tmp_manifest="$(mktemp "$STATE_DIR/manifest.XXXXXX")"
        {
            cat "$MANIFEST"
            grep -qxF '[agents_md]' "$MANIFEST" 2>/dev/null || printf '\n[agents_md]\n'
            printf 'path=%s\n' "$AGENTS_MD_PATH"
            printf 'block_sha256=%s\n' "$block_sha"
        } >"$tmp_manifest"
        # Drop earlier path=/block_sha256= lines inside the section, keeping the last.
        awk '
            /^path=/ { path=$0; next }
            /^block_sha256=/ { sha=$0; next }
            { print }
            END {
                if (path != "") print path
                if (sha != "") print sha
            }
        ' "$tmp_manifest" >"$tmp_manifest.new" && mv "$tmp_manifest.new" "$MANIFEST"
        rm -f "$tmp_manifest"
    fi
fi

# Step 10: Bootstrap
# The filter reads AA_API_KEY from its environment; the values parsed from .env
# are shell variables, so they must be exported before the non-interactive run.
export AA_API_KEY
if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then export OPENROUTER_API_KEY; fi

# Stage inside the state dir under a name this installer owns. Preflight already
# rejected a non-empty staging dir, so this is a plain mkdir.
STAGE="$STATE_DIR/staging-bootstrap"
mkdir -p "$STAGE"
# Publish only when the destination does not already exist, so a pre-existing
# catalogue is never overwritten and later claimed by uninstall.
# Bootstrap skips the probe when the catalogue was not published by THIS run. A
# manifest entry from an earlier run is not evidence, so track publication here.
CATALOGUE_PUBLISHED=0
publish_catalogue() {
    local src
    for src in "$STAGE"/free_models*; do
        [[ -f "$src" ]] || continue
        local dest="$STATE_DIR/$(basename "$src")"
        if [[ -e "$dest" ]]; then
            printf 'WARNING: %s already exists; not overwriting\n' "$dest" >&2
            # Drop the skipped file so a later --update does not see a non-empty
            # staging dir and refuse to proceed.
            rm -f "$src"
            continue
        fi
        mv "$src" "$dest"
        manifest_add "[files]" "$dest sha256:$(sha_of "$dest")"
        # Only the catalogue itself counts: publishing a sibling CSV must not
        # make bootstrap probe a pre-existing free_models.json.
        if [[ "$(basename "$src")" == "free_models.json" ]]; then
            CATALOGUE_PUBLISHED=1
        fi
    done
}
timeout 300 python3 "$REPO_ROOT/free_model_filter.py" --require-agent-ready \
    --output-prefix "$STAGE/free_models" || die "catalogue bootstrap failed" 1
# Validate the staged catalogue before publishing anything.
python3 - "$STAGE/free_models.json" <<'PY' || die "bootstrap JSON validation failed" 1
import json, sys
with open(sys.argv[1]) as handle:
    data = json.load(handle)
summary = data.get("summary")
if not isinstance(summary, dict):
    raise SystemExit("summary missing")
generated = summary.get("generated_unix")
if isinstance(generated, bool) or not isinstance(generated, (int, float)):
    raise SystemExit("generated_unix missing or not a number")
if not isinstance(data.get("models"), list):
    raise SystemExit("models missing or not a list")
PY
# Publish regular files only, recording each one.
publish_catalogue
# The probe writes the state file directly. Claim it only when this bootstrap
# created it, so a pre-existing state file is never adopted for deletion.
state_existed=0
if [[ -e "$STATE_DIR/free_models_junior.json" ]]; then state_existed=1; fi
if [[ ! -f "$STATE_DIR/free_models.json" ]]; then
    # No usable catalogue (for example every publish was skipped): leave the
    # existing state alone rather than failing the whole install.
    printf 'WARNING: no catalogue published; skipping the bootstrap probe\n' >&2
    rm -rf "$STAGE"
    stage_ready=0
else
    stage_ready=1
fi
if [[ $stage_ready -eq 1 ]]; then
    # Probe only against a catalogue this run published; a pre-existing one may
    # be stale or foreign, and probing it could fail or cost a request.
    if [[ $CATALOGUE_PUBLISHED -eq 0 ]]; then
        printf 'WARNING: catalogue was not published by this run; skipping the bootstrap probe\n' >&2
        rm -rf "$STAGE"
        stage_ready=0
    fi
fi
if [[ $stage_ready -eq 1 ]]; then
    # Probe to a staging path first: a pre-existing state file must not be
    # replaced by a writer, and a failed sweep must not destroy it.
    PROBE_STATE="$STAGE/free_models_junior.json"
    probe_rc=0
    timeout 700 python3 "$STATE_DIR/free-models-probe.py" \
        --catalogue "$STATE_DIR/free_models.json" \
        --state "$PROBE_STATE" \
        --env-file "$STATE_DIR/free-models.env" || probe_rc=$?
    if [[ $probe_rc -ne 0 ]]; then
        # A stale or pre-existing catalogue is not fatal: keep what is there and
        # let the scheduled refresh sort it out.
        printf 'WARNING: bootstrap probe failed (exit %d); keeping existing state\n' "$probe_rc" >&2
        rm -f "$PROBE_STATE"
    elif [[ $state_existed -eq 0 && -f "$PROBE_STATE" ]]; then
        mv "$PROBE_STATE" "$STATE_DIR/free_models_junior.json"
        manifest_add "[files]" "$STATE_DIR/free_models_junior.json sha256:$(sha_of "$STATE_DIR/free_models_junior.json")"
    elif [[ $state_existed -eq 1 ]]; then
        printf 'WARNING: %s already exists; not overwriting\n' "$STATE_DIR/free_models_junior.json" >&2
        rm -f "$PROBE_STATE"
    fi
    rm -rf "$STAGE"
fi

# Step 11: Timers
systemctl --user daemon-reload || printf 'WARNING: systemctl --user daemon-reload failed\n' >&2
if ! systemctl --user enable --now free-models-refresh.timer free-models-probe.timer; then
    # A sandbox or a host without a reachable user bus lands here. The units are
    # written and the manifest is accurate, so this is a warning, not a failure.
    printf 'WARNING: could not enable timers; run "systemctl --user enable --now free-models-refresh.timer free-models-probe.timer" later\n' >&2
fi

# Step 12: Complete + summary
manifest_add "[complete]" "1"
printf 'State dir: %s\n' "$STATE_DIR"
printf 'Repo: %s\n' "$REPO_ROOT"
printf 'Refresh interval: %s\n' "$INTERVAL_REFRESH"
printf 'Probe interval: %s\n' "$INTERVAL_PROBE"
systemctl --user list-timers 'free-models-*'
printf 'Uninstall with: ./install.sh --uninstall\n'
# Task 3 — write `install.sh`

Write ONE new file: `install.sh` at the repo root. Do not modify any other file.

Bash 5, `set -euo pipefail`, no dependencies beyond coreutils, `flock`,
`systemctl`, `systemd-analyze`, `python3`, `sha256sum`.

## Purpose

Install/uninstall an automated "free models junior" pipeline:

- copies `scripts/free-models-probe.py` and `scripts/free-models-refresh.sh`
  into `$STATE_DIR`
- writes four systemd user units
- symlinks the skill into `~/.agents/skills/free-models-junior`
- optionally appends a managed block to the canonical shared AGENTS.md
- bootstraps the catalogue and state file
- records ownership in a manifest so uninstall is precise

`STATE_DIR="${STATE_DIR:-$HOME/.software-factory}"` (always overridable via env).

`REPO_ROOT` = the directory containing this `install.sh`
(`cd "$(dirname "$0")" && pwd`).

## Flags

```
--interval-refresh X   catalogue refresh interval, default 4h (accept 1h..4h;
                       reject anything longer than 4h with an explanation that
                       the 6h catalogue age gate needs 2h of head-room)
--interval-probe X     probe interval, default 30m (accept 15m..1h)
--no-harness-integration   skip the AGENTS.md managed block
--update               update an existing install in place
--uninstall            remove everything recorded in the manifest
--dry-run              print planned actions, change nothing
-h | --help            usage
```

Interval parsing: accept `N m`/`Nm`/`N h`/`Nh` forms (`30m`, `1h`, `2h`).
Convert to an OnCalendar expression:

- minutes `M` → `*:0/M` (M must divide 60 evenly, else reject)
- hours `H` → `*-*-* 00/H:00:00` (H must divide 24 evenly, else reject)

Validate every expression with `systemd-analyze calendar "<expr>"` and abort if
it fails.

## Manifest layout

`$STATE_DIR/installed-manifest` — plain text, line oriented, atomic rewrites
(temp file in the same dir + `mv`). Sections are delimited by header lines:

```
[meta]
version=1
state_dir=/home/u/.software-factory
repo=/home/u/Dev/AI/free-model-check

[files]
<path> sha256:<hex> [old_sha256:<hex>]
...

[units]
free-models-refresh.service sha256:<hex>
free-models-refresh.timer sha256:<hex>
free-models-probe.service sha256:<hex>
free-models-probe.timer sha256:<hex>

[agents_md]
path=/home/u/Dev/agent-harnesses/instructions/AGENTS.md
block_sha256:<hex>
begin_marker=<!-- free-models-junior:begin -->
end_marker=<!-- free-models-junior:end -->

[complete]
1
```

Rules:

- `[files]` records every artifact this installer created (installed scripts,
  credentials file, skill symlink target, and any output file it created during
  bootstrap). For symlinks record `symlink:<target>` instead of a sha.
- The optional `old_sha256:` is recorded during `--update` so uninstall can
  accept either the previous or the new content.
- `[complete]` contains `1` only after a fully successful install/update.
- Never list the AGENTS.md file itself under `[files]`.

## Two-phase publishing helper

For every artifact, in this order:

1. **intent** — append the path (plus the expected hash where known) to the
   manifest and rewrite it atomically
2. **publish** — create/replace the artifact (write temp file in the target dir,
   `chmod`, `mv`)
3. **verify** — confirm the artifact exists and its hash matches

On any failure exit non-zero leaving the manifest accurate for `--uninstall`.

## Install sequence

1. Prerequisites: `python3 --version` >= 3.10; `command -v opencode` (store
   absolute path); `command -v systemctl`; `command -v systemd-analyze`. The
   `.env` at `"$REPO_ROOT/.env"` must exist and contain `AA_API_KEY`. On any
   failure print a specific message and exit 1.
2. `mkdir -p "$STATE_DIR"`.
3. Take the lock: `exec 9>"$STATE_DIR/refresh.lock"; flock -w 120 9`.
4. **Preflight** — for every non-AGENTS.md destination that will be created
   (installed scripts, credentials file, units, skill symlink, expected output
   files) : if it exists and is not recorded in the manifest, abort with
   `"refusing to overwrite <path> (not owned by this installer)"` unless
   `--update` was given and the path IS owned. AGENTS.md is special: it is
   expected to exist; only validate that the resolved canonical path exists.
5. Extract credentials. Parse `"$REPO_ROOT/.env"` as data (never `source`):
   key/value lines, strip surrounding quotes, keep only
   `AA_API_KEY`, `OPENROUTER_API_KEY`. Write `$STATE_DIR/free-models.env`
   containing those keys plus `OPENCODE_BIN=<abs path>`; `chmod 600` from the
   first write (create with `umask 077`). Warn (do not fail) when
   `OPENROUTER_API_KEY` is absent.
6. Install scripts: `install -m 0755 scripts/free-models-probe.py
   "$STATE_DIR/free-models-probe.py"` and the same for the refresh wrapper.
7. Write the four units to `$HOME/.config/systemd/user/` (create the dir when
   absent), substituting absolute paths:

`free-models-refresh.service`
```ini
[Unit]
Description=Refresh free model catalogue and rebuild the junior state file
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$STATE_DIR/free-models-refresh.sh
WorkingDirectory=$REPO_ROOT
```

`free-models-refresh.timer`
```ini
[Unit]
Description=Refresh free model catalogue (<refresh interval>)

[Timer]
OnCalendar=<refresh expression>
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
```

`free-models-probe.service`
```ini
[Unit]
Description=Probe free model availability
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$STATE_DIR/free-models-refresh.sh --skip-filter
WorkingDirectory=$REPO_ROOT
```

`free-models-probe.timer`
```ini
[Unit]
Description=Probe free model availability (<probe interval>)

[Timer]
OnCalendar=<probe expression>
Persistent=true
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
```

8. Skill: create `$HOME/.agents/skills` when absent; symlink
   `$HOME/.agents/skills/free-models-junior` → `$REPO_ROOT/scripts/skills/free-models-junior`.
   If it exists and already points there, leave it. If it exists pointing
   elsewhere and is not owned, abort.
9. AGENTS.md managed block, unless `--no-harness-integration`:
   - resolve the canonical path by `readlink -f "$HOME/.claude/CLAUDE.md"`; if
     that is not a symlink or does not resolve, fall back to
     `$HOME/Dev/agent-harnesses/instructions/AGENTS.md`
   - if the file does not exist, create it with a single `# Shared harness
     instructions` heading
   - if exactly one begin marker AND one end marker already exist: if the
     manifest records this block, replace its contents; otherwise warn
     `"AGENTS.md already contains a free-models-junior block; leaving it alone"`
     and skip
   - else append, after a blank line, this block (exact text):

```
<!-- free-models-junior:begin -->
For free coding models, read `~/.software-factory/free_models_junior.json`
and use the `free-models-junior` skill to delegate junior tasks.
<!-- free-models-junior:end -->
```

   - back up the file first to
     `$(dirname "$canonical")/backups/AGENTS.md.$(date +%Y%m%dT%H%M%S).bak`
     (create `backups/` when absent) — but only when that directory's parent is
     writable; otherwise skip the backup with a warning
   - record the block's sha256 in `[agents_md]`
10. Bootstrap, still under the lock, without calling the wrapper (to avoid
    self-deadlock): run
    `timeout 300 python3 "$REPO_ROOT/free_model_filter.py" --require-agent-ready --output-prefix "$STATE_DIR/staging-bootstrap/free_models"`
    then validate the JSON the same way the wrapper does, then `mv` the
    `free_models*` files into `$STATE_DIR`, then
    `timeout 700 python3 "$STATE_DIR/free-models-probe.py"`.
    Record every output file created in `[files]`.
    On failure: print the failure, do NOT write `[complete]`, exit 1.
11. `systemctl --user daemon-reload` then
    `systemctl --user enable --now free-models-refresh.timer free-models-probe.timer`.
12. Write `[complete] 1` and rewrite the manifest atomically.
13. Print a summary: paths, intervals, next timer runs
    (`systemctl --user list-timers 'free-models-*'`), and how to uninstall.

## --update sequence

1. Require an existing manifest and `[complete]`.
2. Take the lock. Clear `[complete]` (rewrite manifest) BEFORE touching
   artifacts.
3. For each owned artifact being replaced, record `old_sha256` next to the new
   expected hash, then publish and verify.
4. Re-run the AGENTS.md block step (idempotent replace inside the markers).
5. Bootstrap as in step 10.
6. `systemctl --user daemon-reload`; `systemctl --user restart` the timers only
   if their unit content changed (`enable --now` is enough otherwise).
7. Re-commit `[complete] 1`.
8. If anything fails, leave `[complete]` absent and exit non-zero. The wrapper
   refuses to run meanwhile.

## --uninstall sequence

1. Take the lock (`flock -w 120 9`). If the manifest is absent, print
   `"nothing to uninstall"` and exit 0.
2. Read the manifest. For each unit listed in `[units]`: verify its current
   content hash (or `old_sha256`) matches; only then
   `systemctl --user disable --now <unit>`; on mismatch warn and skip that unit.
3. `systemctl --user daemon-reload`.
4. For each unit file whose hash matched, remove it.
5. For each path in `[files]`: verify identity (sha256 for regular files,
   symlink target for symlinks, accepting `old_sha256`); remove on match, warn
   and skip on mismatch.
6. If `[agents_md]` is present: require exactly one begin marker and one end
   marker, begin before end, and the enclosed text hashing to the recorded
   `block_sha256`. Only then remove the markers and the block. Otherwise warn
   and preserve the file.
7. Remove the manifest. Keep `$STATE_DIR/refresh.lock` (do not delete it).
8. Print what was removed and what was skipped.

## --dry-run

Print every action (path, intended change) and exit 0 without touching the
filesystem or systemd.

## Code quality

- Every function documented with a one-line comment above it
- Quote every variable expansion
- `die()` helper writing to stderr and exiting with a code
- `usage()` printing the full flag list
- No `set -x`
- Refuse to run as root (`[ "$(id -u)" -eq 0 ]` → die) because user units would
  land in the wrong place
- The script must be safe to re-run: a second plain `install.sh` without
  `--update` on an intact install must abort cleanly with a message telling the
  user to pass `--update`

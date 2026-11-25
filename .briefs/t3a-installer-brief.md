# Task 3a — `install.sh`: setup, credentials, scripts, units

Write ONE new file: `install.sh` at the repo root. Do not modify any other file.

Bash 5, `set -euo pipefail`. Dependencies: coreutils, `flock`, `systemctl`,
`systemd-analyze`, `python3`, `sha256sum`.

`STATE_DIR="${STATE_DIR:-$HOME/.software-factory}"` (always overridable).
`REPO_ROOT` = directory containing this script (`cd "$(dirname "$0")" && pwd`).

Keep the whole file under ~300 lines. Write it in one go; do not stop to ask.

## Flags

```
--interval-refresh X   default 4h; accept 1h..4h, reject longer
--interval-probe X     default 30m; accept 15m..1h
--no-harness-integration
--update
--uninstall
--dry-run
-h | --help
```

Unknown flag: usage to stderr, exit 2.

## Interval parsing

Accept `30m`, `1h`, `2h`, `90m`. Minutes must divide 60; hours must divide 24.
Map to OnCalendar: minutes `M` -> `*:0/M`; hours `H` -> `*-*-* 00/H:00:00`.
Validate each with `systemd-analyze calendar "<expr>"`; abort on failure.

## Helpers

- `die()` — message to stderr, exit with a code
- `usage()` — full flag list
- `sha_of()` — `sha256sum "$1" | cut -d' ' -f1`
- `manifest_add()` / `manifest_rewrite()` — see below

Refuse to run as root (`id -u` = 0 -> die). Never `set -x`. Quote every expansion.

## Manifest

`$STATE_DIR/installed-manifest`, line oriented, rewritten atomically (temp file
in the same dir + `mv`). Sections:

```
[meta]
version=1
state_dir=/home/u/.software-factory
repo=/home/u/Dev/AI/free-model-check

[files]
<path> sha256:<hex> [old_sha256:<hex>]

[units]
free-models-refresh.service sha256:<hex>
free-models-refresh.timer sha256:<hex>
free-models-probe.service sha256:<hex>
free-models-probe.timer sha256:<hex>

[agents_md]
path=/home/u/Dev/agent-harnesses/instructions/AGENTS.md
block_sha256:<hex>

[complete]
1
```

For a symlink record `symlink:<target>` instead of a sha. Never list the
AGENTS.md file itself under `[files]`. `[complete]` holds `1` only after a
fully successful install.

## Two-phase publish

For each artifact: (1) append its path to the manifest and rewrite atomically,
(2) create/replace it (temp file in the target dir, `chmod`, `mv`), (3) verify
the hash matches. On failure exit non-zero leaving the manifest accurate.

## This task: steps 1-7 only

1. Prerequisites: `python3 --version` >= 3.10; `command -v opencode` (store the
   absolute path); `command -v systemctl`; `command -v systemd-analyze`;
   `"$REPO_ROOT/.env"` exists and contains `AA_API_KEY`. Specific message + exit 1
   on any failure.
2. `mkdir -p "$STATE_DIR"`.
3. Lock: `exec 9>"$STATE_DIR/refresh.lock"; /usr/bin/flock -w 120 9 || die 75`.
   Prefer `/usr/bin/flock`, fall back to `command -v flock`.
4. **Preflight**: for every non-AGENTS.md destination (installed scripts,
   credentials file, four units, skill symlink, expected output files) — if it
   exists and is not in the manifest, `die` with
   `"refusing to overwrite <path> (not owned by this installer)"` unless
   `--update` was given and it IS owned. AGENTS.md is expected to exist; only
   validate that its resolved canonical path exists.
5. Credentials: parse `"$REPO_ROOT/.env"` as data (never `source`). Keep only
   `AA_API_KEY` and `OPENROUTER_API_KEY`. Write `$STATE_DIR/free-models.env`
   with those plus `OPENCODE_BIN=<abs path>`; create with `umask 077` so it is
   0600 from the first write. Warn (do not fail) if `OPENROUTER_API_KEY` is absent.
6. Install scripts: `install -m 0755 scripts/free-models-probe.py
   "$STATE_DIR/free-models-probe.py"` and the same for
   `scripts/free-models-refresh.sh`.
7. Write the four units to `$HOME/.config/systemd/user/` (create the dir).
   Substitute absolute paths.

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
OnCalendar=<refresh expr>
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
OnCalendar=<probe expr>
Persistent=true
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
```

Stop after step 7 with a comment `# --- continued in part 2 ---` and exit 0.
Do NOT implement skill install, AGENTS.md, bootstrap, timers, update or
uninstall — those are separate tasks.
# Task 2 — write `scripts/free-models-refresh.sh`

Write ONE new file: `scripts/free-models-refresh.sh`. Do not modify any other
file.

POSIX `/usr/bin/env bash` script, `set -euo pipefail`. No external dependencies
beyond coreutils, flock, timeout, python3.

## Purpose

The only entry point allowed to touch the free-model catalogue. It holds an
exclusive lock, refreshes the catalogue through the filter script, stages output
safely, then runs the probe. Both systemd timers call this script (the probe
timer passes `--skip-filter`).

## Paths (derived from `$HOME`, overridable by environment)

- `STATE_DIR="${STATE_DIR:-$HOME/.software-factory}"`
- `REPO_DIR="${REPO_DIR:-/home/luandro/Dev/AI/free-model-check}"` — the checkout
  that contains `free_model_filter.py`; overridable so tests can point elsewhere
- `LOCK_FILE="$STATE_DIR/refresh.lock"`
- `ENV_FILE="$STATE_DIR/free-models.env"`
- `MANIFEST="$STATE_DIR/installed-manifest"`
- `CATALOGUE="$STATE_DIR/free_models.json"`
- `PROBE="$STATE_DIR/free-models-probe.py"`

## Flags

```
--skip-filter     Do not run the catalogue filter; only run the probe
--dry-run         Print the commands that would run and exit 0
-h|--help         Usage
```

Any other argument: print usage to stderr, exit 2.

## Order of operations (exact)

1. `mkdir -p "$STATE_DIR"`
2. Acquire the exclusive lock:
   `exec 9>"$LOCK_FILE"; flock -w 300 9` — if this fails, print
   `"another refresh is running; giving up"` to stderr and exit 75.
   The lock MUST be taken before loading credentials.
3. Installation-state check. If `$MANIFEST` is missing, print
   `"free-models-junior is not installed (no manifest); nothing to do"` to
   stderr and exit 0. If the manifest exists but does not contain a line exactly
   `complete`, print `"install is partial; refusing to run"` to stderr and exit
   76.
4. Load credentials from `$ENV_FILE` as data:
   - `while IFS='=' read -r key value` over the file, skipping blank lines and
     lines starting with `#`
   - strip one pair of surrounding single or double quotes from `value`
   - `export "$key=$value"` only when `key` matches `^[A-Za-z_][A-Za-z0-9_]*$`
   - never print values
   - if the file is missing, warn to stderr and continue
5. If not `--skip-filter`:
   a. `STAGE="$STATE_DIR/staging-$$"`; `mkdir -p "$STAGE"`
   b. `trap 'rm -rf "$STAGE"' EXIT`
   c. Run the filter with a hard timeout:
      `timeout 300 python3 "$REPO_DIR/free_model_filter.py" --require-agent-ready --output-prefix "$STAGE/free_models"`
      — stream its stdout/stderr through unchanged
      — if exit != 0: print `"catalogue refresh failed; keeping previous catalogue"`
        to stderr, exit 78 (the staged dir is removed by the trap)
   d. Validate the staged catalogue with a python one-liner or small heredoc:
      must parse as JSON, must have `summary.generated_unix` as a number, must
      have a `models` list. On validation failure print
      `"staged catalogue failed validation; keeping previous catalogue"` and
      exit 79.
   e. Publish atomically: for each file matching `"$STAGE"/free_models*` that is a
      regular file, `mv -f` it to `"$STATE_DIR/$(basename "$f")"`. `mv` within
      the same filesystem is atomic per file.
   f. `rm -rf "$STAGE"`; `trap - EXIT`
6. Run the probe:
   `python3 "$PROBE" --catalogue "$CATALOGUE" --state "$STATE_DIR/free_models_junior.json" --env-file "$ENV_FILE"`
   — if exit != 0, print `"probe failed (exit N)"` to stderr and exit with that
   same code
7. Exit 0.

## Behaviour details

- The script must never `cd` into the repo; it passes absolute paths.
- Never write the catalogue anywhere except `$STATE_DIR` (staging lives inside
  `$STATE_DIR` so `mv` never crosses filesystems).
- Never echo secrets. Do not enable `set -x`.
- If `python3` is missing, exit 77 with a clear message.
- If `$PROBE` is missing, exit 77 with a clear message.
- Print a single final line to stdout on success:
  `refresh ok: catalogue mtime <epoch> state <epoch>` using `stat -c %Y`.
- Add a header comment block explaining the caller contract (timers, manual
  use, lock semantics).

## Testing hook

If `STATE_DIR` and `REPO_DIR` are overridden to a temp dir, the script must run
end to end with a stub `free_model_filter.py` and stub `free-models-probe.py`
placed in the expected locations. Do not hardcode any absolute path other than
the `REPO_DIR` default.

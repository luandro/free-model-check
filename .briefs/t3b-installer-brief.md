# Task 3b — `install.sh` part 2: skill, AGENTS.md, bootstrap, timers

Extend the EXISTING `install.sh` (it currently ends after writing the systemd
units, with a comment `# --- continued in part 2 ---`). Do not modify any other
file. Keep the file under ~420 lines total.

Replace that trailing comment with the steps below, then the manifest commit and
summary.

## 8. Skill

- `mkdir -p "$HOME/.agents/skills"`
- Link `$HOME/.agents/skills/free-models-junior` ->
  `$REPO_ROOT/scripts/skills/free-models-junior`.
- If the link already points there, leave it.
- If it exists pointing elsewhere and is not owned (not in the manifest),
  `die` with a clear message.
- Record it in `[files]` as `symlink:<target>`.

## 9. AGENTS.md managed block (unless --no-harness-integration)

- Resolve the canonical path: `readlink -f "$HOME/.claude/CLAUDE.md"`. If that
  is not a symlink or does not resolve, fall back to
  `$HOME/Dev/agent-harnesses/instructions/AGENTS.md`.
- Create the file with a single `# Shared harness instructions` heading if absent.
- If exactly one begin marker AND one end marker already exist:
  - if the manifest records this block, replace the enclosed text;
  - otherwise warn `"AGENTS.md already contains a free-models-junior block; leaving it alone"` and skip.
- Otherwise append, after a blank line, exactly:

```
<!-- free-models-junior:begin -->
For free coding models, read `~/.software-factory/free_models_junior.json`
and use the `free-models-junior` skill to delegate junior tasks.
<!-- free-models-junior:end -->
```

- Before editing, back up to
  `$(dirname "$canonical")/backups/AGENTS.md.$(date +%Y%m%dT%H%M%S).bak`
  (create `backups/` if absent; skip with a warning if not writable).
- Record `path=` and `block_sha256:<sha of the enclosed text>` in `[agents_md]`.
  Record the block in the manifest BEFORE publishing the edit (intent first).

## 10. Bootstrap (still under the lock, do NOT call the wrapper)

- `STAGE="$STATE_DIR/staging-bootstrap"`; `mkdir -p "$STAGE"`
- `timeout 300 python3 "$REPO_ROOT/free_model_filter.py" --require-agent-ready
  --output-prefix "$STAGE/free_models"` — on non-zero, print the failure, do NOT
  write `[complete]`, exit 1.
- Validate the staged JSON with a python heredoc: must parse, `summary.generated_unix`
  must be a number, `models` must be a list. On failure exit 1.
- `mv` each `$STAGE/free_models*` regular file into `$STATE_DIR`.
- `timeout 700 python3 "$STATE_DIR/free-models-probe.py"` — on non-zero, exit 1.
- Record every output file created in `[files]`.
- `rm -rf "$STAGE"`

## 11. Timers

`systemctl --user daemon-reload` then
`systemctl --user enable --now free-models-refresh.timer free-models-probe.timer`.

## 12. Commit and summary

- Write `[complete]` with `1`, rewrite the manifest atomically.
- Print: state dir, repo, intervals, `systemctl --user list-timers 'free-models-*'`,
  and the uninstall command.

## --update

1. Require an existing manifest with `[complete]`.
2. Lock. Clear `[complete]` BEFORE touching artifacts.
3. For each owned artifact being replaced, record `old_sha256` next to the new
   expected hash, then publish and verify.
4. Re-run the AGENTS.md step (idempotent replace inside the markers).
5. Bootstrap as in step 10.
6. `systemctl --user daemon-reload`; `enable --now` the timers.
7. Re-commit `[complete]`.
8. On any failure leave `[complete]` absent and exit non-zero.

## --dry-run

Print every action and exit 0 without touching the filesystem or systemd.
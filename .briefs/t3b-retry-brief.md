# Task 3b (retry) — extend install.sh with REAL shell code

The file `install.sh` currently ends after writing the four systemd units. Its
last line is a `manifest_add` call for `free-models-probe.timer`.

Append the steps below as **executable bash**. Do not modify any other file.

## CRITICAL

- Every line you add must be runnable shell. The previous attempt pasted this
  brief into the file as `#` comments — that is wrong and will be rejected.
- Do NOT write markdown, bullet lists, or numbered prose into the file.
- Use real commands: `mkdir`, `ln`, `install`, `cat >file <<EOF`, `if`, `mv`,
  `systemctl`, `manifest_add`, `die`.
- Comments are fine only as short `#` explanations of real code.
- Keep install.sh under 420 lines total.
- Finish by running `bash -n install.sh`.

## 8. Skill

```bash
mkdir -p "$HOME/.agents/skills"
SKILL_LINK="$HOME/.agents/skills/free-models-junior"
SKILL_TARGET="$REPO_ROOT/scripts/skills/free-models-junior"
```
- If `$SKILL_LINK` is already a symlink resolving to `$SKILL_TARGET`, leave it.
- If it exists (link or dir) and is not recorded in the manifest, `die`.
- Otherwise `ln -s "$SKILL_TARGET" "$SKILL_LINK"`.
- `manifest_add "[files]" "$SKILL_LINK symlink:$SKILL_TARGET"`

## 9. AGENTS.md block

Skip entirely when `HARNESS_INTEGRATION` is 0.

Resolve `$AGENTS_MD_PATH` (already computed earlier in the file). Create it with
a single `# Shared harness instructions` heading if absent.

Use `BEGIN_MARK='<!-- free-models-junior:begin -->'` and
`END_MARK='<!-- free-models-junior:end -->'`.

Count occurrences with `grep -cF`. If both are exactly 1: when the manifest
already records `path=$AGENTS_MD_PATH`, replace the enclosed lines (use
`sed` or `awk` to rewrite between the markers); otherwise print a warning and
skip. If not both 1: append a blank line then the block via a
`cat >>"$AGENTS_MD_PATH" <<EOF` heredoc containing the two markers and:

```
For free coding models, read `~/.software-factory/free_models_junior.json`
and use the `free-models-junior` skill to delegate junior tasks.
```

Before any edit, copy the file to
`$(dirname "$AGENTS_MD_PATH")/backups/AGENTS.md.$(date +%Y%m%dT%H%M%S).bak`
(create `backups/` with `mkdir -p`; skip the backup with a warning if that fails).

Then record the enclosed text's hash: extract the lines between the markers into
a temp file, `sha_of` it, and write `path=...` and `block_sha256:...` under an
`[agents_md]` section (add the section header the same way `manifest_add` does).

## 10. Bootstrap

```bash
STAGE="$STATE_DIR/staging-bootstrap"
rm -rf "$STAGE"; mkdir -p "$STAGE"
timeout 300 python3 "$REPO_ROOT/free_model_filter.py" --require-agent-ready \
    --output-prefix "$STAGE/free_models" || die "catalogue bootstrap failed" 1
```
Validate with a `python3 - "$STAGE/free_models.json" <<'PY'` heredoc that exits 1
unless the JSON parses, `summary.generated_unix` is a number, and `models` is a
list. Then `mv "$STAGE"/free_models* "$STATE_DIR"/` for regular files only.
Then `timeout 700 python3 "$STATE_DIR/free-models-probe.py" || die "probe bootstrap failed" 1`.
Record each moved file with `manifest_add "[files]"`. Finally `rm -rf "$STAGE"`.

## 11. Timers

```bash
systemctl --user daemon-reload
systemctl --user enable --now free-models-refresh.timer free-models-probe.timer
```

## 12. Complete + summary

Write `[complete]` with `1` (use `manifest_add "[complete]" "1"`), then print the
state dir, repo, both intervals, `systemctl --user list-timers 'free-models-*'`,
and the line `Uninstall with: ./install.sh --uninstall`.

## --update

Before step 8, if `DO_UPDATE` is 1: require the manifest to exist and contain
`[complete]`, then clear `[complete]` by rewriting the manifest without that
section. After step 11 succeeds, `[complete]` is re-added by step 12.

## --dry-run

If `DRY_RUN` is 1 at the top of this appended section, print the planned actions
for steps 8-12 and `exit 0` without touching anything.
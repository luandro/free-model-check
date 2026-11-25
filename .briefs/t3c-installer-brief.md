# Task 3c — `install.sh` part 3: uninstall

Extend the EXISTING `install.sh` with the `--uninstall` path. Do not modify any
other file. Keep the file under ~520 lines total.

Add a function `do_uninstall()` and call it from the flag parsing when
`--uninstall` is present (before any prerequisite checks — uninstall must work
on a broken or partial install).

## do_uninstall

1. `mkdir -p "$STATE_DIR"` (needed for the lock file).
2. Lock: `exec 9>"$STATE_DIR/refresh.lock"; /usr/bin/flock -w 120 9 || die 75`.
   Prefer `/usr/bin/flock`, fall back to `command -v flock`.
3. If `$STATE_DIR/installed-manifest` is absent: print `"nothing to uninstall"`,
   exit 0.
4. Read the manifest into memory (arrays or a temp parse).
5. **Units, per unit, identity-verified**: for each unit in `[units]`, compare
   the current file's sha256 (or the recorded `old_sha256`) with the recorded
   hash. Only on a match run `systemctl --user disable --now <unit>`. On
   mismatch warn `"<unit> was modified after install; leaving it alone"` and skip.
   Never batch several unit names into one systemctl call.
6. `systemctl --user daemon-reload`.
7. Remove each unit file whose hash matched.
8. **`[files]`, identity-verified**: for each path, verify identity (sha256 for
   regular files, `symlink:<target>` for links, accepting `old_sha256`). Remove
   on match; warn and skip on mismatch.
9. **`[agents_md]`** (if present): require exactly one begin marker and one end
   marker, begin before end, and the enclosed text hashing to `block_sha256`.
   Only then remove the markers and the enclosed lines. Otherwise warn
   `"AGENTS.md block was modified; leaving it alone"` and preserve the file.
   Never restore from the backup.
10. Remove the manifest.
11. Keep `$STATE_DIR/refresh.lock` (do not delete it — removing it while a
    process holds it would let a later run lock a different inode).
12. Print what was removed and what was skipped.

## Rules

- Quote every expansion.
- `die()` for fatal errors, with a code.
- No `set -x`.
- The whole file must stay syntactically valid: finish with
  `bash -n install.sh` passing.
- Do not implement anything outside this task; parts 1 and 2 are already written.
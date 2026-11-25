# Task 5 — README documentation for the junior pipeline

Edit ONLY `README.md`. Do not create or modify any other file.

Add one new section titled `## Automated free-model junior pipeline` immediately
after the existing `## Quick start` section, and add one line to the existing
`## Development` section.

## Section content (write it as flowing prose + one bash block + one table)

Explain, in the register the README already uses (plain, factual, no marketing):

- `./install.sh` sets up two systemd **user** timers and keeps a state file:
  - `free-models-refresh.timer` — every 4 hours by default, rebuilds the
    catalogue in `~/.software-factory/`
  - `free-models-probe.timer` — every 30 minutes, probes each zero-priced route
    with a one-token completion and rewrites
    `~/.software-factory/free_models_junior.json`
- Why the refresh is 4 hours: the probe refuses to run against a catalogue older
  than 6 hours, so the refresh keeps two hours of head-room for jitter.
- The probe is provider-aware: OpenRouter over HTTPS with `OPENROUTER_API_KEY`,
  Kilo anonymously over HTTPS, OpenCode through its CLI (`OPENCODE_BIN`). Only
  routes verified `zero-priced` are ever probed; free-tier and mirror-listed
  routes are never touched with credentials.
- Availability is tri-state: `true` (reachable), `false` (confirmed dead or
  auth failure), `null` (unknown: rate-limited, timeout, or skipped). Only
  `true` routes are selectable.
- The skill `free-models-junior` is symlinked into `~/.agents/skills/` and
  appended as a managed block to the canonical shared AGENTS.md unless
  `--no-harness-integration` is passed.
- Ownership: every artifact is recorded in
  `~/.software-factory/installed-manifest`, so `--uninstall` removes only what
  the installer created, verifying content hashes first.
- Credentials: read from `.env`, copied to `~/.software-factory/free-models.env`
  with mode 0600, because systemd user timers do not inherit an interactive
  shell. Rotate a key by editing `.env` and running `./install.sh --update`.
- The catalogue filter never writes into the checkout: output is staged inside
  `~/.software-factory/` and moved into place atomically, so a failed refresh
  keeps the previous catalogue.

Single bash block:

```bash
./install.sh                      # install and bootstrap
./install.sh --update             # re-read keys, replace owned artifacts
./install.sh --uninstall          # remove everything the installer created
./install.sh --dry-run            # show planned actions
systemctl --user list-timers 'free-models-*'
```

Table of the files it manages:

| Path | Purpose |
|---|---|
| `~/.software-factory/free_models.json` | Catalogue produced by the filter |
| `~/.software-factory/free_models_junior.json` | Probe state read by the skill |
| `~/.software-factory/free-models.env` | Credentials for systemd (0600) |
| `~/.software-factory/installed-manifest` | Ownership record for uninstall |
| `~/.config/systemd/user/free-models-*` | Timers and services |

Close with: junior delegation is best for scoped tasks with explicit acceptance
criteria, and every junior diff must be reviewed.

## Development section

Append one line:

```
bash -n install.sh && bash -n scripts/free-models-refresh.sh  # syntax check
python3 -m py_compile scripts/free-models-probe.py
```

## Constraints

- Do not renumber or reword other sections.
- Do not add badges, images, or emoji.
- Match the existing heading level style (`##`).
- Keep the section under ~70 lines total.

# Task 4 — write `scripts/skills/free-models-junior/SKILL.md`

Write ONE new file: `scripts/skills/free-models-junior/SKILL.md`. Create parent
directories. Do not modify any other file.

Keep it small: 60–90 lines of Markdown. It is an agent skill, not documentation.

## Required frontmatter

```yaml
---
name: free-models-junior
description: Delegate small, well-scoped coding tasks to free benchmark-strong models discovered by free-model-check, using the state file at ~/.software-factory/free_models_junior.json.
---
```

## Required content (adapt wording, keep every constraint)

### What it is
One sentence: free, benchmark-strong models as junior developers for scoped
tasks; the list is refreshed automatically by systemd timers.

### When to use
- small, well-defined tasks: bug fixes, boilerplate, small refactors, test
  scaffolding
- trying a change cheaply before spending premium tokens
- parallel junior work while a senior reviews

### When NOT to use
- architecture decisions, multi-file redesigns, anything needing repo-wide
  judgement
- tasks that must not leave the machine (the provider sees the prompt)

### Procedure (numbered, exact)

1. Acquire the shared lock and verify the install is complete:
   ```bash
   exec 8>"$HOME/.software-factory/refresh.lock"
   flock -s -w 5 8 || { echo "state busy; retry later"; exit 1; }
   grep -qx 1 "$HOME/.software-factory/installed-manifest" 2>/dev/null || true
   ```
   Simpler and acceptable alternative, state it explicitly: read the manifest
   and refuse to continue unless a line `[complete]` followed by `1` exists.
   Keep exactly ONE of these two approaches, not both.

2. Read `$HOME/.software-factory/free_models_junior.json`. Reject the whole file
   (and tell the user to wait for the next probe cycle) when any of these hold:
   - `catalogue_generated_unix` is older than 6 hours
   - `catalogue_generated_unix` is missing, non-numeric, or in the future
   - `catalogue_generation_id` is missing or not of the form `sha256:<hex>`
   - the state's `catalogue_generation_id` differs from
     `sha256sum "$HOME/.software-factory/free_models.json"`

3. Choose a model: keep only models with `agent_ready: true` and a non-null
   `best_available_route`. Sort by `coding` descending, then `agentic`
   descending. Take the first whose route's `last_probed` is younger than 2
   hours and parseable. Everything else is stale.

4. Build the model id: `"<provider>/<route_model>"` from
   `best_available_route`, e.g. `opencode/deepseek-v4.1-flash-free` or
   `kilo/baidu/ernie-5.5-flash-preview:free`. Do not invent ids.

5. Delegate:
   ```bash
   opencode run --model "$MODEL_ID" "$BRIEF"
   ```
   - pass the brief as ONE argument; never shell-interpolate untrusted text into
     the command line
   - if the CLI is missing or the model is unknown (`opencode models | grep -F
     "$MODEL_ID"` returns nothing), pick the next candidate

6. Review: read the diff yourself and check it against the stated acceptance
   criteria. A free-model junior is unproven — never merge unreviewed output.

### State file fields (short table)
`model`, `coding`, `agentic`, `intelligence`, `agent_ready`,
`routes[].available` (`true` / `false` / `null` = unknown),
`routes[].latency_ms`, `routes[].last_probed`, `best_available_route`,
`catalogue_generated_unix`, `catalogue_generation_id`.

### Notes
- `null` availability means unprobed or transient — not dead. Only
  `available: true` is selectable.
- If nothing is available, run `~/.software-factory/free-models-refresh.sh`
  manually or wait for the next 30-minute probe.
- Do not edit the state file.
- A one-token probe proves reachability, not coding quality.
- Paths: state `~/.software-factory/free_models_junior.json`, catalogue
  `~/.software-factory/free_models.json`, lock
  `~/.software-factory/refresh.lock`, wrapper
  `~/.software-factory/free-models-refresh.sh`.

## Style

Imperative, no marketing language, no emoji. Tables and short code blocks only
where they carry information.

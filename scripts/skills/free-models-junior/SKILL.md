---
name: free-models-junior
description: Delegate small, well-scoped coding tasks to free benchmark-strong models discovered by free-model-check, using the state file at ~/.software-factory/free_models_junior.json.
---

## What it is

Free, benchmark-strong models as junior developers for scoped tasks; the list is
refreshed automatically by systemd timers. `STATE_DIR` below defaults to `~/.software-factory`.

## When to use

- Small, well-defined tasks: bug fixes, boilerplate, small refactors, test scaffolding
- Trying a change cheaply before spending premium tokens
- Parallel junior work while a senior reviews

## When NOT to use

- Architecture decisions, multi-file redesigns, anything needing repo-wide judgement
- Tasks that must not leave the machine (the provider sees the prompt)

## Procedure

1. Verify the install is complete. Read `$STATE_DIR/installed-manifest` and refuse to continue unless it has a `[complete]` section whose only line is `1`. Otherwise the install is partial or an update is mid-flight: stop, the state file cannot be trusted.
2. Read `$STATE_DIR/free_models_junior.json`. Reject the whole file (and tell the user to wait for the next probe cycle) when any of these hold:
   - `catalogue_generated_unix` is older than 6 hours
   - `catalogue_generated_unix` is missing, non-numeric, or in the future
   - `catalogue_generation_id` is missing or not of the form `sha256:<hex>`
   - the state's `catalogue_generation_id` does not equal `sha256:<digest>` where `<digest>` is the first field of `sha256sum "$STATE_DIR/free_models.json"` — a new catalogue was published but the probe has not run against it yet
3. Choose a model: keep only models with `agent_ready: true` and a non-null `best_available_route`. Sort by `coding` descending, then `agentic` descending. Take the first whose chosen route's `last_probed` is younger than 2 hours and parseable. Everything else is stale.
4. Build the model id: `"<provider>/<route_model>"` from `best_available_route`, e.g. `opencode/deepseek-v4.1-flash-free` or `kilo/baidu/ernie-5.5-flash-preview:free`. Do not invent ids.
5. Delegate:
   ```bash
   opencode run --model "$MODEL_ID" "$BRIEF"
   ```
   - Pass the brief as ONE argument; never shell-interpolate untrusted text into the command line
   - If the CLI is missing or the model is unknown (check with `opencode models`), pick the next candidate
6. Review: read the diff yourself and check it against the stated acceptance criteria. A free-model junior is unproven — never merge unreviewed output.

## State file fields

| Field | Meaning |
|---|---|
| `model` | Model name |
| `coding` | Coding benchmark score |
| `agentic` | Agentic benchmark score |
| `intelligence` | Intelligence benchmark score |
| `agent_ready` | Whether the model is ready for agent use |
| `routes[].available` | `true` / `false` / `null` = unknown |
| `routes[].latency_ms` | Route latency in milliseconds |
| `routes[].last_probed` | Last probe timestamp |
| `best_available_route` | Best available route for the model |
| `catalogue_generated_unix` | Catalogue generation timestamp (Unix) |
| `catalogue_generation_id` | Catalogue generation ID (`sha256:<hex>`) |

## Notes

- `null` availability means unprobed or transient — not dead. Only `available: true` is selectable.
- If nothing is available, run `~/.software-factory/free-models-refresh.sh` manually or wait for the next 30-minute probe.
- Do not edit the state file.
- A one-token probe proves reachability, not coding quality.
- Paths: state `STATE_DIR/free_models_junior.json`, catalogue `STATE_DIR/free_models.json`, lock `STATE_DIR/refresh.lock`, wrapper `STATE_DIR/free-models-refresh.sh` (STATE_DIR defaults to `~/.software-factory`).
# Task 1 — write `scripts/free-models-probe.py`

Write ONE new file: `scripts/free-models-probe.py` in the repo root (create the
`scripts/` directory). Do not modify any other file.

Python 3.10+, standard library only. No third-party imports. Executable
(`#!/usr/bin/env python3`, chmod +x is done afterwards).

## Purpose

Read the free-model catalogue (`free_models.json` produced by
`free_model_filter.py`), select models that are apt for coding, probe each
zero-priced route for liveness, and publish a state file that a "junior dev"
skill reads.

## Paths (all overridable by CLI flags)

- Catalogue: `~/.software-factory/free_models.json`
- State output: `~/.software-factory/free_models_junior.json`
- Credentials: `~/.software-factory/free-models.env`
- Repo fallback for credentials: `<repo>/.env` (only if the first is absent)

`~/.software-factory` is `$HOME/.software-factory`.

## Credentials

Read the env file as DATA. Do not use `source`, `eval`, or `os.system`.
Parse lines of the form `KEY=VALUE`, strip surrounding single or double quotes,
ignore blank lines and `#` comments. Never log values. Keys used:

- `OPENROUTER_API_KEY` — required for OpenRouter routes
- `OPENCODE_BIN` — absolute path to the `opencode` executable, required for
  OpenCode routes (default fallback `shutil.which("opencode")`)
- `AA_API_KEY` — accepted and ignored (the probe never calls AA)

If the env file is missing, fall back to the repo `.env`; if neither exists,
continue with no keys (all authenticated providers become `null`).

## Catalogue freshness gate

Load the catalogue JSON. Read `summary.generated_unix`. Exit with code 3 and a
clear stderr message if:

- the catalogue is missing or unreadable or not valid JSON
- `summary.generated_unix` is missing, not an int/float, or in the future
  (more than 300 s ahead of now, to allow clock skew)
- the catalogue is older than 6 hours (`6 * 3600` seconds)

Never write the state file when this gate fails — the previous state must be
preserved.

## Model selection

For each entry in `catalogue["models"]`:

- include only if `quality.coding >= 25` OR `quality.agentic >= 25`
  (missing, null, or non-numeric fails this gate)
- AND `agent_readiness.status` is `True`

## Route selection and probing

For each included model, iterate `free_routes`. Only probe a route when its
`verification` equals `"zero-priced"`. Routes with any other verification are
recorded in the output with `"available": null` and
`"error": "skipped: not zero-priced"` — never probed with credentials.

Provider base URLs (exact):

- `openrouter` → `https://openrouter.ai/api/v1`
- `kilo` → `https://ai-gateway.kilo.ai/api/v1`
- `opencode` → CLI probe, no HTTP

HTTP probe: `POST {base}/chat/completions` with JSON body
`{"model": route_model, "messages": [{"role": "user", "content": "Say ok"}],
"max_tokens": 1, "temperature": 0}`.

Headers: `Content-Type: application/json`. Send
`Authorization: Bearer $OPENROUTER_API_KEY` only for openrouter. Send nothing
for kilo (anonymous).

Use `urllib.request`. Do not follow redirects to a different host: build an
opener with a custom `HTTPRedirectHandler` that raises when the redirect host
differs from the original host.

OpenCode CLI probe: run
`[OPENCODE_BIN, "run", "--model", f"opencode/{route_model}", "Say ok"]`.
Treat exit code 0 with any stdout as available.

## Result classification (must be exact)

| Outcome | `available` | `http_status` | `error` |
|---|---|---|---|
| HTTP 200, JSON parses, has `choices[0].message` | `true` | 200 | `null` |
| HTTP 200, body has top-level `error` | `false` | 200 | `body error message` |
| HTTP 401 / 403 | `false` | code | `auth failed` |
| HTTP 429 | `null` | 429 | `rate limited` |
| HTTP 5xx | `null` | code | `server error` |
| Redirect to different host | `false` | 3xx | `cross-host redirect` |
| Timeout / DNS / connection error | `null` | `null` | short reason |
| Missing API key for provider | `null` | `null` | `no api key` |
| CLI exit non-zero | `false` | `null` | first line of stderr |
| Deadline hit before probing | `null` | `null` | `sweep deadline` |

`null` means "unknown / do not trust"; `false` means "confirmed dead".

## Sweep deadline (hard wall-clock)

Total budget default 600 s (`--sweep-budget`). Capture `time.monotonic()` once
at the start. Every probe runs in its own child process via
`subprocess.Popen(..., start_new_session=True)`. The parent registers the
child's pid, waits with a bounded timeout equal to the remaining budget, and on
expiry:

1. `os.killpg(child.pid, signal.SIGTERM)`
2. wait up to 5 s
3. if `os.killpg(child.pid, 0)` still succeeds, `os.killpg(child.pid, signal.SIGKILL)`

Once the deadline has passed, do not start new probes; mark every unprobed
route `"available": null, "error": "sweep deadline"`.

The child must not begin its network work before the parent has recorded its
pid. Use a `multiprocessing.Pipe` (or an inherited fd) handshake: child sends
`b"ready"` first, parent waits for it before the timer starts counting that
probe.

Implement each probe as a module-level function so it is picklable, or better:
have the child run this same script with a hidden `--probe-one` flag and pass
the request as JSON on stdin, returning JSON on stdout. Pick one approach and
keep it simple and robust.

## Output state file

Atomically write JSON to the state path: temp file in the same directory, then
`os.replace`. `ensure_ascii=False`, indent 2, trailing newline.

Schema:

```json
{
  "generated_unix": 1764000000,
  "catalogue_generated_unix": 1763990000,
  "catalogue_generation_id": "sha256:<hex>",
  "source_repo": "/abs/path/to/repo",
  "sweep_budget_seconds": 600,
  "models": [
    {
      "model": "ERNIE 5.5 Flash Preview",
      "llmstats_id": "ernie-5.5-flash-preview",
      "organization": "Baidu",
      "coding": 37.31,
      "agentic": 38.01,
      "intelligence": 33.599,
      "agent_ready": true,
      "routes": [
        {
          "provider": "kilo",
          "route_model": "baidu/ernie-5.5-flash-preview:free",
          "model_name": "Baidu: ERNIE-5.5-Flash-Preview (free)",
          "available": true,
          "latency_ms": 812,
          "http_status": 200,
          "error": null,
          "last_probed": "2026-11-24T18:00:00Z"
        }
      ],
      "best_available_route": {
        "provider": "kilo",
        "route_model": "baidu/ernie-5.5-flash-preview:free"
      }
    }
  ]
}
```

Rules:

- `catalogue_generation_id` = `"sha256:" + hashlib.sha256(raw_catalogue_bytes).hexdigest()`
- `latency_ms` = integer milliseconds, or `null` when not measured
- `last_probed` = UTC ISO-8601 with a trailing `Z`, second precision
- `best_available_route` = the route with `available: true` and the lowest
  `latency_ms` (nulls sort last; ties broken by provider name); `null` when no
  route is available
- model order = highest `coding` first, then `agentic`, then name
- if no model passes selection, still write a valid state file with an empty
  `models` list

## CLI

```
free-models-probe.py [--catalogue PATH] [--state PATH] [--env-file PATH]
                     [--sweep-budget SECONDS] [--request-timeout SECONDS]
                     [--verbose]
```

Defaults: catalogue `~/.software-factory/free_models.json`, state
`~/.software-factory/free_models_junior.json`, env file
`~/.software-factory/free-models.env`, sweep budget 600, request timeout 30.

`--verbose` prints one line per route to stderr. Always print a one-line JSON
summary to stdout on success, for example:
`{"models": 3, "routes_probed": 4, "available": 3, "unknown": 1, "dead": 0}`

Exit codes: `0` success, `2` bad CLI usage, `3` catalogue unusable or stale,
`4` unexpected internal error (traceback to stderr).

## Code quality requirements

- Type hints on all functions, `from __future__ import annotations`
- Docstrings on every function
- No global mutable state except argparse-free constants
- Keep functions under ~60 lines; factor helpers
- `main(argv: list[str] | None = None) -> int` and
  `if __name__ == "__main__": raise SystemExit(main())`
- Never print secrets; never log Authorization headers
- Handle `KeyboardInterrupt` by exiting 130 without writing a partial state file

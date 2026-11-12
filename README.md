# free-model-check

Find genuinely free, benchmark-strong LLM routes from live provider catalogues.

`free_model_filter.py` combines free-route discovery, Artificial Analysis (AA)
benchmarks, StealthMark proxy ratings, and `models.dev` capability metadata. It
is strict about free-ness: a route is listed only when the provider publishes a
zero price for every billable dimension.

## Quick start

```bash
# 1. Offline self-test (no key or network required)
python3 free_model_filter.py --self-test

# 2. Provide the Artificial Analysis key
export AA_API_KEY="..."

# 3. Build the report
python3 free_model_filter.py
```

With `uv` (export the key first, or pass `--aa-api-key`):

```bash
uv run --script free_model_filter.py
```

If you keep a local `.env`, pass it explicitly:

```bash
uv run --script --env-file .env free_model_filter.py
```

If `AA_API_KEY` is missing, an interactive run prompts for it. Non-interactive
runs exit with a clear error.

## Requirements

- Python 3.10+
- PyYAML (installed automatically by `uv`; otherwise `pip install pyyaml`)
- An Artificial Analysis API key (`AA_API_KEY`)

## Usage

```bash
# Default: all sources, verified zero-priced routes, intelligence floor 25
python3 free_model_filter.py

# One discovery source
python3 free_model_filter.py --source openrouter
python3 free_model_filter.py --source opencode

# Filter by provider/host substring
python3 free_model_filter.py --provider deepseek

# Raise the intelligence floor
python3 free_model_filter.py --min-intelligence 30

# Include free models with no score at all
python3 free_model_filter.py --include-unscored

# Include unverified F24 free-tier and stale mirror routes (labelled)
python3 free_model_filter.py --include-free-tier

# List every AA-scored free model, disabling quality floors
python3 free_model_filter.py --all
```

## GitHub Pages

A scheduled GitHub Action rebuilds the report every six hours and deploys the
ranking to:

https://luandro.github.io/free-model-check/

The workflow requires the `AA_API_KEY` repository secret and GitHub Pages set to
**GitHub Actions** as the source. It runs `free_model_filter.py
--include-unscored` so the page can show both AA-scored and unscored free models
in separate sections.

If Artificial Analysis rate-limits a run (HTTP 429), the workflow deploys the
last known good report from `site/data/` instead of publishing an empty page.
Those three files are the only generated artifacts tracked in git; the root-level
`free_models*.json` and `free_models*.csv` reports stay ignored.

The page itself is a printed register rather than a dashboard: slate paper, two
rule weights, tabular figures, no cards and no colour used as decoration. It is
checked against the Hallmark gates (Almanac theme) and lints clean.

## Configuration

| Flag | Default | Purpose |
|---|---:|---|
| `--min-intelligence` | `25` | Hide scored models below this intelligence value. |
| `--min-specialized` | `25` | Floor for `max(Coding, Agentic)` when AA serves those indices. |
| `--source` | `all` | `all`, `f24`, `openrouter`, or `opencode`. |
| `--include-unscored` | off | Show free models with no score in the unbenchmarked section. |
| `--include-free-tier` | off | Include F24 free-tier and stale mirror routes; they stay labelled. |
| `--include-useful` | off | Include the lower `useful` tier. |
| `--require-agent-ready` | off | Require tool calling and a minimum context. |
| `--min-context` | `65536` | Context required for agent-readiness. |
| `--sort-by` | `score` | `score`, `coding`, `intelligence`, or `agentic`. |
| `--all` | off | Disable quality floors and list every AA-scored free model. |

## Output

All outputs use the `--output-prefix` prefix (default `free_models`) and are
gitignored.

| File | Contents |
|---|---|
| `free_models.json` / `.csv` | Selected AA-scored models and their free routes. |
| `free_models_stealthmark.json` / `.csv` | Free routes scored by StealthMark where AA has no entry. |
| `free_models_unbenchmarked.json` / `.csv` | Free, agent-ready models with no score; populated with `--include-unscored`. |
| `free_models_diagnostics.json` | AA-matched models rejected by quality or readiness gates. |
| `free_models_unmatched.json` | Free routes not confidently matched to AA. |

## How free-ness is decided

| Level | Default | Meaning |
|---|---|---|
| `zero-priced` | included | The provider publishes `0` for every required and remaining billable field: tokens, requests, images, audio, cache, and context tiers. |
| `free-tier` | `--include-free-tier` | F24 SALES scanner assertion using your own provider key; usage may bill at list price. |
| `mirror-listed` | `--include-free-tier` | A stale third-party mirror claims zero cost that the provider does not confirm. |

Pricing is parsed with `Decimal` so a tiny published price such as `1e-999`
cannot underflow to `0.0`. Absent or empty pricing objects are rejected. OpenRouter
routes must also declare text-only output modalities and must not mention prose
prices.

## Intelligence floor

The default floor is **25**. AA-scored and StealthMark-scored models below it are
hidden. Models with no score at all are hidden unless `--include-unscored` is
passed. `--include-useful` and `--all` opt into a lower tier. The floor is
printed at the top of the console report and stored in
`summary.intelligence_floor`.

## StealthMark proxy

StealthMark measures visual-SVG generation and planning games, not coding. Its
`General` score is used only as an intelligence-shaped proxy for free models that
AA does not list. StealthMark results are kept in a separate output and console
section, are labelled `stealthmark-proxy`, never override an AA score, and reject
partial runs.

## Development

```bash
python3 -m py_compile free_model_filter.py
python3 free_model_filter.py --self-test
ruff check free_model_filter.py   # if ruff is installed
```

## Security

`.env`, API keys, private keys, and generated reports are gitignored. The AA key
is sent only to the Artificial Analysis API. Never commit `.env` or a key.

## Data sources

- Artificial Analysis: https://artificialanalysis.ai/
- OpenRouter: https://openrouter.ai/
- OpenCode Zen: https://opencode.ai/
- F24 SALES: https://www.f24-sales.com/
- models.dev: https://models.dev/
- StealthMark: https://stealthmodels.com/

## Contributing

Open an issue or pull request with a focused change. Run the self-test before
submitting.

## License

No license is currently specified.
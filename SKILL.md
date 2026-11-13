---
name: free-model-check
description: Discover and shortlist verified zero-priced LLM routes ranked by Artificial Analysis benchmarks, StealthMark proxy scores, and models.dev agent-readiness metadata.
---

# Free Model Check (`free_model_filter.py`)

Build a live shortlist of strong, genuinely free LLM routes by combining provider pricing feeds (OpenRouter, OpenCode Zen, F24 SALES LiteLLM), Artificial Analysis (AA) benchmarks, StealthMark proxy ratings, and `models.dev` capability metadata.

## When to Use This Skill

Use this skill when you need to:
- Find currently free, benchmark-strong LLM routes for coding, chat, or agentic tool use.
- Audit whether a claimed "free" model route is genuinely zero-priced by the provider versus an unverified scanner claim or stale mirror.
- Evaluate newly released or stealth free models (`stealth/*`, `*alpha*`) that are not yet indexed by Artificial Analysis.

## Prerequisites

- **Python 3.10+** with `pyyaml` (`pip install pyyaml`, or run via `uv`).
- **`AA_API_KEY`**: Artificial Analysis API key provided via environment variable (`export AA_API_KEY="..."`), `--aa-api-key`, or an optional local `.env` file passed explicitly with `uv run --script --env-file .env free_model_filter.py`. The script does not auto-load `.env`; it is gitignored. If the key is missing, an interactive run prompts for it; a non-interactive run exits with an error.
- **Secret hygiene**: `.env` is gitignored. Never read, print, or commit `.env` or API keys.

## Commands

```bash
# Offline self-test (no network or AA_API_KEY required)
python3 free_model_filter.py --self-test

# Default run: verified zero-priced routes across all sources, intelligence floor 25
python3 free_model_filter.py

# Filter by discovery source (all | f24 | openrouter | opencode) or provider substring
python3 free_model_filter.py --source openrouter
python3 free_model_filter.py --provider deepseek

# Require verified agent-readiness (tool calling + >=65,536 context) for selection
python3 free_model_filter.py --require-agent-ready

# Change the intelligence floor (default 25) or include the lower "useful" tier (22)
python3 free_model_filter.py --min-intelligence 30
python3 free_model_filter.py --include-useful

# Include unscored agent-ready models in the unbenchmarked section
python3 free_model_filter.py --include-unscored

# Include unverified F24 free-tier and stale mirror-listed routes (kept labelled)
python3 free_model_filter.py --include-free-tier

# List every AA-scored free model regardless of quality floors
python3 free_model_filter.py --all
```

## Intelligence Floor (`--min-intelligence`, default `25`)

- **Default `25.0`**: Stored in `summary.intelligence_floor` and printed at the top of the console report.
- **Scored models**: Any AA-scored or StealthMark-scored model below `--min-intelligence` is hidden (unless `--include-useful` lowers the cutoff to `--useful-intelligence` `22.0`, or `--all` sets the floor to `0`).
- **Unscored models**: Models with no AA score and no StealthMark score are hidden by default. Pass `--include-unscored` to list unscored, agent-ready chat models in the unbenchmarked section; routes with a known StealthMark proxy score below the floor remain hidden even with `--include-unscored`.
- **AA Free-Tier Inheritance**: AA's free API exposes `artificial_analysis_intelligence_index` (coding and agentic indices are Pro-only and ignored when absent). If a canonical AA slug has no score of its own, the script inherits the highest score from same-family snapshots/reasoning variants and sets `score_source: "inherited"` with `score_inherited_from`.
- **Adaptive percentiles**: `--intel-percentile` and `--specialized-percentile` are opt-in (off by default) so small candidate pools do not drop strong models at the median.

## Free-ness Levels (`verification`)

Only `zero-priced` routes are included by default:

| Level | `verified_free` | Console Marker | Meaning | Included by Default |
|---|---|---|---|---|
| `zero-priced` | `true` | *(none)* | Provider publishes explicit `0` across required token fields and every other billable field in the pricing tree (requests, images, audio, cache, context tiers/overrides). Parsed as `Decimal` so tiny floats like `1e-999` cannot underflow to `0.0`. Absent or empty pricing objects fail. OpenRouter entries must also declare text-only `output_modalities` and contain no price mentions in `description`. | **Yes** |
| `free-tier` | `false` (`requires_provider_key: true`) | `*` | F24 SALES LiteLLM scanner assertion pointing at vendor endpoints with your own provider key; usage may bill at list price. | No (`--include-free-tier`) |
| `mirror-listed` | `false` (`stale_mirror: true`) | `†` | Third-party mirror (`models.dev`) claims zero cost unconfirmed by the live provider catalogue. | No (`--include-free-tier`) |

## Output Files

All files use `--output-prefix` (default `free_models`) and are gitignored:

- `free_models.json` / `free_models.csv` — Selected AA-scored models and their free routes, ranked by `--sort-by` (`score`, `coding`, `intelligence`, or `agentic`).
- `free_models_stealthmark.json` / `free_models_stealthmark.csv` — Free routes scored by StealthMark (`stealthmodels.com`) for models that have no AA entry.
- `free_models_published.json` / `free_models_published.csv` — Free routes with an llm-stats published composite (`published-composite`) and no AA or StealthMark score.
- `free_models_unbenchmarked.json` / `free_models_unbenchmarked.csv` — Free, agent-ready chat models with no AA or StealthMark score (populated when `--include-unscored` is set; stealth models sorted first, legacy models last).
- `free_models_diagnostics.json` — AA-matched models rejected by the quality or agent-readiness gates, with `reasons_not_selected`.
- `free_models_unmatched.json` — Free routes that did not match any AA model above `--match-threshold` (`0.82`).

## How to Interpret the Report

1. **Selected free models (AA section)**:
   - `Score`: Weighted capability score (`0.30 * Intel + 0.35 * Agent + 0.25 * Code`, normalized over available indices; equals `Intel` on the AA free tier).
   - `Ready`: Agent readiness from `models.dev` / feed metadata (`yes` = tool calling supported and context >= `--min-context` `65536`; `no` = failed check; `?` = metadata incomplete).
   - `score_source` in JSON/CSV: `"own"` (direct AA score) or `"inherited"` (from a sibling snapshot in `score_inherited_from`).
2. **StealthMark-scored free models (separate proxy section)**:
   - Uses StealthMark `General` (`0–100`) as an intelligence-shaped proxy (`score_source: "stealthmark-proxy"`). StealthMark evaluates visual-SVG generation and planning games, **not** coding.
   - **Never overrides AA**: Any model identity that matches AA is excluded from StealthMark selection.
   - **Completeness gate**: Partial runs (`countedResults != prompts`, `partial: true`, or non-empty `missingFamilies`) are rejected.
3. **Published composite (llm-stats section)**:
   - Uses the llm-stats composite (`0–100`, relative ranking) as published evidence (`score_source: "published-composite"`). It is a **different scale** from the AA intelligence index and is never written into the AA columns.
   - **Calibrated placement**: a least-squares fit (`Calibration`, `fit_calibration`) maps the composite onto the AA index using only canonical AA rows, excluding effort variants and dated snapshots (counted in `summary.calibration`). A model whose **lower 80% prediction bound** clears `--min-intelligence` joins the register as `score_source: "calibrated-llmstats"` with `quality.calibration` carrying the composite, estimate, band and fit. A model that misses it keeps `published-composite` and gains only `calibration_preview`, so no calibrated number is ever written onto an unplaced model.
   - **Never overrides AA or StealthMark**: lane order is AA, then StealthMark, then llm-stats; a route appears in exactly one lane.
   - **Rank intervals**: `assign_rank_intervals` simulates 4000 draws over each row's score posterior and reports a median rank with an 80% interval (`quality.rank_interval`, `summary.ranking`). A calibrated row can span several places; a measured row is treated as exact, which is optimistic because AA publishes no per-model variance.
   - **Coding and Agentic**: `fill_specialized_from_llmstats` fills these from llm-stats where AA publishes none, marked `specialized_source: "llm-stats"`, and never feeds `capability_score`.
   - **Separate floor**: `--min-published` (default `0`) for the section itself; the calibrated promotion uses `--min-intelligence` against the lower prediction bound.
4. **Free but unbenchmarked**:
   - Lists text chat models confirmed agent-ready (`tool_call: true` and context >= `65536`) that lack AA, StealthMark and llm-stats scores (`no AA entry` or `AA unscored`), only when `--include-unscored` is enabled.

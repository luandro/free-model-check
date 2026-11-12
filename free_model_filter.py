#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pyyaml",
# ]
# ///
"""
free_model_filter.py

Build a live shortlist of strong free LLM routes by combining:
  1) live free-route feeds (F24 SALES LiteLLM YAML, OpenRouter zero-priced
     models, the OpenCode provider catalogue)
  2) Artificial Analysis benchmark scores
  3) models.dev capability metadata (context, tool calling, reasoning, modalities)

The script keeps "model quality" separate from "route availability". A model can
be benchmark-strong while one or more of its free routes may be provider-specific.
Free routes are gathered from every configured source by default (--source all);
Artificial Analysis decides whether the underlying model is strong enough;
models.dev enriches the result.

What counts as free:
    "zero-priced"  The provider itself publishes a price of zero for every
                   billable dimension. OpenRouter pricing and the OpenCode
                   catalogue (https://opencode.ai/zen/v1/models) are checked
                   field by field, including per request, image, audio, cache
                   and context-tier prices, so a model with zero token prices but
                   a per-song or long-context fee is rejected. The fields a
                   source bills on must be published explicitly as zeros (an
                   absent pricing object is not proof), and every other amount in
                   the object must be zero too. Only these routes are listed by
                   default.
    "free-tier"    The F24 SALES feed maps to vendor endpoints such as
                   generativelanguage.googleapis.com or api.river.ai using the
                   operator's own key. Its "free" claim is a scanner assertion,
                   not published pricing, and some of those providers are paid
                   products.
    "mirror-listed" A stale third-party mirror (models.dev) claims zero cost that
                   the provider does not confirm.
    The last two are excluded unless --include-free-tier is passed, and stay
    labelled when included (requires_provider_key / stale_mirror).

    OpenCode prices come from the provider catalogue, not models.dev, because
    models.dev lags: it listed 47 zero-cost OpenCode models while the provider
    published 10, and kept a zero-cost id for a model the provider now sells at
    $1.25/$4.25 per million tokens.

Intelligence floor:
    --min-intelligence (default 25) hides any model with a known Artificial
    Analysis or StealthMark score below it. Models with no score at all are
    hidden from the unbenchmarked section unless --include-unscored is passed:
    no score is not evidence that the floor was met. The floor is printed in
    the console report and stored in summary.intelligence_floor.

Artificial Analysis free-tier caveat:
    The free tier exposes artificial_analysis_intelligence_index only, and only
    for part of the catalogue. Coding and agentic indices are Pro-tier. When an
    AA entry has no score of its own, the script inherits the best score from
    same-family variants (for example `deepseek-v4-1-flash` inherits from
    `deepseek-v4-1-flash-1017`) and marks the record as inherited. When coding
    and agentic are absent the quality gate falls back to intelligence alone.

    The adaptive cutoff is opt-in (`--intel-percentile`, off by default). With only
    a few verified-free scored routes a percentile is a moving target that drops
    good models: a median over three candidates removes the third. Selection
    relies on the absolute `--min-intelligence` floor instead.

Unbenchmarked free models:
    Stealth releases (Pulse Alpha, Crouton Alpha, Ox Alpha) and many newly free
    routes have no AA entry at all, so a benchmark gate can never select them.
    They are listed separately in free_models_unbenchmarked.json when they are
    agent-ready (tool calling + context), with stealth models first.

StealthMark proxy:
    StealthMark (stealthmodels.com) scores visual-SVG generation and planning
    games; it has no coding benchmark. Its General score is used only as an
    intelligence-shaped proxy for free routes AA does not list, kept in a
    separate output and console section and labelled "stealthmark-proxy".
    Partial runs are rejected, and matching is alias-only, so a StealthMark id
    never overrides an AA score for a model AA already covers.

Published composite (llm-stats):
    llm-stats.com publishes one composite score built from ranked benchmark
    evidence, with per-row provenance. It runs on its own scale, which sits well
    above the AA intelligence index, so its value is never written into the AA
    columns and never compared with --min-intelligence. Free routes AA has not
    indexed and StealthMark does not score are listed in a third section,
    labelled "published-composite", with the scale and the provider-reported
    caveat shown. --min-published (default 0) is a separate floor for it.

Requirements:
    Python 3.10+
    pip install pyyaml

Artificial Analysis key:
    export AA_API_KEY="..."

Typical use:
    python free_model_filter.py

Outputs by default:
    free_models.json          # selected strong models, grouped by underlying model
    free_models.csv           # one row per selected free route
    free_models_unmatched.json# free routes we could not confidently match to AA
    free_models_diagnostics.json
                              # AA-matched models rejected by quality/readiness gates
    free_models_unbenchmarked.json
                              # free, agent-ready models with no AA score
                              # (hidden unless --include-unscored)
    free_models_unbenchmarked.csv
                              # one row per unscored free route
    free_models_stealthmark.json
                              # free routes StealthMark can score and AA does not
    free_models_stealthmark.csv
                              # one row per StealthMark-scored free route
    free_models_published.json
                              # free routes with an llm-stats published composite
                              # and no AA or StealthMark score
    free_models_published.csv
                              # one row per published-composite free route

Useful examples:
    # Use the absolute floors (adaptive percentiles are opt-in)
    python free_model_filter.py

    # Only look at OpenCode Zen free models
    python free_model_filter.py --source opencode

    # Loosen thresholds
    python free_model_filter.py --min-intelligence 22 --min-specialized 20 --no-adaptive

    # Show free models with no score at all
    python free_model_filter.py --include-unscored

    # Require Hermes-friendly metadata when deciding selection
    python free_model_filter.py --require-agent-ready

    # Use a downloaded F24 YAML file instead of the remote URL
    python free_model_filter.py --f24-file litellm-config.yaml

Data source attribution:
    Artificial Analysis: https://artificialanalysis.ai/
    StealthMark:         https://stealthmodels.com/
    models.dev:          https://models.dev/
    F24 SALES:           https://www.f24-sales.com/
"""

from __future__ import annotations

import argparse
import csv
import difflib
import getpass
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None


DEFAULT_F24_URL = "https://www.f24-sales.com/litellm-config.yaml"
DEFAULT_AA_URL = "https://artificialanalysis.ai/api/v2/language/models/free"
DEFAULT_MODELS_DEV_URL = "https://models.dev/models.json"
DEFAULT_OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
DEFAULT_OPENCODE_URL = "https://opencode.ai/zen/v1/models"
# stealthmodels.com publishes StealthMark ratings as a public JSON feed. Stealth and
# newly revealed models (e.g. Crouton Alpha) have no Artificial Analysis entry, so
# without an external benchmark they would otherwise never pass the quality gate.
DEFAULT_STEALTH_URL = "https://stealthmodels.com/data/v1/benchmarks-page.json"
# Hard intelligence floor for every scored model in the report. Models with a
# known score below this are hidden; override per run with --min-intelligence.
DEFAULT_MIN_INTELLIGENCE = 25.0
USER_AGENT = "free-model-filter/1.0 (+https://artificialanalysis.ai/)"

# Words that often identify the serving gateway rather than the underlying model.
PROVIDER_PREFIXES = {
    "openrouter",
    "groq",
    "nvidia",
    "nvidia_nim",
    "cerebras",
    "huggingface",
    "hugging_face",
    "opencode",
    "opencode_zen",
    "kilo",
    "kilocode",
    "cloudflare",
    "mistral",
    "cohere",
    "google",
    "gemini",
    "github",
    "github_models",
    "ovhcloud",
    "llm7",
    "llm7_io",
    "hetzner",
    "together_ai",
    "fireworks_ai",
    "deepinfra",
    "sambanova",
    "perplexity",
    "azure",
    "bedrock",
    "vertex_ai",
}

GENERIC_ROUTE_WORDS = {
    "free",
    "latest",
    "chat",
    "instruct",
    "model",
}


@dataclass
class FreeRoute:
    provider: str
    route_model: str
    model_name: str
    api_base: str | None
    aliases: list[str]
    raw_index: int
    source: str = "f24"
    metadata: dict[str, Any] | None = None
    # "zero-priced": the provider publishes a price of zero for every billable
    #   dimension, so the route is free without an account tier.
    # "free-tier": a third-party scanner asserts a provider free tier; the route
    #   uses the operator's own key and may bill at list price.
    # "mirror-listed": a stale third-party mirror (models.dev) claims zero cost,
    #   which the provider does not confirm.
    verification: str = "zero-priced"


@dataclass
class MatchResult:
    item: dict[str, Any] | None
    confidence: float
    method: str
    matched_alias: str | None = None
    matched_target: str | None = None


def eprint(*args: Any, **kwargs: Any) -> None:
    print(*args, file=sys.stderr, **kwargs)


def http_get_bytes(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> bytes:
    req_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} fetching {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error fetching {url}: {exc}") from exc


def http_get_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    *,
    exact_numbers: bool = False,
) -> Any:
    raw = http_get_bytes(url, headers=headers, timeout=timeout)
    try:
        # exact_numbers keeps JSON fractions as Decimal so a published price like
        # 1e-999 cannot underflow to 0.0 while decoding and pass as free.
        return json.loads(raw.decode("utf-8"), parse_float=Decimal if exact_numbers else float)
    except Exception as exc:
        raise RuntimeError(f"Expected JSON from {url}, got invalid JSON") from exc


def load_yaml_bytes(raw: bytes, source: str) -> Any:
    if yaml is None:
        raise RuntimeError(
            "PyYAML is required to read the F24 LiteLLM feed. Install it with: pip install pyyaml"
        )
    try:
        return yaml.safe_load(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Could not parse YAML from {source}: {exc}") from exc


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_text(value: str) -> str:
    """Aggressive comparison normalization, not a canonical public ID."""
    s = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = re.sub(r"\([^)]*\b(?:high|medium|low|thinking|reasoning)\b[^)]*\)", "", s)
    s = re.sub(r":free\b", "", s)
    s = re.sub(r"(?:^|[-_:/])free(?:$|[-_:/])", "-", s)
    s = re.sub(r"\blatest\b", "", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def strip_route_decorations(value: str) -> str:
    s = value.strip()
    s = re.sub(r":free\b", "", s, flags=re.I)
    s = re.sub(r"[-_]?free$", "", s, flags=re.I)
    return s.strip(" /:-_")


def alias_variants(*values: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def add(v: str) -> None:
        v = strip_route_decorations(v)
        if not v:
            return
        n = normalize_text(v)
        if len(n) < 4 or n in seen:
            return
        seen.add(n)
        out.append(v)

    for raw in values:
        raw = clean_text(raw)
        if not raw:
            continue
        add(raw)

        parts = [p for p in re.split(r"[/]", raw) if p]
        if len(parts) >= 2:
            # Drop serving gateway prefix.
            if normalize_text(parts[0]) in {normalize_text(p) for p in PROVIDER_PREFIXES}:
                add("/".join(parts[1:]))
            add(parts[-1])
            add("/".join(parts[-2:]))

        # Useful when provider and model are separated by a colon.
        colon_parts = [p for p in raw.split(":") if p]
        if len(colon_parts) > 1 and colon_parts[-1].lower() != "free":
            add(colon_parts[-1])

    return out


def infer_provider(params: dict[str, Any], route_model: str) -> str:
    for key in ("custom_llm_provider", "provider"):
        if params.get(key):
            return clean_text(params[key])
    first = route_model.split("/", 1)[0] if "/" in route_model else "unknown"
    return first or "unknown"


def extract_f24_routes(doc: Any) -> list[FreeRoute]:
    if isinstance(doc, dict):
        entries = doc.get("model_list")
        if entries is None and isinstance(doc.get("models"), list):
            entries = doc.get("models")
    elif isinstance(doc, list):
        entries = doc
    else:
        entries = None

    if not isinstance(entries, list):
        raise RuntimeError("F24 YAML did not contain a LiteLLM-style model_list array")

    routes: list[FreeRoute] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        params = entry.get("litellm_params") or {}
        if not isinstance(params, dict):
            params = {}

        route_model = clean_text(params.get("model") or entry.get("model") or entry.get("id"))
        model_name = clean_text(entry.get("model_name") or entry.get("name") or route_model)
        if not route_model:
            continue

        provider = infer_provider(params, route_model)
        api_base = clean_text(params.get("api_base")) or None

        extra_aliases: list[str] = []
        model_info = entry.get("model_info")
        if isinstance(model_info, dict):
            for key in (
                "canonical_model",
                "canonical_model_id",
                "base_model",
                "model_id",
                "display_name",
            ):
                if model_info.get(key):
                    extra_aliases.append(clean_text(model_info[key]))

        aliases = alias_variants(route_model, model_name, *extra_aliases)
        routes.append(
            FreeRoute(
                provider=provider,
                route_model=route_model,
                model_name=model_name,
                api_base=api_base,
                aliases=aliases,
                raw_index=idx,
                source="f24",
                # The feed maps to vendor endpoints with the operator's own key;
                # its "free" claim is a scanner assertion, not published pricing.
                verification="free-tier",
            )
        )

    # Exact de-duplication only. Multiple providers for one model are intentional,
    # and one provider may serve the same model id from two different endpoints.
    unique: dict[tuple[str, str, str], FreeRoute] = {}
    for route in routes:
        unique[(route.provider.lower(), route.route_model, (route.api_base or "").lower())] = route
    return list(unique.values())


def fetch_f24(args: argparse.Namespace) -> list[FreeRoute]:
    if args.f24_file:
        p = Path(args.f24_file)
        raw = p.read_bytes()
        doc = load_yaml_bytes(raw, str(p))
    else:
        raw = http_get_bytes(args.f24_url)
        doc = load_yaml_bytes(raw, args.f24_url)
    return extract_f24_routes(doc)


def price_zero(value: Any) -> bool:
    """True only when a price is an explicit zero.

    Decimal, not float: float("1e-999") underflows to 0.0, which would turn a
    published nonzero price into a free claim.
    """
    if isinstance(value, bool) or value is None:
        return False
    try:
        return Decimal(str(value).strip()) == 0
    except (ArithmeticError, ValueError):
        return False


# Scalar keys that describe a threshold, not money: `tier: {type, size}` in
# models.dev and `when: {context: {gt}}` in the OpenCode catalogue. Only scalars
# are skipped; a dict or list under the same key is still inspected, so a price
# cannot hide behind a threshold-shaped wrapper.
PRICE_THRESHOLD_SCALARS = {
    "type",
    "size",
    "gt",
    "min_prompt_tokens",
    "min_completion_tokens",
}


def _pricing_path(mapping: Any, path: tuple[str, ...]) -> tuple[Any, bool]:
    current = mapping
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None, False
        current = current[key]
    return current, True


def _all_amounts_zero(mapping: Any) -> bool:
    if isinstance(mapping, dict):
        for key, value in mapping.items():
            if key in PRICE_THRESHOLD_SCALARS and not isinstance(value, (dict, list)):
                continue
            if isinstance(value, (dict, list)):
                if not _all_amounts_zero(value):
                    return False
            elif value is None or value == "":
                # Not offered (no cache write, no image price): not a charge.
                continue
            elif isinstance(value, bool) or not price_zero(value):
                # A boolean is not a price; treat anything unparseable as billed.
                return False
        return True
    if isinstance(mapping, list):
        return all(_all_amounts_zero(value) for value in mapping)
    if mapping is None or mapping == "":
        return True
    return price_zero(mapping)


def prices_all_zero(mapping: Any, required: tuple[tuple[str, ...], ...] = ()) -> bool:
    """True only when pricing data exists and every amount in it is zero.

    An absent or empty pricing object is not proof of a free model, and a
    partial object must not pass just because the only field it happens to
    carry is zero. `required` names the fields the caller needs published
    explicitly (OpenRouter prompt/completion, OpenCode text input/output price);
    every other amount in the object is then inspected too, so a provider that
    charges per request, image, song, cache write or long-context tier fails.
    """
    if not isinstance(mapping, (dict, list)) or not mapping:
        return False
    for path in required:
        value, present = _pricing_path(mapping, path)
        if not present or not price_zero(value):
            return False
    return _all_amounts_zero(mapping)


def route_free_fields(route: FreeRoute) -> dict[str, Any]:
    """Provenance fields describing how strong the "free" claim is."""
    return {
        "verification": route.verification,
        "verified_free": route.verification == "zero-priced",
        # zero-priced routes still need a marketplace account key, but the
        # provider's published price is zero; free-tier routes use a provider
        # key whose usage may bill at list price.
        "requires_provider_key": route.verification == "free-tier",
        "stale_mirror": route.verification == "mirror-listed",
    }


def _openrouter_metadata(entry: dict[str, Any], rid: str, name: str) -> dict[str, Any]:
    """Normalize an OpenRouter model entry to the models.dev metadata shape."""
    architecture = entry.get("architecture") if isinstance(entry.get("architecture"), dict) else {}
    params = entry.get("supported_parameters")
    params = params if isinstance(params, list) else []
    output_modalities = architecture.get("output_modalities")
    input_modalities = architecture.get("input_modalities")
    created = entry.get("created")
    release_date = (
        time.strftime("%Y-%m-%d", time.gmtime(float(created)))
        if isinstance(created, (int, float, Decimal)) and not isinstance(created, bool)
        else None
    )
    return {
        "id": rid,
        "name": name,
        "description": clean_text(entry.get("description")),
        "limit": {"context": entry.get("context_length") or (entry.get("top_provider") or {}).get("context_length")},
        "tool_call": "tools" in params,
        "reasoning": "reasoning" in params,
        "release_date": release_date,
        "modalities": {
            "input": input_modalities if isinstance(input_modalities, list) else None,
            "output": output_modalities if isinstance(output_modalities, list) else None,
        },
    }


# OpenRouter's pricing object is per token only. Media and per-request models
# publish zero token prices and state their real fee in prose, so a zero pricing
# object is not proof that the model is free.
PRICE_MENTION_RE = re.compile(
    r"(?:[$€£]\s?\d|\b(?:usd|eur|gbp)\s?\d|\d+(?:\.\d+)?\s*(?:cents?|¢)|\bpriced (?:at|per)\b)",
    re.I,
)


def _openrouter_free_rejection(entry: dict[str, Any]) -> str | None:
    """Reason a zero-priced OpenRouter entry cannot be trusted as free, if any."""
    architecture = entry.get("architecture") if isinstance(entry.get("architecture"), dict) else {}
    outputs = architecture.get("output_modalities")
    if not isinstance(outputs, list) or not outputs:
        # Without a declared output modality the pricing we verified may cover
        # only tokens while the model bills per image, song or request.
        return "no declared output modality; billing coverage unknown"
    declared = {str(modality).strip().lower() for modality in outputs}
    extra = sorted(declared - {"text"})
    if extra:
        return f"non-text output ({', '.join(extra)}); token pricing excludes media fees"
    description = clean_text(entry.get("description")) or ""
    if PRICE_MENTION_RE.search(description):
        return "description states a price the pricing object omits"
    return None


def fetch_openrouter_routes(base_url: str) -> list[FreeRoute]:
    doc = http_get_json(base_url, headers={"Accept": "application/json"}, exact_numbers=True)
    entries = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(entries, list):
        raise RuntimeError("Unexpected OpenRouter models response shape")
    routes: list[FreeRoute] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pricing = entry.get("pricing")
        if not isinstance(pricing, dict):
            continue
        # OpenRouter bills per token for text; prompt and completion must both be
        # published as explicit zeros, and every other amount must be zero too.
        if not prices_all_zero(pricing, required=(("prompt",), ("completion",))):
            continue
        if _openrouter_free_rejection(entry):
            continue
        rid = clean_text(entry.get("id"))
        if not rid:
            continue
        name = clean_text(entry.get("name"))
        routes.append(
            FreeRoute(
                provider="openrouter",
                route_model=rid,
                model_name=name or rid,
                api_base=None,
                aliases=alias_variants(rid, name),
                raw_index=len(routes),
                source="openrouter",
                metadata=_openrouter_metadata(entry, rid, name or rid),
            )
        )
    return routes


def _opencode_metadata(entry: dict[str, Any], model_id: str) -> dict[str, Any]:
    """Normalize an OpenCode catalogue entry to the models.dev metadata shape."""
    capabilities = entry.get("capabilities") if isinstance(entry.get("capabilities"), dict) else {}
    tools = capabilities.get("tools") if isinstance(capabilities.get("tools"), dict) else {}
    reasoning = capabilities.get("reasoning") if isinstance(capabilities.get("reasoning"), dict) else {}
    return {
        "id": model_id,
        "name": clean_text(entry.get("name")) or model_id,
        "description": clean_text(entry.get("description")),
        "limit": entry.get("limit") if isinstance(entry.get("limit"), dict) else None,
        "tool_call": tools.get("supported") if isinstance(tools.get("supported"), bool) else entry.get("tool_call"),
        "reasoning": reasoning.get("supported")
        if isinstance(reasoning.get("supported"), bool)
        else entry.get("reasoning"),
        "modalities": entry.get("modalities") if isinstance(entry.get("modalities"), dict) else None,
        "release_date": clean_text(entry.get("release_date")) or None,
    }


def fetch_opencode_routes(base_url: str, provider_id: str = "opencode") -> list[FreeRoute]:
    """Zero-priced OpenCode routes from the provider's own catalogue.

    models.dev mirrors OpenCode's catalogue and drifts behind it. At the time of
    writing it still listed `muse-spark-1.3-contributor-free` at zero cost while
    the provider served `muse-spark-1.3` at $1.25/$4.25 per million tokens and no
    longer served the contributor id at all, and 37 of its 47 "free" entries were
    absent from the provider catalogue. Pricing therefore has to come from
    https://opencode.ai/zen/v1/models, whose entries carry an explicit cost tree
    per token, cache read and cache write.

    A models.dev document is still accepted for explicit --source-url overrides, but
    its entries are marked "mirror-listed" because a mirror cannot confirm what the
    provider charges today.
    """
    doc = http_get_json(base_url, headers={"Accept": "application/json"}, exact_numbers=True)
    entries = doc.get("data") if isinstance(doc, dict) else None
    mirrored = False
    if not isinstance(entries, list):
        prov = doc.get(provider_id) if isinstance(doc, dict) else None
        model_map = prov.get("models") if isinstance(prov, dict) else None
        if not isinstance(model_map, dict):
            raise RuntimeError("Unexpected OpenCode models response shape")
        entries = [dict(v, id=k) for k, v in model_map.items() if isinstance(v, dict)]
        mirrored = True

    routes: list[FreeRoute] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cost = entry.get("cost")
        if not isinstance(cost, dict):
            continue
        # The provider publishes the per-token prices under cost.text; the mirror
        # flattens them to cost.input/output. Either way both must be explicit
        # zeros before anything else in the cost tree is inspected.
        required: tuple[tuple[str, ...], ...] = (
            (("text", "input", "price"), ("text", "output", "price"))
            if isinstance(cost.get("text"), dict)
            else (("input",), ("output",))
        )
        if not prices_all_zero(cost, required=required):
            continue
        model_id = clean_text(entry.get("id"))
        if not model_id:
            continue
        name = clean_text(entry.get("name"))
        routes.append(
            FreeRoute(
                provider=provider_id,
                route_model=model_id,
                model_name=name or model_id,
                api_base=None,
                aliases=alias_variants(model_id, name),
                raw_index=len(routes),
                source="opencode",
                metadata=_opencode_metadata(entry, model_id),
                verification="mirror-listed" if mirrored else "zero-priced",
            )
        )
    return routes


def filter_routes(routes: list[FreeRoute], needle: str) -> list[FreeRoute]:
    """Keep routes whose provider, api_base host, or route id contains the substring."""
    n = needle.lower()
    return [
        r
        for r in routes
        if n in r.provider.lower()
        or n in (r.api_base or "").lower()
        or n in r.route_model.lower()
    ]


def fetch_aa_models(
    api_key: str,
    base_url: str,
    verbose: bool = False,
) -> tuple[list[dict[str, Any]], float | None]:
    page = 1
    rows: list[dict[str, Any]] = []
    index_version: float | None = None

    while True:
        parsed = urllib.parse.urlsplit(base_url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        query["page"] = str(page)
        page_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
        )
        if verbose:
            eprint(f"Fetching Artificial Analysis page {page} ...")
        payload = http_get_json(page_url, headers={"x-api-key": api_key, "Accept": "application/json"})
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise RuntimeError("Unexpected Artificial Analysis response shape")
        rows.extend([r for r in payload["data"] if isinstance(r, dict)])
        if index_version is None:
            iv = payload.get("intelligence_index_version")
            if isinstance(iv, (int, float)):
                index_version = float(iv)

        pagination = payload.get("pagination") or {}
        if not isinstance(pagination, dict) or not pagination.get("has_more"):
            break
        page += 1
        if page > 20:
            raise RuntimeError("Artificial Analysis pagination exceeded 20 pages; aborting defensively")

    return rows, index_version


def aa_aliases(item: dict[str, Any]) -> list[str]:
    creator = item.get("model_creator") or {}
    creator_name = creator.get("name") if isinstance(creator, dict) else ""
    slug = clean_text(item.get("slug"))
    name = clean_text(item.get("name"))
    vals = [slug, name]
    if creator_name:
        vals.extend([f"{creator_name}/{slug}", f"{creator_name} {name}"])
    return alias_variants(*vals)


def extract_parameter_sizes(values: Iterable[str]) -> set[str]:
    sizes: set[str] = set()
    for value in values:
        for m in re.finditer(r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*[bB](?![a-z])", value):
            sizes.add(m.group(1))
    return sizes


def compare_alias_sets(source_aliases: list[str], target_aliases: list[str]) -> tuple[float, str, str | None, str | None]:
    best_score = 0.0
    best_method = "none"
    best_src: str | None = None
    best_tgt: str | None = None

    source_sizes = extract_parameter_sizes(source_aliases)
    target_sizes = extract_parameter_sizes(target_aliases)
    if source_sizes and target_sizes and source_sizes.isdisjoint(target_sizes):
        return 0.0, "parameter-size-mismatch", None, None

    for src in source_aliases:
        ns = normalize_text(src)
        if len(ns) < 4:
            continue
        for tgt in target_aliases:
            nt = normalize_text(tgt)
            if len(nt) < 4:
                continue

            if ns == nt:
                score, method = 1.0, "exact"
            elif min(len(ns), len(nt)) >= 8 and (ns in nt or nt in ns):
                ratio_len = min(len(ns), len(nt)) / max(len(ns), len(nt))
                score = 0.91 + 0.07 * ratio_len
                method = "containment"
            else:
                score = difflib.SequenceMatcher(a=ns, b=nt).ratio()
                method = "fuzzy"

            if score > best_score:
                best_score, best_method, best_src, best_tgt = score, method, src, tgt

    return best_score, best_method, best_src, best_tgt


def match_item(
    source_aliases: list[str],
    candidates: list[dict[str, Any]],
    alias_func,
    min_confidence: float,
    min_margin: float = 0.035,
    preference=None,
) -> MatchResult:
    """Pick the best candidate for a set of aliases.

    ``preference`` (optional) maps a candidate to a tuple used as a deterministic
    tie-breaker when several candidates match at the same score. Without it, the
    winner would depend on feed order, which is how a route can land on a
    score-less benchmark variant instead of the model's canonical entry.
    """
    ranked: list[tuple[float, tuple, str, str | None, str | None, dict[str, Any]]] = []
    for item in candidates:
        targets = alias_func(item)
        score, method, src, tgt = compare_alias_sets(source_aliases, targets)
        if score > 0:
            pref = tuple(preference(item)) if preference else ()
            ranked.append((score, pref, method, src, tgt, item))

    if not ranked:
        return MatchResult(None, 0.0, "none")

    ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0

    if best[0] < min_confidence:
        return MatchResult(None, best[0], f"below-threshold:{best[2]}", best[3], best[4])

    # Exact and containment matches are allowed even with a small margin. Fuzzy
    # matching requires separation from the runner-up to avoid family collisions.
    if best[2] == "fuzzy" and (best[0] - second_score) < min_margin:
        return MatchResult(None, best[0], "ambiguous-fuzzy", best[3], best[4])

    return MatchResult(best[5], best[0], best[2], best[3], best[4])


def extract_eval(item: dict[str, Any], key: str) -> float | None:
    ev = item.get("evaluations") or {}
    if not isinstance(ev, dict):
        return None
    val = ev.get(key)
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    return None


def variant_base_key(item: dict[str, Any]) -> str:
    """Family key that ignores AA reasoning-effort labels and dated snapshots.

    Artificial Analysis lists the same underlying model once per reasoning effort
    and per snapshot, e.g. "DeepSeek V4.1 Flash 1017 (Max)" and
    "DeepSeek V4.1 Flash (Non-reasoning)". Parenthesised labels are dropped and a
    trailing 4-digit snapshot date is stripped, so both collapse to one key.
    """
    name = clean_text(item.get("name")) or clean_text(item.get("slug"))
    name = re.sub(r"\([^)]*\)", " ", name)
    key = normalize_text(name)
    if len(key) > 8:
        key = re.sub(r"\d{4}$", "", key)
    return key


def build_variant_score_index(aa_models: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Best available score per model family, used to fill unscored AA entries.

    The AA free tier only returns intelligence scores and only for part of the
    catalogue: a canonical slug such as ``deepseek-v4-1-flash`` can be null while
    its dated snapshot ``deepseek-v4-1-flash-1017`` scores 32.5. Inheriting the
    family's best score keeps such routes visible; callers mark the record as
    inherited so the number is never presented as the entry's own result.
    """
    index: dict[str, dict[str, Any]] = {}
    for item in aa_models:
        key = variant_base_key(item)
        if not key:
            continue
        entry = index.setdefault(
            key,
            {"intelligence": None, "coding": None, "agentic": None, "intelligence_from": None},
        )
        intel = extract_eval(item, "artificial_analysis_intelligence_index")
        if intel is not None and (entry["intelligence"] is None or intel > entry["intelligence"]):
            entry["intelligence"] = intel
            entry["intelligence_from"] = clean_text(item.get("slug")) or None
        for field, eval_key in (
            ("coding", "artificial_analysis_coding_index"),
            ("agentic", "artificial_analysis_agentic_index"),
        ):
            value = extract_eval(item, eval_key)
            if value is not None and (entry[field] is None or value > entry[field]):
                entry[field] = value

    return {
        key: entry
        for key, entry in index.items()
        if any(entry[f] is not None for f in ("intelligence", "coding", "agentic"))
    }


# ---------------------------------------------------------------------------
# StealthMark (stealthmodels.com) proxy scores.
#
# StealthMark measures visual-SVG generation and multi-step planning games; it has
# no coding benchmark. Crouton Alpha rates 43.5 General / 56.0 Visual, in the
# same family as GLM-5.3-Flash on AA, so its General score is used here only as an
# intelligence-shaped proxy for models that have no AA entry. Every StealthMark
# number is recorded with its source so none of it can be mistaken for AA data.
# ---------------------------------------------------------------------------

STEALTHMARK_BASE = "https://stealthmodels.com"


def _stealthmark_summary(model: dict[str, Any]) -> dict[str, Any] | None:
    """One model's StealthMark scores, or None if the model has no complete run.

    A model is scored only when the default edition has all 8 graded scenes
    (``partial`` is false and ``countedResults`` equals ``prompts``); a partial
    run is a lower bound and must not stand in for a full result.
    """
    summaries = model.get("scoreSummaries")
    if not isinstance(summaries, dict) or not summaries:
        return None
    edition = clean_text(model.get("defaultEdition"))
    summary = summaries.get(edition) if edition and edition in summaries else summaries.get("")
    if not isinstance(summary, dict):
        # A model with several editions has no default; fall back to the one with
        # the most counted results so the most complete run wins.
        full = [
            s
            for s in summaries.values()
            if isinstance(s, dict) and isinstance(s.get("countedResults"), int)
        ]
        if not full:
            return None
        summary = max(full, key=lambda s: (s.get("countedResults") or 0, s.get("prompts") or 0))
    counted = summary.get("countedResults")
    prompts = summary.get("prompts")
    if not isinstance(counted, int) or not isinstance(prompts, int) or prompts <= 0:
        return None
    if counted != prompts:
        return None
    caps = summary.get("capabilities") if isinstance(summary.get("capabilities"), dict) else {}
    general = caps.get("general") if isinstance(caps.get("general"), dict) else {}
    visual = caps.get("visual") if isinstance(caps.get("visual"), dict) else {}
    if general.get("partial") or visual.get("partial"):
        return None
    if general.get("missingFamilies") or visual.get("missingFamilies"):
        return None
    overall = summary.get("score")
    if not isinstance(overall, (int, float)) or not isinstance(general.get("score"), (int, float)):
        return None
    return {
        "id": clean_text(model.get("id")) or None,
        "name": clean_text(model.get("name")) or None,
        "revealed_name": clean_text(model.get("revealedName")) or None,
        "identity_status": clean_text(model.get("identityStatus")) or None,
        "group": clean_text(model.get("group")) or None,
        "overall": float(overall),
        "general": float(general["score"]),
        "visual": float(visual["score"]) if isinstance(visual.get("score"), (int, float)) else None,
        "reasoning": model.get("reasoning") if isinstance(model.get("reasoning"), dict) else None,
        "results_counted": counted,
        "prompts": prompts,
        "edition": edition or None,
        "page_url": clean_text(model.get("url"))
        or f"{STEALTHMARK_BASE}/benchmarks/models/{clean_text(model.get('id'))}",
        "source_url": f"{STEALTHMARK_BASE}/data/v1/benchmarks-page.json",
    }


def parse_stealthmark(doc: Any) -> list[dict[str, Any]]:
    """StealthMark scores, one dict per model with a complete default run."""
    models = doc.get("models") if isinstance(doc, dict) else None
    if not isinstance(models, list):
        return []
    out: list[dict[str, Any]] = []
    for model in models:
        if not isinstance(model, dict):
            continue
        entry = _stealthmark_summary(model)
        if entry is not None:
            out.append(entry)
    return out


def fetch_stealthmark(url: str) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch StealthMark ratings.

    The feed is best-effort: a fetch failure must not break the run, so the error
    is returned for the summary instead of raising.
    """
    try:
        doc = http_get_json(url, headers={"Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001 - best-effort enrichment
        return [], str(exc)
    entries = parse_stealthmark(doc)
    if not entries:
        return [], "StealthMark feed was readable but no complete model scores were recognized"
    return entries, None


def stealthmark_aliases(entry: dict[str, Any]) -> list[str]:
    return alias_variants(entry.get("id"), entry.get("name"), entry.get("revealed_name"))


def match_stealthmark(
    route: FreeRoute, entries: list[dict[str, Any]], min_confidence: float
) -> tuple[dict[str, Any] | None, float, str]:
    """Best StealthMark entry for a route, matched on the feed's own aliases.

    Grouping is done on each route's own identity, not on the union of all
    routes' aliases: a shared group key would let one route's alias claim a score
    that belongs to another model (the union would hand Ox Alpha's GLM score to
    space-bunny-alpha, for example).
    """
    best: dict[str, Any] | None = None
    best_score = 0.0
    best_method = "none"
    for entry in entries:
        score, method, _src, _tgt = compare_alias_sets(route.aliases, stealthmark_aliases(entry))
        if score > best_score:
            best, best_score, best_method = entry, score, method
    if best is None or best_score < min_confidence:
        return None, best_score, f"below-threshold:{best_method}"
    return best, best_score, best_method


def stealthmark_only_routes(
    routes: list[FreeRoute],
    aa_identities: set[str],
    entries: list[dict[str, Any]],
    min_confidence: float,
) -> list[dict[str, Any]]:
    """Routes StealthMark can score but Artificial Analysis cannot.

    Exclusion is by model identity, not by the exact provider/route id: if any
    route for the same model already has an AA entry, a sibling route must not
    create a second, StealthMark-only entry for that model. Within StealthMark
    itself, matching stays per-route on the feed's own aliases so one model's
    alias cannot claim another model's score.
    """
    scored: list[dict[str, Any]] = []
    for route in routes:
        if _route_identity(route) in aa_identities:
            continue
        entry, confidence, method = match_stealthmark(route, entries, min_confidence)
        if entry is None:
            continue
        scored.append(
            {
                "provider": route.provider,
                "route_model": route.route_model,
                "model_name": route.model_name,
                "api_base": route.api_base,
                "aliases": list(route.aliases),
                "metadata": dict(route.metadata) if route.metadata else None,
                "source": route.source,
                "source_label": route_source_label(route.source),
                **route_free_fields(route),
                "stealthmark_match_confidence": round(confidence, 4),
                "stealthmark_match_method": method,
                "stealthmark": entry,
            }
        )
    return scored


def stealthmark_group_identity(route: dict[str, Any]) -> str:
    """One group per StealthMark model, not per route."""
    entry = route.get("stealthmark") if isinstance(route.get("stealthmark"), dict) else {}
    name = clean_text(entry.get("id")) or clean_text(entry.get("name")) or clean_text(entry.get("revealed_name"))
    if name:
        return normalize_text(name)
    return normalize_text(route["route_model"])


def group_stealthmark_routes(routes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Collapse routes that StealthMark identifies as the same model."""
    grouped: dict[str, dict[str, Any]] = {}
    for route in routes:
        key = stealthmark_group_identity(route)
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {"routes": [], "stealthmark": route.get("stealthmark")}
            grouped[key] = bucket
        bucket["routes"].append(route)
    return grouped


def _free_route_from_dict(route: dict[str, Any]) -> FreeRoute:
    """Rebuild a FreeRoute from a scored route dict for metadata lookup."""
    return FreeRoute(
        provider=clean_text(route.get("provider")),
        route_model=clean_text(route.get("route_model")),
        model_name=clean_text(route.get("model_name")),
        api_base=route.get("api_base"),
        aliases=list(route.get("aliases") or []),
        raw_index=0,
        source=clean_text(route.get("source")),
        metadata=route.get("metadata") if isinstance(route.get("metadata"), dict) else None,
    )


def build_stealthmark_records(
    routes: list[dict[str, Any]],
    models_dev_rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Promote free routes that StealthMark scores and Artificial Analysis does not.

    StealthMark measures visual-SVG generation and planning games; it has no
    coding benchmark. Its General score is therefore used only as an
    intelligence-shaped proxy, and every record is labelled ``stealthmark-proxy``
    so it cannot be mistaken for an AA result. These models stay in their own
    output and console section rather than being mixed with AA-scored models.
    """
    grouped = group_stealthmark_routes(routes)
    records: list[dict[str, Any]] = []
    below_floor = 0
    not_agent_ready = 0
    not_chat = 0

    for group in grouped.values():
        sm = group.get("stealthmark") if isinstance(group.get("stealthmark"), dict) else {}
        general = sm.get("general")
        visual = sm.get("visual")
        overall = sm.get("overall")
        if not isinstance(general, (int, float)) or isinstance(general, bool):
            below_floor += 1
            continue

        score = float(general)
        if score >= args.min_intelligence:
            tier = "strong"
        elif args.include_useful and score >= args.useful_intelligence:
            tier = "useful"
        else:
            below_floor += 1
            continue

        route_dicts = sorted(
            group["routes"], key=lambda r: (r.get("source", ""), r.get("route_model", ""))
        )

        name = (
            clean_text(sm.get("name"))
            or clean_text(sm.get("id"))
            or clean_text(route_dicts[0].get("model_name"))
            or clean_text(route_dicts[0].get("route_model"))
            or clean_text(sm.get("revealed_name"))
        )
        description = clean_text(route_dicts[0].get("description"))
        if not description:
            for route in route_dicts:
                meta = route.get("metadata")
                if isinstance(meta, dict):
                    description = clean_text(meta.get("description"))
                    if description:
                        break
        if NON_CHAT_PATTERN.search(f"{name} {description}"):
            not_chat += 1
            continue

        # Prefer a real models.dev match, then the feed's own metadata. Stealth
        # models are usually absent from models.dev, so the provider entry is the
        # normal source and must be accepted when it is complete enough.
        def is_feed_meta(meta: dict[str, Any]) -> bool:
            return meta.get("match_method") == "feed-metadata"

        best_meta: dict[str, Any] | None = None
        best_ready: bool | None = None
        best_reasons: list[str] = ["no capability metadata"]
        for route in route_dicts:
            meta = _unbenchmarked_metadata(_free_route_from_dict(route), models_dev_rows, args)
            if best_meta is None or (not is_feed_meta(meta) and is_feed_meta(best_meta)):
                best_meta = meta
                best_ready, best_reasons = agent_readiness(meta, args.min_context)
            if best_meta.get("matched") and not is_feed_meta(best_meta):
                break
        if best_meta is None:
            best_meta = models_dev_metadata(None)
            best_ready, best_reasons = agent_readiness(best_meta, args.min_context)

        # A known non-agent-ready model is not promoted; unknown metadata is kept
        # and marked "?" so the section stays honest about what is unverified.
        if best_ready is False:
            not_agent_ready += 1
            continue

        modalities = best_meta.get("modalities") if isinstance(best_meta.get("modalities"), dict) else {}
        outputs = modalities.get("output")
        if isinstance(outputs, list) and outputs and "text" not in outputs:
            not_chat += 1
            continue

        free_routes = []
        for route in route_dicts:
            free_routes.append(
                {
                    key: value
                    for key, value in route.items()
                    if key not in ("stealthmark", "aliases", "metadata", "description")
                }
            )

        records.append(
            {
                "model": {
                    "name": name,
                    "stealthmark_id": sm.get("id"),
                    "revealed_name": sm.get("revealed_name"),
                    "identity_status": sm.get("identity_status"),
                    "group": sm.get("group"),
                    "page_url": sm.get("page_url"),
                },
                "quality": {
                    "tier": tier,
                    "selected": True,
                    "capability_score": round(capability_score(score, None, None) or score, 3),
                    "intelligence": score,
                    "coding": None,
                    "agentic": None,
                    "score_source": "stealthmark-proxy",
                    "score_inherited_from": None,
                    "score_scale": "StealthMark General (0-100); not Artificial Analysis",
                    "stealthmark": {
                        "general": score,
                        "visual": float(visual) if isinstance(visual, (int, float)) else None,
                        "overall": float(overall) if isinstance(overall, (int, float)) else None,
                        "results_counted": sm.get("results_counted"),
                        "prompts": sm.get("prompts"),
                        "edition": sm.get("edition"),
                        "source_url": sm.get("source_url"),
                    },
                    "reasons_not_selected": [],
                },
                "agent_readiness": {
                    "status": best_ready,
                    "min_context_required": args.min_context,
                    "reasons": best_reasons,
                    "models_dev": best_meta,
                },
                "free_routes": free_routes,
                "route_count": len(free_routes),
                "best_route_match_confidence": round(
                    max(
                        (
                            r.get("stealthmark_match_confidence")
                            for r in route_dicts
                            if isinstance(r.get("stealthmark_match_confidence"), (int, float))
                        ),
                        default=0.0,
                    ),
                    4,
                ),
                "sources": {
                    "free_routes": sorted(
                        {route_source_label(r.get("source")) for r in free_routes}
                    ),
                    "benchmarks": "StealthMark (proxy, not Artificial Analysis)",
                    "capabilities": (
                        "feed metadata"
                        if is_feed_meta(best_meta)
                        else ("models.dev" if best_meta.get("matched") else "unavailable")
                    ),
                },
            }
        )

    records.sort(
        key=lambda rec: (
            rec["quality"]["intelligence"],
            rec["quality"]["stealthmark"]["visual"] or 0.0,
            rec["route_count"],
            clean_text(rec["model"]["name"]),
        ),
        reverse=True,
    )

    info = {
        "models_gathered": 0,
        "routes_matched": len(routes),
        "models_promoted": len(records),
        "selected_models": len(records),
        "models_below_floor": below_floor,
        "models_skipped_not_agent_ready": not_agent_ready,
        "models_skipped_not_chat": not_chat,
        "min_intelligence": args.min_intelligence,
        "scoring": "StealthMark General as an intelligence proxy; no AA coding score",
        "source_url": DEFAULT_STEALTH_URL,
    }
    return records, info


# ---------------------------------------------------------------------------
# Published composite (llm-stats.com).
#
# llm-stats publishes an "LLM Stats Score": one composite built from ranked
# benchmark evidence, with per-benchmark provenance. It is a *different scale*
# from the Artificial Analysis intelligence index, so its number is never written
# into the AA column. Models that AA has not indexed are listed here in their own
# section, with the source, the scale, and the fact that the underlying rows are
# provider-reported wherever that is true.
# ---------------------------------------------------------------------------

DEFAULT_LLMSTATS_URL = "https://llm-stats.com/leaderboards/llm-leaderboard"


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _llmstats_flight_text(html: str) -> str:
    """Recover the JSON payload from llm-stats' React flight stream.

    The leaderboard is a Next.js page that ships its rows inside
    ``self.__next_f.push([1,"<js string literal>"])`` chunks, so the row objects
    only appear after the literal escapes are undone.
    """
    chunks: list[str] = []
    for match in re.finditer(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html):
        try:
            chunk = json.loads(match.group(1))
        except Exception:  # noqa: BLE001 - a truncated chunk is skipped, not fatal
            continue
        if isinstance(chunk, str):
            chunks.append(chunk)
    if chunks:
        return "".join(chunks)
    # A page that changed shape still works if the escapes are simply removed.
    return html.replace('\\"', '"').replace("\\\\", "\\")


def parse_llmstats_models(html: str) -> list[dict[str, Any]]:
    """Model rows from the llm-stats leaderboard payload, one dict per model id."""
    blob = _llmstats_flight_text(html)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in re.finditer(r'\{"model_id":"', blob):
        start = match.start()
        depth = 0
        for pos in range(start, len(blob)):
            char = blob[pos]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        row = json.loads(blob[start : pos + 1])
                    except Exception:  # noqa: BLE001 - malformed row is skipped
                        row = None
                    if isinstance(row, dict):
                        model_id = clean_text(row.get("model_id"))
                        if model_id and model_id not in seen:
                            seen.add(model_id)
                            rows.append(row)
                    break
    return rows


def fetch_llmstats(url: str, attempts: int = 3) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch the llm-stats leaderboard.

    The leaderboard is rendered per request and its row set shifts between
    fetches: a model can be missing, or present with a null index, for one
    response and back the next. Three attempts are merged so a passing row is
    kept once it is seen, and a row with a score beats the same row without one.

    Best-effort enrichment: a fetch failure must not break the AA-scored
    shortlist, so the error is returned for the summary instead of raising.
    """
    merged: dict[str, dict[str, Any]] = {}
    last_error: str | None = None

    for attempt in range(max(1, attempts)):
        try:
            html = http_get_bytes(url, headers={"Accept": "text/html"}).decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 - best-effort enrichment
            last_error = str(exc)
            continue
        rows = parse_llmstats_models(html)
        if not rows:
            last_error = "llm-stats page was readable but no model rows were recognized"
            continue
        last_error = None
        for row in rows:
            model_id = clean_text(row.get("model_id"))
            if not model_id:
                continue
            held = merged.get(model_id)
            if held is None or (held.get("index_general") is None and row.get("index_general") is not None):
                merged[model_id] = row
        if attempt + 1 < max(1, attempts):
            time.sleep(2)

    if not merged:
        return [], last_error or "llm-stats returned no usable model rows"
    return list(merged.values()), None


def llmstats_entry(row: dict[str, Any]) -> dict[str, Any] | None:
    """One scored llm-stats entry, or None when the model has no overall index."""
    general = _as_float(row.get("index_general"))
    if general is None:
        return None
    model_id = clean_text(row.get("model_id"))
    return {
        "id": model_id or None,
        "name": clean_text(row.get("name")) or None,
        "organization": clean_text(row.get("organization")) or None,
        "general": general,
        "reasoning": _as_float(row.get("index_reasoning")),
        "coding": _as_float(row.get("index_code")),
        "agentic": _as_float(row.get("index_agents")),
        "release_date": clean_text(row.get("release_date")) or None,
        "page_url": f"https://llm-stats.com/models/{model_id}" if model_id else DEFAULT_LLMSTATS_URL,
        "source_url": DEFAULT_LLMSTATS_URL,
    }


def llmstats_aliases(entry: dict[str, Any]) -> list[str]:
    return alias_variants(entry.get("id"), entry.get("name"))


def match_llmstats(
    route: FreeRoute, entries: list[dict[str, Any]], min_confidence: float
) -> tuple[dict[str, Any] | None, float, str]:
    """Best llm-stats entry for a route, matched on the feed's own aliases.

    Matching stays per-route on that route's own identity for the same reason
    StealthMark does: a union of aliases would let one route claim another
    model's score.
    """
    best: dict[str, Any] | None = None
    best_score = 0.0
    best_method = "none"
    for entry in entries:
        score, method, _src, _tgt = compare_alias_sets(route.aliases, llmstats_aliases(entry))
        if score > best_score:
            best, best_score, best_method = entry, score, method
    if best is None or best_score < min_confidence:
        return None, best_score, f"below-threshold:{best_method}"
    return best, best_score, best_method


def published_only_routes(
    routes: list[FreeRoute],
    claimed_identities: set[str],
    entries: list[dict[str, Any]],
    min_confidence: float,
) -> list[dict[str, Any]]:
    """Routes llm-stats can score that no earlier lane has already claimed.

    ``claimed_identities`` carries the AA-covered models and everything StealthMark
    already promoted, so a model is never listed in two lanes at once.
    """
    scored: list[dict[str, Any]] = []
    for route in routes:
        if _route_identity(route) in claimed_identities:
            continue
        entry, confidence, method = match_llmstats(route, entries, min_confidence)
        if entry is None:
            continue
        scored.append(
            {
                "provider": route.provider,
                "route_model": route.route_model,
                "model_name": route.model_name,
                "api_base": route.api_base,
                "aliases": list(route.aliases),
                "metadata": dict(route.metadata) if route.metadata else None,
                "source": route.source,
                "source_label": route_source_label(route.source),
                **route_free_fields(route),
                "llmstats_match_confidence": round(confidence, 4),
                "llmstats_match_method": method,
                "llmstats": entry,
            }
        )
    return scored


def group_published_routes(routes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Collapse routes that llm-stats identifies as the same model."""
    grouped: dict[str, dict[str, Any]] = {}
    for route in routes:
        entry = route.get("llmstats") if isinstance(route.get("llmstats"), dict) else {}
        key = normalize_text(clean_text(entry.get("id")) or clean_text(route["route_model"]))
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {"routes": [], "llmstats": route.get("llmstats")}
            grouped[key] = bucket
        bucket["routes"].append(route)
    return grouped


def build_published_records(
    routes: list[dict[str, Any]],
    models_dev_rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Promote free routes that llm-stats scores and no earlier lane claimed.

    The llm-stats index is a relative ranking on its own scale, not an Artificial
    Analysis intelligence index, so these records carry no ``intelligence`` value
    and are kept in a separate section. Provider-reported rows are labelled: an
    unscored model's composite is only as independent as the benchmarks behind it.
    """
    grouped = group_published_routes(routes)
    records: list[dict[str, Any]] = []
    below_floor = 0
    not_agent_ready = 0
    not_chat = 0

    for group in grouped.values():
        entry = group.get("llmstats") if isinstance(group.get("llmstats"), dict) else {}
        score = _as_float(entry.get("general"))
        if score is None:
            below_floor += 1
            continue
        if score < args.min_published:
            below_floor += 1
            continue

        route_dicts = sorted(
            group["routes"], key=lambda r: (r.get("source", ""), r.get("route_model", ""))
        )
        name = (
            clean_text(entry.get("name"))
            or clean_text(entry.get("id"))
            or clean_text(route_dicts[0].get("model_name"))
            or clean_text(route_dicts[0].get("route_model"))
        )
        description = clean_text(route_dicts[0].get("description"))
        if not description:
            for route in route_dicts:
                meta = route.get("metadata")
                if isinstance(meta, dict):
                    description = clean_text(meta.get("description"))
                    if description:
                        break
        if NON_CHAT_PATTERN.search(f"{name} {description}"):
            not_chat += 1
            continue

        best_meta: dict[str, Any] | None = None
        best_ready: bool | None = None
        best_reasons: list[str] = ["no capability metadata"]
        for route in route_dicts:
            meta = _unbenchmarked_metadata(_free_route_from_dict(route), models_dev_rows, args)
            if best_meta is None:
                best_meta = meta
                best_ready, best_reasons = agent_readiness(meta, args.min_context)
            if best_meta.get("matched"):
                break
        if best_meta is None:
            best_meta = models_dev_metadata(None)
            best_ready, best_reasons = agent_readiness(best_meta, args.min_context)

        # A known non-agent-ready model is not promoted; unknown metadata is kept
        # and marked "?" so the section stays honest about what is unverified.
        if best_ready is False:
            not_agent_ready += 1
            continue

        modalities = best_meta.get("modalities") if isinstance(best_meta.get("modalities"), dict) else {}
        outputs = modalities.get("output")
        if isinstance(outputs, list) and outputs and "text" not in outputs:
            not_chat += 1
            continue

        free_routes = [
            {key: value for key, value in route.items() if key not in ("llmstats", "aliases", "metadata", "description")}
            for route in route_dicts
        ]

        records.append(
            {
                "model": {
                    "name": name,
                    "llmstats_id": entry.get("id"),
                    "organization": entry.get("organization"),
                    "page_url": entry.get("page_url"),
                },
                "quality": {
                    "tier": "published",
                    "selected": True,
                    "capability_score": round(score, 3),
                    "intelligence": None,
                    "coding": None,
                    "agentic": None,
                    "score_source": "published-composite",
                    "score_inherited_from": None,
                    "score_scale": (
                        "llm-stats composite (0-100, relative ranking); "
                        "not Artificial Analysis and not comparable with it"
                    ),
                    "llmstats": {
                        "general": score,
                        "reasoning": _as_float(entry.get("reasoning")),
                        "coding": _as_float(entry.get("coding")),
                        "agentic": _as_float(entry.get("agentic")),
                        "release_date": entry.get("release_date"),
                        "source_url": entry.get("source_url"),
                    },
                    "reasons_not_selected": [],
                },
                "agent_readiness": {
                    "status": best_ready,
                    "min_context_required": args.min_context,
                    "reasons": best_reasons,
                    "models_dev": best_meta,
                },
                "free_routes": free_routes,
                "route_count": len(free_routes),
                "best_route_match_confidence": round(
                    max(
                        (
                            r.get("llmstats_match_confidence")
                            for r in route_dicts
                            if isinstance(r.get("llmstats_match_confidence"), (int, float))
                        ),
                        default=0.0,
                    ),
                    4,
                ),
                "sources": {
                    "free_routes": sorted({route_source_label(r.get("source")) for r in free_routes}),
                    "benchmarks": "llm-stats composite (provider-reported rows; not Artificial Analysis)",
                    "capabilities": "models.dev" if best_meta.get("matched") else "unavailable",
                },
            }
        )

    records.sort(
        key=lambda rec: (
            rec["quality"]["llmstats"]["general"],
            rec["route_count"],
            clean_text(rec["model"]["name"]),
        ),
        reverse=True,
    )

    info = {
        "models_gathered": 0,
        "routes_matched": len(routes),
        "models_promoted": len(records),
        "selected_models": len(records),
        "models_below_floor": below_floor,
        "models_skipped_not_agent_ready": not_agent_ready,
        "models_skipped_not_chat": not_chat,
        "min_published": args.min_published,
        "scoring": "llm-stats composite as a published-evidence score; not an AA intelligence index",
        "source_url": DEFAULT_LLMSTATS_URL,
    }
    return records, info


def percentile_threshold(values: list[float], percentile: float) -> float | None:
    vals = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    if not vals:
        return None
    p = max(0.0, min(100.0, percentile)) / 100.0
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * p
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vals[lo]
    frac = pos - lo
    return vals[lo] * (1 - frac) + vals[hi] * frac


def capability_score(intelligence: float | None, coding: float | None, agentic: float | None) -> float | None:
    weighted = []
    if intelligence is not None:
        weighted.append((0.30, intelligence))
    if agentic is not None:
        weighted.append((0.35, agentic))
    if coding is not None:
        weighted.append((0.25, coding))
    if not weighted:
        return None
    total_weight = sum(w for w, _ in weighted)
    return sum(w * v for w, v in weighted) / total_weight


def classify_quality(
    intelligence: float | None,
    coding: float | None,
    agentic: float | None,
    args: argparse.Namespace,
    adaptive_thresholds: dict[str, float | None],
) -> tuple[str, bool, list[str]]:
    reasons: list[str] = []
    specialized_values = [v for v in (coding, agentic) if v is not None]
    specialized = max(specialized_values) if specialized_values else None

    # AA's free tier no longer serves coding/agentic indices. When they are
    # absent, intelligence is the only available signal, so do not fail a model
    # for a benchmark the feed does not provide.
    useful = (
        intelligence is not None
        and intelligence >= args.useful_intelligence
        and (specialized is None or specialized >= args.useful_specialized)
    )

    absolute_strong = (
        intelligence is not None
        and intelligence >= args.min_intelligence
        and (specialized is None or specialized >= args.min_specialized)
    )

    if not absolute_strong:
        if intelligence is None:
            reasons.append("missing intelligence score")
        elif intelligence < args.min_intelligence:
            reasons.append(f"intelligence {intelligence:.1f} < {args.min_intelligence:.1f}")
        if specialized is None:
            reasons.append("coding/agentic score unavailable (AA free tier)")
        elif specialized < args.min_specialized:
            reasons.append(f"best specialized score {specialized:.1f} < {args.min_specialized:.1f}")

    adaptive_ok = True
    adaptive_enabled = not args.no_adaptive and (
        args.intel_percentile is not None or args.specialized_percentile is not None
    )
    if adaptive_enabled:
        if args.intel_percentile is not None:
            intel_cut = adaptive_thresholds.get("intelligence")
            if intel_cut is not None and (intelligence is None or intelligence < intel_cut):
                adaptive_ok = False
                reasons.append(
                    f"below free-candidate intelligence p{args.intel_percentile:g} cutoff ({intel_cut:.1f})"
                )
        if args.specialized_percentile is not None:
            spec_cut = adaptive_thresholds.get("specialized")
            if spec_cut is not None and specialized is not None and specialized < spec_cut:
                adaptive_ok = False
                reasons.append(
                    f"below free-candidate specialized p{args.specialized_percentile:g} cutoff ({spec_cut:.1f})"
                )

    strong = absolute_strong and adaptive_ok
    if strong:
        return "strong", True, []
    if useful:
        return "useful", bool(args.include_useful), reasons
    return "weak", False, reasons


def flatten_models_dev(doc: Any) -> list[dict[str, Any]]:
    """Support current models.dev shapes plus a few historical/simple variants."""
    rows: list[dict[str, Any]] = []

    def add(item: dict[str, Any], inferred_id: str | None = None) -> None:
        if not isinstance(item, dict):
            return
        row = dict(item)
        if inferred_id and not row.get("id"):
            row["id"] = inferred_id
        if row.get("name") or row.get("id") or row.get("family"):
            rows.append(row)

    if isinstance(doc, list):
        for item in doc:
            if isinstance(item, dict):
                add(item)
        return rows

    if not isinstance(doc, dict):
        return rows

    # Common provider-agnostic shape: {"lab/model": {...}}
    direct_model_count = 0
    for key, value in doc.items():
        if isinstance(value, dict) and any(k in value for k in ("name", "family", "limit", "tool_call", "capabilities")):
            add(value, str(key))
            direct_model_count += 1
    if direct_model_count:
        return rows

    # Alternate nested shapes.
    for parent_key, parent in doc.items():
        if not isinstance(parent, dict):
            continue
        models = parent.get("models")
        if isinstance(models, dict):
            for model_id, model in models.items():
                if isinstance(model, dict):
                    inferred = f"{parent_key}/{model_id}" if "/" not in str(model_id) else str(model_id)
                    add(model, inferred)
        elif isinstance(models, list):
            for model in models:
                if isinstance(model, dict):
                    add(model)

    return rows


def models_dev_aliases(item: dict[str, Any]) -> list[str]:
    vals = [
        clean_text(item.get("id")),
        clean_text(item.get("name")),
        clean_text(item.get("family")),
        clean_text(item.get("canonical_model_id")),
    ]
    return alias_variants(*vals)


def models_dev_metadata(item: dict[str, Any] | None) -> dict[str, Any]:
    if not item:
        return {
            "matched": False,
            "id": None,
            "name": None,
            "context": None,
            "tool_call": None,
            "reasoning": None,
            "attachment": None,
            "structured_output": None,
            "modalities": None,
            "release_date": None,
        }

    limit = item.get("limit") if isinstance(item.get("limit"), dict) else {}
    capabilities = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}

    tool_call = item.get("tool_call")
    if tool_call is None and isinstance(capabilities, dict):
        tools = capabilities.get("tools")
        if isinstance(tools, dict):
            tool_call = tools.get("supported")
        elif isinstance(tools, bool):
            tool_call = tools

    reasoning = item.get("reasoning")
    if reasoning is None and isinstance(capabilities, dict):
        r = capabilities.get("reasoning")
        if isinstance(r, dict):
            reasoning = r.get("supported")
        elif isinstance(r, bool):
            reasoning = r

    context = limit.get("context") if isinstance(limit, dict) else None
    if not isinstance(context, int):
        context = item.get("context_length") if isinstance(item.get("context_length"), int) else None

    return {
        "matched": True,
        "id": item.get("id"),
        "name": item.get("name"),
        "context": context,
        "tool_call": tool_call if isinstance(tool_call, bool) else None,
        "reasoning": reasoning if isinstance(reasoning, bool) else None,
        "attachment": item.get("attachment") if isinstance(item.get("attachment"), bool) else None,
        "structured_output": item.get("structured_output") if isinstance(item.get("structured_output"), bool) else None,
        "modalities": item.get("modalities") if isinstance(item.get("modalities"), dict) else None,
        "release_date": clean_text(item.get("release_date")) or None,
    }


def agent_readiness(meta: dict[str, Any], min_context: int) -> tuple[bool | None, list[str]]:
    reasons: list[str] = []
    context = meta.get("context")
    tools = meta.get("tool_call")

    unknown = context is None or tools is None
    if context is not None and context < min_context:
        reasons.append(f"context {context} < {min_context}")
    if tools is False:
        reasons.append("tool calling unsupported")

    if reasons:
        return False, reasons
    if unknown:
        return None, ["models.dev metadata incomplete"]
    return True, []


def _aa_canonical_preference(item: dict[str, Any]) -> tuple:
    """Deterministic tie-break when several AA variants match a route equally.

    AA lists one entry per reasoning effort/snapshot, and ``normalize_text`` erases
    those suffixes, so exact ties are common. Prefer the entry with its own score,
    then the shortest (canonical) slug, instead of whichever came first in the feed.
    """
    slug = clean_text(item.get("slug")) or clean_text(item.get("name"))
    has_score = extract_eval(item, "artificial_analysis_intelligence_index") is not None
    return (1 if has_score else 0, -len(slug))


def group_routes_by_aa(
    routes: list[FreeRoute],
    aa_models: list[dict[str, Any]],
    min_confidence: float,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[str, dict[str, Any]] = {}
    unmatched: list[dict[str, Any]] = []

    for route in routes:
        match = match_item(
            route.aliases,
            aa_models,
            aa_aliases,
            min_confidence=min_confidence,
            preference=_aa_canonical_preference,
        )
        if not match.item:
            unmatched.append(
                {
                    "provider": route.provider,
                    "route_model": route.route_model,
                    "model_name": route.model_name,
                    "api_base": route.api_base,
                    "source": route.source,
                    "source_label": route_source_label(route.source),
                    **route_free_fields(route),
                    "aliases": route.aliases,
                    "best_match_confidence": round(match.confidence, 4),
                    "match_status": match.method,
                    "matched_alias": match.matched_alias,
                    "matched_target": match.matched_target,
                }
            )
            continue

        aa = match.item
        key = clean_text(aa.get("id")) or clean_text(aa.get("slug")) or clean_text(aa.get("name"))
        if key not in groups:
            groups[key] = {
                "aa": aa,
                "routes": [],
                "route_match_confidences": [],
            }
        groups[key]["routes"].append(
            {
                "provider": route.provider,
                "route_model": route.route_model,
                "model_name": route.model_name,
                "api_base": route.api_base,
                "aliases": route.aliases,
                **route_free_fields(route),
                "match_confidence": round(match.confidence, 4),
                "match_method": match.method,
                "source": route.source,
            }
        )
        groups[key]["route_match_confidences"].append(match.confidence)

    return groups, unmatched


def resolve_group_scores(
    groups: dict[str, dict[str, Any]],
    variant_scores: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve per-group benchmark scores, filling gaps from same-family variants."""
    resolved: list[dict[str, Any]] = []
    for group in groups.values():
        aa = group["aa"]
        own_intelligence = extract_eval(aa, "artificial_analysis_intelligence_index")
        own_coding = extract_eval(aa, "artificial_analysis_coding_index")
        own_agentic = extract_eval(aa, "artificial_analysis_agentic_index")

        inherited = variant_scores.get(variant_base_key(aa)) or {}
        intelligence = own_intelligence if own_intelligence is not None else inherited.get("intelligence")
        coding = own_coding if own_coding is not None else inherited.get("coding")
        agentic = own_agentic if own_agentic is not None else inherited.get("agentic")

        if own_intelligence is not None:
            score_source, inherited_from = "own", None
        elif intelligence is not None:
            score_source, inherited_from = "inherited", inherited.get("intelligence_from")
        else:
            score_source, inherited_from = "missing", None

        resolved.append(
            {
                "group": group,
                "aa": aa,
                "intelligence": intelligence,
                "coding": coding,
                "agentic": agentic,
                "score_source": score_source,
                "score_inherited_from": inherited_from,
            }
        )
    return resolved


def build_result(
    groups: dict[str, dict[str, Any]],
    aa_models: list[dict[str, Any]],
    models_dev_rows: list[dict[str, Any]],
    args: argparse.Namespace,
    index_version: float | None,
    stealthmark_routes: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    variant_scores = build_variant_score_index(aa_models)
    resolved = resolve_group_scores(groups, variant_scores)

    # Adaptive cutoffs are computed over the free candidates themselves. AA's
    # free tier mixes paid and free models, so a catalogue-wide percentile puts
    # the cutoff above every model that actually has a free route.
    intelligence_values = [r["intelligence"] for r in resolved if r["intelligence"] is not None]
    specialized_values: list[float] = []
    for r in resolved:
        vals = [v for v in (r["coding"], r["agentic"]) if v is not None]
        if vals:
            specialized_values.append(max(vals))

    adaptive_thresholds = {
        "intelligence": (
            percentile_threshold(intelligence_values, args.intel_percentile)
            if not args.no_adaptive and args.intel_percentile is not None
            else None
        ),
        "specialized": (
            percentile_threshold(specialized_values, args.specialized_percentile)
            if not args.no_adaptive and args.specialized_percentile is not None
            else None
        ),
    }

    catalogue_intelligence_scores = sum(
        1
        for item in aa_models
        if extract_eval(item, "artificial_analysis_intelligence_index") is not None
    )
    catalogue_specialized_scores = sum(
        1
        for item in aa_models
        if extract_eval(item, "artificial_analysis_coding_index") is not None
        or extract_eval(item, "artificial_analysis_agentic_index") is not None
    )

    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for entry in resolved:
        group = entry["group"]
        aa = entry["aa"]
        intelligence = entry["intelligence"]
        coding = entry["coding"]
        agentic = entry["agentic"]
        score_source = entry["score_source"]
        score_inherited_from = entry["score_inherited_from"]

        score = capability_score(intelligence, coding, agentic)

        tier, selected_by_quality, quality_reasons = classify_quality(
            intelligence, coding, agentic, args, adaptive_thresholds
        )

        source_aliases = aa_aliases(aa)
        for route in group["routes"]:
            source_aliases.extend(alias_variants(route["route_model"], route["model_name"]))
        source_aliases = alias_variants(*source_aliases)

        md_match = match_item(
            source_aliases,
            models_dev_rows,
            models_dev_aliases,
            min_confidence=args.models_dev_match_threshold,
        ) if models_dev_rows else MatchResult(None, 0.0, "unavailable")
        md_meta = models_dev_metadata(md_match.item)
        md_meta["match_confidence"] = round(md_match.confidence, 4) if md_match.item else None
        md_meta["match_method"] = md_match.method

        ready, readiness_reasons = agent_readiness(md_meta, args.min_context)
        final_selected = selected_by_quality
        selection_reasons = list(quality_reasons)
        if args.require_agent_ready:
            if ready is not True:
                final_selected = False
                selection_reasons.extend(readiness_reasons)

        perf = aa.get("performance") if isinstance(aa.get("performance"), dict) else {}
        creator = aa.get("model_creator") if isinstance(aa.get("model_creator"), dict) else {}

        record = {
            "model": {
                "aa_id": aa.get("id"),
                "aa_slug": aa.get("slug"),
                "name": aa.get("name"),
                "creator": creator.get("name"),
                "release_date": aa.get("release_date"),
            },
            "quality": {
                "tier": tier,
                "selected": final_selected,
                "capability_score": round(score, 3) if score is not None else None,
                "intelligence": intelligence,
                "coding": coding,
                "agentic": agentic,
                "score_source": score_source,
                "score_inherited_from": score_inherited_from,
                "reasons_not_selected": selection_reasons,
            },
            "performance": {
                "tokens_per_second": perf.get("median_output_tokens_per_second") if isinstance(perf, dict) else None,
                "time_to_first_token_seconds": perf.get("median_time_to_first_token_seconds") if isinstance(perf, dict) else None,
            },
            "agent_readiness": {
                "status": ready,
                "min_context_required": args.min_context,
                "reasons": readiness_reasons,
                "models_dev": md_meta,
            },
            "free_routes": sorted(group["routes"], key=lambda r: (r["provider"].lower(), r["route_model"])),
            "route_count": len(group["routes"]),
            "best_route_match_confidence": round(max(group["route_match_confidences"]), 4),
            "sources": {
                "free_routes": sorted(
                    {route_source_label(r.get("source")) for r in group["routes"]}
                ),
                "benchmarks": "Artificial Analysis",
                "capabilities": "models.dev" if models_dev_rows else "unavailable",
            },
        }

        (selected if final_selected else rejected).append(record)

    def sort_key(rec: dict[str, Any]) -> tuple[float, float, int]:
        q = rec["quality"]

        def val(key: str) -> float:
            v = q.get(key)
            return float(v) if isinstance(v, (int, float)) else -1.0

        if args.sort_by == "score":
            return (val("capability_score"), val("intelligence"), int(rec.get("route_count") or 0))
        return (val(args.sort_by), val("capability_score"), int(rec.get("route_count") or 0))

    selected.sort(key=sort_key, reverse=True)
    rejected.sort(key=sort_key, reverse=True)

    stealth_records, stealthmark_info = build_stealthmark_records(
        stealthmark_routes or [], models_dev_rows, args
    )

    summary = {
        "artificial_analysis_index_version": index_version,
        "aa_models_considered": len(aa_models),
        "free_underlying_models_matched": len(groups),
        "selected_underlying_models": len(selected),
        "intelligence_floor": args.min_intelligence,
        "intelligence_floor_source": "--min-intelligence",
        "adaptive_filter_enabled": not args.no_adaptive
        and (args.intel_percentile is not None or args.specialized_percentile is not None),
        "thresholds": {
            "absolute": {
                "min_intelligence": args.min_intelligence,
                "min_coding_or_agentic": args.min_specialized,
            },
            "adaptive": {
                "intelligence_percentile": args.intel_percentile
                if not args.no_adaptive and args.intel_percentile is not None
                else None,
                "intelligence_cutoff": adaptive_thresholds["intelligence"],
                "specialized_percentile": args.specialized_percentile
                if not args.no_adaptive and args.specialized_percentile is not None
                else None,
                "specialized_cutoff": adaptive_thresholds["specialized"],
            },
            "useful_tier": {
                "min_intelligence": args.useful_intelligence,
                "min_coding_or_agentic": args.useful_specialized,
                "included_in_output": args.include_useful,
            },
            "agent_ready": {
                "required_for_selection": args.require_agent_ready,
                "min_context": args.min_context,
                "tool_calling_required": True,
            },
        },
        "benchmark_coverage": {
            "aa_catalogue_models_with_intelligence_score": catalogue_intelligence_scores,
            "aa_catalogue_models_with_coding_or_agentic_score": catalogue_specialized_scores,
            "coding_agentic_available": bool(catalogue_specialized_scores),
            "free_candidates_scored": len(intelligence_values),
            "free_candidates_scored_own": sum(1 for r in resolved if r["score_source"] == "own"),
            "free_candidates_scored_inherited": sum(1 for r in resolved if r["score_source"] == "inherited"),
            "free_candidates_unscored": sum(1 for r in resolved if r["score_source"] == "missing"),
            "adaptive_pool": "free candidates with a score",
        },
        "stealthmark": stealthmark_info,
    }
    return selected, rejected, summary, stealth_records


def _json_default(value: Any) -> Any:
    """Serialize Decimal catalogue numbers; they carry no billing meaning here."""
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, selected: list[dict[str, Any]]) -> None:
    fields = [
        "rank",
        "model_name",
        "creator",
        "aa_slug",
        "tier",
        "capability_score",
        "intelligence",
        "coding",
        "agentic",
        "score_source",
        "score_inherited_from",
        "agent_ready",
        "context",
        "tool_call",
        "reasoning",
        "provider",
        "route_model",
        "route_source",
        "api_base",
        "verification",
        "verified_free",
        "requires_provider_key",
        "stale_mirror",
        "route_match_confidence",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for rank, record in enumerate(selected, 1):
            model = record["model"]
            quality = record["quality"]
            agent = record["agent_readiness"]
            md = agent["models_dev"]
            for route in record["free_routes"]:
                writer.writerow(
                    {
                        "rank": rank,
                        "model_name": model.get("name"),
                        "creator": model.get("creator"),
                        "aa_slug": model.get("aa_slug"),
                        "tier": quality.get("tier"),
                        "capability_score": quality.get("capability_score"),
                        "intelligence": quality.get("intelligence"),
                        "coding": quality.get("coding"),
                        "agentic": quality.get("agentic"),
                        "score_source": quality.get("score_source"),
                        "score_inherited_from": quality.get("score_inherited_from"),
                        "agent_ready": agent.get("status"),
                        "context": md.get("context"),
                        "tool_call": md.get("tool_call"),
                        "reasoning": md.get("reasoning"),
                        "provider": route.get("provider"),
                        "route_model": route.get("route_model"),
                        "route_source": route.get("source"),
                        "api_base": route.get("api_base"),
                        "verification": route.get("verification"),
                        "verified_free": route.get("verified_free"),
                        "requires_provider_key": route.get("requires_provider_key"),
                        "stale_mirror": route.get("stale_mirror"),
                        "route_match_confidence": route.get("match_confidence"),
                    }
                )


def route_provider_label(route: dict[str, Any]) -> str:
    """Human-facing vendor label for a route: api_base host, else provider."""
    base = clean_text(route.get("api_base"))
    if base:
        try:
            host = urllib.parse.urlsplit(base if "//" in base else f"//{base}").hostname or ""
        except ValueError:
            host = ""
        host = host.removeprefix("api.").removeprefix("www.")
        if host.endswith("googleapis.com"):
            return "google"
        if host:
            parts = host.split(".")
            # Skip utilitarian subdomains like discovery-api.intern-ai.org.cn
            if parts[0].endswith("-api") and len(parts) > 1:
                parts = parts[1:]
            return parts[0]
    return clean_text(route.get("provider")) or "unknown"


def route_source_label(source: str) -> str:
    """Human-facing label for the feed a free route was discovered from."""
    return {
        "f24": "F24 SALES LiteLLM feed (free-tier claim)",
        "openrouter": "OpenRouter public models API (zero-priced)",
        "opencode": "OpenCode provider catalogue (zero-priced)",
    }.get(clean_text(source).lower(), clean_text(source) or "unknown")


NON_CHAT_PATTERN = re.compile(
    r"\b(router|embedding|embed|rerank|reranker|moderation|guardrail|content[- ]safety|music|image generation)\b",
    re.I,
)


def _route_identity(route: FreeRoute | dict[str, Any]) -> str:
    """Provider-agnostic key for grouping the same model across feeds."""
    if isinstance(route, dict):
        aliases = route.get("aliases") or []
        route_model = route.get("route_model")
    else:
        aliases = route.aliases
        route_model = route.route_model
    candidates = [normalize_text(strip_route_decorations(a)) for a in aliases]
    candidates = [c for c in candidates if len(c) >= 4]
    return min(candidates, key=len) if candidates else normalize_text(route_model)


def _unbenchmarked_metadata(
    route: FreeRoute,
    models_dev_rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Capability metadata for a route, preferring models.dev's canonical entry.

    models.dev's provider-agnostic feed does not list stealth/new models, so the
    feed's own entry (opencode provider, OpenRouter model object) is the fallback
    and the only metadata some of these routes will ever have.
    """
    if models_dev_rows:
        match = match_item(
            route.aliases,
            models_dev_rows,
            models_dev_aliases,
            min_confidence=args.models_dev_match_threshold,
        )
        if match.item:
            meta = models_dev_metadata(match.item)
            meta["match_confidence"] = round(match.confidence, 4)
            meta["match_method"] = match.method
            return meta

    if route.metadata:
        meta = models_dev_metadata(dict(route.metadata))
        meta["match_confidence"] = None
        meta["match_method"] = "feed-metadata"
        return meta

    return models_dev_metadata(None)


def build_unbenchmarked(
    routes: list[FreeRoute],
    matched_aa_by_route: dict[tuple[str, str], str | None],
    benchmarked_keys: set[tuple[str, str]],
    models_dev_rows: list[dict[str, Any]],
    args: argparse.Namespace,
    intelligence_by_route: dict[tuple[str, str], float] | None = None,
    include_unscored: bool = False,
) -> list[dict[str, Any]]:
    """Free, agent-ready models that have no usable Artificial Analysis score.

    Stealth releases (e.g. Pulse Alpha) are absent from AA's catalogue, and AA's
    free tier leaves many known models unscored, so a benchmark-first pipeline
    drops them silently. They still belong in the shortlist as a separate,
    explicitly unbenchmarked section rather than being confused with scored
    models. Agent-readiness is required so the section does not fill with routers,
    guardrails, or audio models.

    A route with a known proxy score (for example StealthMark General) below
    ``--min-intelligence`` is hidden. A route with no score at all is hidden too
    unless ``--include-unscored`` is passed: no score is not evidence of the floor.
    """
    grouped: dict[str, dict[str, Any]] = {}

    for route in routes:
        key = (route.provider.lower(), route.route_model)
        if key in benchmarked_keys:
            continue

        known_score = (intelligence_by_route or {}).get(key)
        if known_score is None:
            if not include_unscored:
                continue
        elif known_score < args.min_intelligence:
            continue

        meta = _unbenchmarked_metadata(route, models_dev_rows, args)
        ready, _ = agent_readiness(meta, args.min_context)
        if ready is not True:
            continue

        modalities = meta.get("modalities") if isinstance(meta.get("modalities"), dict) else {}
        outputs = modalities.get("output")
        if isinstance(outputs, list) and outputs and "text" not in outputs:
            continue

        name = clean_text(route.model_name) or clean_text(route.route_model)
        description = clean_text((route.metadata or {}).get("description"))
        if NON_CHAT_PATTERN.search(f"{name} {description}"):
            continue

        aa_slug = matched_aa_by_route.get(key)
        is_stealth = bool(
            re.search(r"\bstealth\b", f"{name} {description}", re.I)
        ) or route.route_model.lower().startswith("stealth/")
        is_legacy = "legacy model retained" in description.lower()
        bucket = grouped.get(_route_identity(route))
        if bucket is None:
            grouped[_route_identity(route)] = {
                "model": {
                    "name": name,
                    "models_dev_id": meta.get("id"),
                    "description": description or None,
                },
                "benchmark": {
                    "status": "aa-unscored" if aa_slug else "no-aa-entry",
                    "aa_slug": aa_slug,
                },
                "capabilities": {
                    "context": meta.get("context"),
                    "tool_call": meta.get("tool_call"),
                    "reasoning": meta.get("reasoning"),
                    "release_date": meta.get("release_date"),
                    "agent_ready": ready,
                },
                "stealth": is_stealth,
                "legacy": is_legacy,
                "free_routes": [],
            }
            bucket = grouped[_route_identity(route)]
        else:
            bucket["stealth"] = bucket["stealth"] or is_stealth
            bucket["legacy"] = bucket["legacy"] or is_legacy

        bucket["free_routes"].append(
            {
                "provider": route.provider,
                "route_model": route.route_model,
                "model_name": route.model_name,
                "api_base": route.api_base,
                "source": route.source,
                "source_label": route_source_label(route.source),
                **route_free_fields(route),
            }
        )

    records = list(grouped.values())
    for record in records:
        record["free_routes"].sort(key=lambda r: (r["source"], r["route_model"]))
        record["route_count"] = len(record["free_routes"])
        record["providers"] = sorted({route_provider_label(r) for r in record["free_routes"]})
        record["sources"] = sorted({r["source_label"] for r in record["free_routes"]})

    def sort_key(record: dict[str, Any]) -> tuple:
        caps = record["capabilities"]
        release = clean_text(caps.get("release_date"))
        return (
            1 if record["stealth"] else 0,
            0 if record["legacy"] else 1,
            release,
            caps.get("context") or 0,
            clean_text(record["model"]["name"]),
        )

    records.sort(key=sort_key, reverse=True)
    return records


def write_unbenchmarked_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "rank",
        "model_name",
        "stealth",
        "legacy",
        "benchmark_status",
        "aa_slug",
        "context",
        "tool_call",
        "reasoning",
        "release_date",
        "provider",
        "route_model",
        "route_source",
        "api_base",
        "verification",
        "verified_free",
        "requires_provider_key",
        "stale_mirror",
        "models_dev_id",
        "description",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for rank, record in enumerate(records, 1):
            model = record["model"]
            caps = record["capabilities"]
            bench = record["benchmark"]
            for route in record["free_routes"]:
                writer.writerow(
                    {
                        "rank": rank,
                        "model_name": model.get("name"),
                        "stealth": record.get("stealth"),
                        "legacy": record.get("legacy"),
                        "benchmark_status": bench.get("status"),
                        "aa_slug": bench.get("aa_slug"),
                        "context": caps.get("context"),
                        "tool_call": caps.get("tool_call"),
                        "reasoning": caps.get("reasoning"),
                        "release_date": caps.get("release_date"),
                        "provider": route.get("provider"),
                        "route_model": route.get("route_model"),
                        "route_source": route.get("source"),
                        "api_base": route.get("api_base"),
                        "verification": route.get("verification"),
                        "verified_free": route.get("verified_free"),
                        "requires_provider_key": route.get("requires_provider_key"),
                        "stale_mirror": route.get("stale_mirror"),
                        "models_dev_id": model.get("models_dev_id"),
                        "description": model.get("description"),
                    }
                )


def write_stealthmark_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "rank",
        "model_name",
        "stealthmark_id",
        "revealed_name",
        "identity_status",
        "group",
        "tier",
        "general",
        "visual",
        "overall",
        "results_counted",
        "prompts",
        "edition",
        "score_source",
        "context",
        "tool_call",
        "reasoning",
        "release_date",
        "provider",
        "route_model",
        "route_source",
        "api_base",
        "verification",
        "verified_free",
        "requires_provider_key",
        "stale_mirror",
        "stealthmark_match_confidence",
        "page_url",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for rank, record in enumerate(records, 1):
            model = record["model"]
            quality = record["quality"]
            sm = quality["stealthmark"]
            caps = record["agent_readiness"]["models_dev"]
            for route in record["free_routes"]:
                writer.writerow(
                    {
                        "rank": rank,
                        "model_name": model.get("name"),
                        "stealthmark_id": model.get("stealthmark_id"),
                        "revealed_name": model.get("revealed_name"),
                        "identity_status": model.get("identity_status"),
                        "group": model.get("group"),
                        "tier": quality.get("tier"),
                        "general": sm.get("general"),
                        "visual": sm.get("visual"),
                        "overall": sm.get("overall"),
                        "results_counted": sm.get("results_counted"),
                        "prompts": sm.get("prompts"),
                        "edition": sm.get("edition"),
                        "score_source": quality.get("score_source"),
                        "context": caps.get("context"),
                        "tool_call": caps.get("tool_call"),
                        "reasoning": caps.get("reasoning"),
                        "release_date": caps.get("release_date"),
                        "provider": route.get("provider"),
                        "route_model": route.get("route_model"),
                        "route_source": route.get("source"),
                        "api_base": route.get("api_base"),
                        "verification": route.get("verification"),
                        "verified_free": route.get("verified_free"),
                        "requires_provider_key": route.get("requires_provider_key"),
                        "stale_mirror": route.get("stale_mirror"),
                        "stealthmark_match_confidence": route.get(
                            "stealthmark_match_confidence"
                        ),
                        "page_url": model.get("page_url"),
                    }
                )


def write_published_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "rank",
        "model_name",
        "llmstats_id",
        "organization",
        "tier",
        "composite",
        "reasoning",
        "coding",
        "agentic",
        "score_source",
        "score_scale",
        "context",
        "tool_call",
        "reasoning_flag",
        "release_date",
        "provider",
        "route_model",
        "route_source",
        "api_base",
        "verification",
        "verified_free",
        "requires_provider_key",
        "stale_mirror",
        "llmstats_match_confidence",
        "page_url",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for rank, record in enumerate(records, 1):
            model = record["model"]
            quality = record["quality"]
            ls = quality["llmstats"]
            caps = record["agent_readiness"]["models_dev"]
            for route in record["free_routes"]:
                writer.writerow(
                    {
                        "rank": rank,
                        "model_name": model.get("name"),
                        "llmstats_id": model.get("llmstats_id"),
                        "organization": model.get("organization"),
                        "tier": quality.get("tier"),
                        "composite": ls.get("general"),
                        "reasoning": ls.get("reasoning"),
                        "coding": ls.get("coding"),
                        "agentic": ls.get("agentic"),
                        "score_source": quality.get("score_source"),
                        "score_scale": quality.get("score_scale"),
                        "context": caps.get("context"),
                        "tool_call": caps.get("tool_call"),
                        "reasoning_flag": caps.get("reasoning"),
                        "release_date": caps.get("release_date"),
                        "provider": route.get("provider"),
                        "route_model": route.get("route_model"),
                        "route_source": route.get("source"),
                        "api_base": route.get("api_base"),
                        "verification": route.get("verification"),
                        "verified_free": route.get("verified_free"),
                        "requires_provider_key": route.get("requires_provider_key"),
                        "stale_mirror": route.get("stale_mirror"),
                        "llmstats_match_confidence": route.get(
                            "llmstats_match_confidence"
                        ),
                        "page_url": model.get("page_url"),
                    }
                )


def _provider_labels(routes: list[dict[str, Any]]) -> list[str]:
    """Provider labels for one model, marking claims the provider does not confirm."""
    labels: list[str] = []
    for route in routes:
        label = route_provider_label(route)
        if not label:
            continue
        if route.get("requires_provider_key"):
            label += "*"
        elif route.get("stale_mirror"):
            label += "†"
        labels.append(label)
    return list(dict.fromkeys(labels))


def _print_verification_footnote(route_groups: list[list[dict[str, Any]]]) -> None:
    routes = [route for group in route_groups for route in group]
    if any(route.get("requires_provider_key") for route in routes):
        print("* free-tier claim: your provider key is used and usage may bill at list price.")
    if any(route.get("stale_mirror") for route in routes):
        print("† mirror-listed: a third-party mirror claims zero cost; the provider does not confirm it.")


def _fit_labels(labels: list[str], width: int) -> str:
    """Fit provider labels into `width` characters, keeping markers attached.

    A `*`/`†` belongs to the provider it follows, so truncation only ever shortens
    the last label that still fits and keeps that label's own marker.
    """
    out = ""
    for label in labels:
        candidate = f"{out}/{label}" if out else label
        if len(candidate) <= width:
            out = candidate
            continue
        marker = "".join(m for m in ("*", "†") if m in label)
        room = width - len(out) - (1 if out else 0) - len(marker)
        if room <= 0:
            if not out:
                # Never return an empty column: the first label is simply cut.
                out = (marker + label.rstrip("*†"))[:width]
            break
        prefix = f"{out}/" if out else ""
        out = f"{prefix}{label.rstrip('*†')[:room]}{marker}"
        break
    return out


def print_unbenchmarked(
    records: list[dict[str, Any]],
    limit: int = 12,
    min_intelligence: float = DEFAULT_MIN_INTELLIGENCE,
) -> None:
    if not records:
        return

    print(
        f"\nFree but unbenchmarked ({len(records)} agent-ready model(s) with no AA score; "
        f"floor {min_intelligence:g} applies to known scores; unscored models are included "
        "only with --include-unscored):\n"
    )
    header = f"{'#':>2}  {'Model':30} {'Providers':18} {'Context':>9} {'Released':>10} {'Stealth':>7}  Status"
    print(header)
    print("-" * len(header))
    for i, record in enumerate(records[:limit], 1):
        model = record["model"]
        caps = record["capabilities"]
        bench = record["benchmark"]
        labels = _provider_labels(record.get("free_routes", [])) or record["providers"]
        providers = _fit_labels(labels, 18)
        context = caps.get("context")
        context_text = f"{context:,}" if isinstance(context, int) else "-"
        status = "no AA entry" if bench["status"] == "no-aa-entry" else "AA unscored"
        print(
            f"{i:>2}  {clean_text(model.get('name'))[:30]:30} {providers:18} {context_text:>9} "
            f"{clean_text(caps.get('release_date'))[:10]:>10} {'yes' if record['stealth'] else '-':>7}  {status}"
        )
    if len(records) > limit:
        print(f"    ... {len(records) - limit} more in the unbenchmarked JSON")
    _print_verification_footnote([record.get("free_routes", []) for record in records])


def print_stealthmark(
    records: list[dict[str, Any]],
    limit: int = 12,
    min_intelligence: float = DEFAULT_MIN_INTELLIGENCE,
) -> None:
    """Separate StealthMark-scored section: a different benchmark, not AA."""
    if not records:
        return

    print(
        f"\nStealthMark-scored free models ({len(records)}; floor {min_intelligence:g}; "
        "visual/planning proxy, not AA):\n"
    )
    header = (
        f"{'#':>2}  {'Model':30} {'Providers':18} {'General':>7} {'Visual':>7} "
        f"{'Routes':>6} {'Ready':>6}"
    )
    print(header)
    print("-" * len(header))
    for i, record in enumerate(records[:limit], 1):
        model = record["model"]
        quality = record["quality"]
        sm = quality["stealthmark"]
        labels = _provider_labels(record.get("free_routes", [])) or ["unknown"]
        providers = _fit_labels(labels, 18)
        ready = record["agent_readiness"].get("status")
        ready_text = "yes" if ready is True else ("no" if ready is False else "?")

        def fmt(value: Any) -> str:
            return f"{value:.1f}" if isinstance(value, (int, float)) else "-"

        print(
            f"{i:>2}  {clean_text(model.get('name'))[:30]:30} {providers:18} "
            f"{fmt(sm.get('general')):>7} {fmt(sm.get('visual')):>7} "
            f"{record['route_count']:>6} {ready_text:>6}"
        )
    if len(records) > limit:
        print(f"    ... {len(records) - limit} more in the StealthMark JSON")
    print(
        "Note: StealthMark General is an intelligence proxy from a visual/planning "
        "benchmark; it is not an Artificial Analysis coding score."
    )
    _print_verification_footnote([record.get("free_routes", []) for record in records])


def print_published(
    records: list[dict[str, Any]],
    limit: int = 12,
    min_published: float = 0.0,
) -> None:
    """Separate published-composite section: a different scale, not AA."""
    if not records:
        return

    print(
        f"\nPublished composite ({len(records)}; floor {min_published:g}; "
        "llm-stats scale, not AA):\n"
    )
    header = (
        f"{'#':>2}  {'Model':30} {'Providers':18} {'Comp':>6} {'Code':>6} "
        f"{'Agent':>6} {'Routes':>6} {'Ready':>6}"
    )
    print(header)
    print("-" * len(header))
    for i, record in enumerate(records[:limit], 1):
        model = record["model"]
        quality = record["quality"]
        ls = quality["llmstats"]
        labels = _provider_labels(record.get("free_routes", [])) or ["unknown"]
        providers = _fit_labels(labels, 18)
        ready = record["agent_readiness"].get("status")
        ready_text = "yes" if ready is True else ("no" if ready is False else "?")

        def fmt(value: Any) -> str:
            return f"{value:.1f}" if isinstance(value, (int, float)) else "-"

        print(
            f"{i:>2}  {clean_text(model.get('name'))[:30]:30} {providers:18} "
            f"{fmt(ls.get('general')):>6} {fmt(ls.get('coding')):>6} "
            f"{fmt(ls.get('agentic')):>6} {record['route_count']:>6} {ready_text:>6}"
        )
    if len(records) > limit:
        print(f"    ... {len(records) - limit} more in the published JSON")
    print(
        "Note: the llm-stats composite is a relative ranking on its own scale, not an "
        "Artificial Analysis intelligence index. Its rows are provider-reported."
    )
    _print_verification_footnote([record.get("free_routes", []) for record in records])


def print_console(
    selected: list[dict[str, Any]],
    unmatched_count: int,
    free_tier_skipped: int = 0,
    mirror_skipped: int = 0,
    min_intelligence: float = DEFAULT_MIN_INTELLIGENCE,
) -> None:
    print(
        f"\nIntelligence floor: {min_intelligence:g} "
        "(known scores below are hidden; unscored models require --include-unscored)."
    )
    if not selected:
        print("No models passed the current filters.")
        print(
            "Hint: try --all (list every AA-scored free model) or change "
            "--min-intelligence/--min-specialized."
        )
        if free_tier_skipped:
            print(
                f"Note: {free_tier_skipped} F24 free-tier route(s) were skipped because they require "
                "your own provider key and are not proven zero-priced."
            )
        if mirror_skipped:
            print(
                f"Note: {mirror_skipped} mirror-listed route(s) were skipped because models.dev lags "
                "the provider catalogue."
            )
        return

    print("\nSelected free models:\n")
    header = f"{'#':>2}  {'Model':34} {'Providers':24} {'Score':>7} {'Intel':>7} {'Code':>7} {'Agent':>7} {'Routes':>6} {'Ready':>6}"
    print(header)
    print("-" * len(header))
    for i, rec in enumerate(selected, 1):
        model = clean_text(rec["model"].get("name"))[:34]
        providers = _fit_labels(_provider_labels(rec.get("free_routes", [])), 24)
        q = rec["quality"]
        ready = rec["agent_readiness"].get("status")
        ready_text = "yes" if ready is True else ("no" if ready is False else "?")

        def fmt(v: Any) -> str:
            return f"{v:.1f}" if isinstance(v, (int, float)) else "-"

        print(
            f"{i:>2}  {model:34} {providers:24} {fmt(q.get('capability_score')):>7} "
            f"{fmt(q.get('intelligence')):>7} {fmt(q.get('coding')):>7} "
            f"{fmt(q.get('agentic')):>7} {rec['route_count']:>6} {ready_text:>6}"
        )

    if unmatched_count:
        print(f"\n{unmatched_count} free route(s) could not be confidently matched to Artificial Analysis; see unmatched JSON.")
    if free_tier_skipped:
        print(
            f"{free_tier_skipped} F24 free-tier route(s) skipped: they need your own provider key and are "
            "not proven zero-priced (--include-free-tier to include them)."
        )
    if mirror_skipped:
        print(
            f"{mirror_skipped} mirror-listed route(s) skipped: models.dev lags the provider "
            "(--include-free-tier to include them)."
        )
    _print_verification_footnote([rec.get("free_routes", []) for rec in selected])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Find strong, currently-free LLM routes using F24 + Artificial Analysis + models.dev."
    )
    p.add_argument("--aa-api-key", default=os.getenv("AA_API_KEY"), help="Artificial Analysis API key (default: AA_API_KEY env var)")
    p.add_argument("--aa-url", default=DEFAULT_AA_URL, help="Artificial Analysis free models endpoint")
    p.add_argument("--f24-url", default=DEFAULT_F24_URL, help="F24 LiteLLM YAML URL")
    p.add_argument("--f24-file", help="Read F24/LiteLLM YAML from a local file instead of --f24-url")
    p.add_argument("--models-dev-url", default=DEFAULT_MODELS_DEV_URL, help="models.dev provider-agnostic JSON URL")
    p.add_argument("--skip-models-dev", action="store_true", help="Do not fetch models.dev capability metadata")
    p.add_argument(
        "--source",
        choices=SOURCE_CHOICES,
        default="all",
        help="Where free routes are discovered from: all, f24, openrouter, or opencode (default: all)",
    )
    p.add_argument("--source-url", help="Override the discovery URL for a single --source (not usable with --source all)")
    p.add_argument(
        "--include-free-tier",
        action="store_true",
        help=(
            "Also use routes whose free price the provider does not confirm: F24 free-tier routes "
            "(your own vendor key, may bill at list price) and stale models.dev mirror entries. "
            "Excluded by default; included routes stay labelled."
        ),
    )
    p.add_argument("--provider", help="Only keep routes whose provider/host/route id contains this substring (case-insensitive)")
    p.add_argument("--sort-by", choices=("score", "coding", "intelligence", "agentic"), default="score", help="Ordering of selected models (default: score)")

    p.add_argument(
        "--min-intelligence",
        type=float,
        default=DEFAULT_MIN_INTELLIGENCE,
        help=(
            "Intelligence floor for every scored model in the report (default: "
            f"{DEFAULT_MIN_INTELLIGENCE:g}). Models with a known score below this are hidden."
        ),
    )
    p.add_argument(
        "--include-unscored",
        action="store_true",
        help=(
            "Also list free models with no intelligence score in the unbenchmarked "
            "section. Off by default: a model with no score is not evidence of the floor."
        ),
    )
    p.add_argument("--min-specialized", type=float, default=25.0, help="Absolute floor for max(Coding, Agentic); ignored when AA serves no coding/agentic scores")
    p.add_argument(
        "--intel-percentile",
        type=float,
        default=None,
        help=(
            "Optional adaptive Intelligence percentile cutoff across scored free candidates "
            "(e.g. 50 = median). Disabled by default: with a handful of verified-free routes a "
            "percentile silently drops good models."
        ),
    )
    p.add_argument(
        "--specialized-percentile",
        type=float,
        default=None,
        help=(
            "Optional adaptive max(Coding, Agentic) percentile cutoff; ignored when AA serves no "
            "coding/agentic scores. Disabled by default."
        ),
    )
    p.add_argument("--no-adaptive", action="store_true", help="Disable percentile/adaptive filtering; use absolute floors only")
    p.add_argument("--all", action="store_true", help="List every AA-scored free model; disables quality floors and adaptive cutoffs")

    p.add_argument("--useful-intelligence", type=float, default=22.0, help="Intelligence floor for the 'useful' tier")
    p.add_argument("--useful-specialized", type=float, default=20.0, help="max(Coding, Agentic) floor for the 'useful' tier")
    p.add_argument("--include-useful", action="store_true", help="Include 'useful' (not just strong) models in selected output")

    p.add_argument("--min-context", type=int, default=65536, help="Minimum context for Hermes-style agent readiness")
    p.add_argument("--require-agent-ready", action="store_true", help="Require models.dev confirmation of tool calling + context")

    p.add_argument("--match-threshold", type=float, default=0.82, help="Minimum F24 -> Artificial Analysis model match confidence")
    p.add_argument("--models-dev-match-threshold", type=float, default=0.82, help="Minimum benchmark model -> models.dev match confidence")
    p.add_argument(
        "--stealthmark-url",
        default=DEFAULT_STEALTH_URL,
        help="StealthMark public ratings feed (proxy scores for stealth models AA does not list)",
    )
    p.add_argument(
        "--skip-stealthmark",
        action="store_true",
        help="Do not fetch StealthMark; stealth models stay in the unbenchmarked section",
    )
    p.add_argument(
        "--stealthmark-match-threshold",
        type=float,
        default=0.82,
        help="Minimum route -> StealthMark model match confidence",
    )
    p.add_argument(
        "--llmstats-url",
        default=DEFAULT_LLMSTATS_URL,
        help="llm-stats leaderboard page (published composite for models AA has not indexed)",
    )
    p.add_argument(
        "--skip-llmstats",
        action="store_true",
        help="Do not fetch llm-stats; unscored models stay in the unbenchmarked section",
    )
    p.add_argument(
        "--llmstats-match-threshold",
        type=float,
        default=0.82,
        help="Minimum route -> llm-stats model match confidence",
    )
    p.add_argument(
        "--min-published",
        type=float,
        default=0.0,
        help=(
            "Floor for the llm-stats published composite (default: 0, no floor). "
            "It is a different scale from the AA intelligence floor, so it is never "
            "compared with --min-intelligence."
        ),
    )
    p.add_argument("--output-prefix", default="free_models", help="Output path prefix")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--self-test", action="store_true", help="Run built-in matching/filter tests without network access")
    return p


def run_self_test() -> int:
    # Test the important MiniMax normalization path plus parameter-size safety.
    f24_alias = alias_variants("openrouter/minimax/minimax-m3.1-flash:free", "MiniMax M3.1 Flash")
    aa = {
        "id": "test-mm",
        "slug": "minimax-m3-1-flash",
        "name": "MiniMax M3.1 Flash",
        "model_creator": {"name": "MiniMax"},
        "evaluations": {
            "artificial_analysis_intelligence_index": 43.0,
            "artificial_analysis_coding_index": 40.0,
            "artificial_analysis_agentic_index": 45.0,
        },
    }
    other = {
        "id": "test-other",
        "slug": "gpt-oss-20b",
        "name": "gpt-oss-20B (high)",
        "model_creator": {"name": "OpenAI"},
        "evaluations": {},
    }
    match = match_item(f24_alias, [aa, other], aa_aliases, min_confidence=0.82)
    assert match.item and match.item["id"] == "test-mm", match

    score, method, *_ = compare_alias_sets(
        alias_variants("gpt-oss-120b"),
        alias_variants("gpt-oss-20b"),
    )
    assert score == 0.0 and method == "parameter-size-mismatch", (score, method)

    assert percentile_threshold([1, 2, 3, 4, 5], 75) == 4.0
    cs = capability_score(43, 40, 45)
    assert cs is not None and 42 < cs < 44.5, cs

    # AA free tier: a canonical entry can be unscored while a dated snapshot
    # carries the score, and effort variants tie with the canonical name because
    # normalize_text erases "(Non-reasoning)".
    canonical = {
        "id": "ds-canonical",
        "slug": "deepseek-v4-1-flash",
        "name": "DeepSeek V4.1 Flash (Max)",
        "model_creator": {"name": "DeepSeek"},
        "evaluations": {"artificial_analysis_intelligence_index": None},
    }
    effort_variant = {
        "id": "ds-effort",
        "slug": "deepseek-v4-1-flash-non-reasoning",
        "name": "DeepSeek V4.1 Flash (Non-reasoning)",
        "model_creator": {"name": "DeepSeek"},
        "evaluations": {"artificial_analysis_intelligence_index": None},
    }
    snapshot = {
        "id": "ds-snapshot",
        "slug": "deepseek-v4-1-flash-1017",
        "name": "DeepSeek V4.1 Flash 1017 (Max)",
        "model_creator": {"name": "DeepSeek"},
        "evaluations": {"artificial_analysis_intelligence_index": 32.5},
    }
    family_key = variant_base_key(canonical)
    assert family_key == variant_base_key(effort_variant) == variant_base_key(snapshot) == "deepseekv41flash", family_key

    variant_index = build_variant_score_index([canonical, effort_variant, snapshot])
    assert variant_index[family_key]["intelligence"] == 32.5, variant_index
    assert variant_index[family_key]["intelligence_from"] == "deepseek-v4-1-flash-1017", variant_index

    route_aliases = alias_variants("deepseek-v4.1-flash-free", "DeepSeek V4.1 Flash Free")
    pick = match_item(
        route_aliases,
        [effort_variant, snapshot, canonical],
        aa_aliases,
        min_confidence=0.82,
        preference=_aa_canonical_preference,
    )
    assert pick.item and pick.item["id"] == "ds-canonical", pick
    assert _aa_canonical_preference(snapshot) > _aa_canonical_preference(effort_variant)

    # Missing coding/agentic scores must not reject a model on their own.
    quality_args = build_parser().parse_args(["--aa-api-key", "test"])
    tier, selected_by_quality, quality_reasons = classify_quality(
        32.5, None, None, quality_args, {"intelligence": None, "specialized": None}
    )
    assert tier == "strong" and selected_by_quality and not quality_reasons, (tier, quality_reasons)

    # OpenCode publishes prices as a nested cost tree (per token, cache read and
    # cache write), sometimes with long-context overrides.
    assert prices_all_zero(
        {"text": {"input": {"price": 0, "cache": {"read": 0, "write": None}}, "output": {"price": 0}}}
    )
    assert not prices_all_zero(
        {"text": {"input": {"price": 0.3, "cache": {"read": 0, "write": None}}, "output": {"price": 1.2}}}
    )
    assert not prices_all_zero(
        {
            "text": {
                "input": {"price": 0, "overrides": [{"when": {"context": {"gt": 100000}}, "price": 0.5}]},
                "output": {"price": 0},
            }
        }
    )
    oc_meta = _opencode_metadata(
        {
            "name": "Pulse Alpha Free",
            "limit": {"context": 1048576, "output": 131072},
            "capabilities": {"tools": {"supported": True}, "reasoning": {"supported": True}},
            "modalities": {"input": ["text"], "output": ["text"]},
            "release_date": "2026-11-02",
        },
        "pulse-alpha-free",
    )
    assert oc_meta["tool_call"] is True and oc_meta["reasoning"] is True, oc_meta
    assert oc_meta["limit"] == {"context": 1048576, "output": 131072}, oc_meta

    assert prices_all_zero(
        {"text": {"input": {"price": 0, "cache": {"read": 0}}, "output": {"price": 0}}},
        required=(("text", "input", "price"), ("text", "output", "price")),
    )
    assert not prices_all_zero(
        {"text": {"input": {"price": 0, "cache": {"read": 0}}}},
        required=(("text", "input", "price"), ("text", "output", "price")),
    )
    # A zero token price is not proof of a free model: media models bill per song
    # or clip and only state it in prose, which the pricing object omits.
    assert _openrouter_free_rejection(
        {
            "id": "google/lyria-3-pro-preview",
            "description": "Full-length songs are priced at $0.08 per song.",
            "architecture": {"output_modalities": ["text", "audio"]},
        }
    )
    assert _openrouter_free_rejection(
        {
            "id": "x/paid-in-prose",
            "description": "Usage is $2 per million tokens.",
            "architecture": {"output_modalities": ["text"]},
        }
    )
    assert (
        _openrouter_free_rejection(
            {
                "id": "stealth/crouton-alpha",
                "description": "A lightweight model for coding and agentic workflows.",
                "architecture": {"output_modalities": ["text"]},
            }
        )
        is None
    )
    # Without a declared output modality the token pricing may not cover what the
    # model actually bills, so it is not accepted as proof.
    assert _openrouter_free_rejection({"id": "x/no-metadata"})
    assert _openrouter_free_rejection({"id": "x/unknown-out", "architecture": {}})
    # Only an explicitly text-only output set is covered by token pricing.
    assert _openrouter_free_rejection(
        {"id": "x/music", "architecture": {"output_modalities": ["music"]}}
    )
    assert _openrouter_free_rejection(
        {"id": "x/padded", "architecture": {"output_modalities": ["text", "image "]}}
    )
    # Provider markers must survive column truncation.
    assert _fit_labels(["river*", "tokenharbor*", "opencode"], 18).endswith("*"), _fit_labels(
        ["river*", "tokenharbor*", "opencode"], 18
    )
    assert _fit_labels(["opencode†", "openrouter"], 4).endswith("†")
    for labels, width in (
        (["nemotron-3.5-super-vl-free"], 18),
        (["x*"], 1),
        (["river*", "tokenharbor*"], 18),
        (["opencode", "river*"], 10),
    ):
        fitted = _fit_labels(labels, width)
        assert fitted and len(fitted) <= width, (labels, width, fitted)

    # An absent or partial pricing object is not proof of a free model.
    assert not prices_all_zero({})
    assert not prices_all_zero(None)
    assert not prices_all_zero({"prompt": "0"}, required=(("prompt",), ("completion",)))
    assert not prices_all_zero(
        {"prompt": "0", "completion": "0"}, required=(("prompt",), ("completion", "price"))
    )
    assert prices_all_zero(
        {"prompt": "0", "completion": "0", "request": "0"},
        required=(("prompt",), ("completion",)),
    )
    # A price hidden behind a threshold-shaped wrapper is still a price.
    assert not prices_all_zero({"input": 0, "tier": {"price": 5}})
    # Tiny exponents must not underflow to zero.
    assert not price_zero("1e-999")
    assert not prices_all_zero({"prompt": "0", "completion": "0", "input": "1e-999"})
    # Variable/unknown pricing (-1) is not free.
    assert not prices_all_zero({"prompt": "-1", "completion": "0"})
    # A boolean or an unparseable value is not a published zero.
    assert not prices_all_zero({"prompt": "0", "completion": "0", "request": True})
    assert not prices_all_zero({"prompt": "0", "completion": "0", "image": "unknown"})
    # JSON fractions are decoded as Decimal for catalogue payloads, so a tiny
    # published price cannot underflow to 0.0 before the check runs.
    assert not prices_all_zero(
        json.loads('{"prompt": 1e-999, "completion": 0}', parse_float=Decimal),
        required=(("prompt",), ("completion",)),
    )
    assert prices_all_zero(
        json.loads('{"prompt": 0, "completion": 0}', parse_float=Decimal),
        required=(("prompt",), ("completion",)),
    )

    # The adaptive percentile is opt-in: with a handful of verified-free scored
    # routes a median cutoff drops the third-best model for no good reason.
    default_args = build_parser().parse_args(["--aa-api-key", "test"])
    assert default_args.intel_percentile is None and default_args.specialized_percentile is None
    tier, picked, reasons = classify_quality(
        26.7, None, None, default_args, {"intelligence": 29.0, "specialized": None}
    )
    assert tier == "strong" and picked and not reasons, (tier, reasons)

    # Free-ness must be proven per billable dimension, not just per token.
    assert prices_all_zero({"prompt": "0", "completion": "0"})
    assert prices_all_zero({"input": 0, "output": 0, "cache_read": 0})
    assert prices_all_zero({"input": 0, "tiers": [{"input": 0, "tier": {"type": "context", "size": 200000}}]})
    assert not prices_all_zero({"prompt": "0", "completion": "0", "request": "0.0001"})
    assert not prices_all_zero({"prompt": "0", "completion": "0", "image": "0.04"})
    assert not prices_all_zero({"input": 0, "output": 0, "cache_write": 0.125})
    assert not prices_all_zero({"input": 0, "tiers": [{"input": 2, "tier": {"type": "context", "size": 200000}}]})
    assert not prices_all_zero({"prompt": "0", "completion": "0", "overrides": [{"min_prompt_tokens": 272000, "prompt": "0.000002"}]})

    # F24 routes are free-tier assertions against vendor endpoints, never
    # verified zero-priced, so they carry their own verification label.
    f24_doc = {
        "model_list": [
            {
                "model_name": "openai/models/gemini-3.8-flash",
                "litellm_params": {
                    "model": "openai/models/gemini-3.8-flash",
                    "api_base": "https://generativelanguage.googleapis.com/v1beta/openai",
                    "api_key": "os.environ/GEMINI_API_KEY",
                },
                "model_info": {"f24_provider": "google", "access_groups": ["litellm-free"]},
            }
        ]
    }
    f24_routes = extract_f24_routes(f24_doc)
    assert len(f24_routes) == 1, f24_routes
    assert f24_routes[0].verification == "free-tier", f24_routes[0]
    assert route_free_fields(f24_routes[0])["verified_free"] is False
    assert route_free_fields(f24_routes[0])["requires_provider_key"] is True

    # A stale mirror entry claims zero cost without provider confirmation, so it
    # must never be labelled verified free.
    mirror_route = FreeRoute(
        provider="opencode",
        route_model="muse-spark-1.3-contributor-free",
        model_name="Muse Spark 1.3 Free",
        api_base=None,
        aliases=["muse-spark-1.3-contributor-free"],
        raw_index=0,
        source="opencode",
        verification="mirror-listed",
    )
    mirror_fields = route_free_fields(mirror_route)
    assert mirror_fields["verified_free"] is False, mirror_fields
    assert mirror_fields["stale_mirror"] is True, mirror_fields
    assert mirror_fields["requires_provider_key"] is False, mirror_fields

    # Stealth models have no AA entry at all; they must land in the separate
    # unbenchmarked list instead of disappearing into the unmatched pile.
    pulse = FreeRoute(
        provider="opencode",
        route_model="pulse-alpha-free",
        model_name="Pulse Alpha Free",
        api_base=None,
        aliases=alias_variants("pulse-alpha-free", "Pulse Alpha Free"),
        raw_index=0,
        source="opencode",
        metadata={
            "id": "pulse-alpha-free",
            "name": "Pulse Alpha Free",
            "limit": {"context": 1048576},
            "tool_call": True,
            "reasoning": True,
            "modalities": {"input": ["text", "image"], "output": ["text"]},
            "description": "Stealth reasoning model with text and image input and tool use",
            "release_date": "2026-11-02",
        },
    )
    guardrail = FreeRoute(
        provider="openrouter",
        route_model="nvidia/nemotron-3.5-content-safety:free",
        model_name="NVIDIA: Nemotron 3.5 Content Safety (free)",
        api_base=None,
        aliases=alias_variants("nvidia/nemotron-3.5-content-safety:free"),
        raw_index=1,
        source="openrouter",
        metadata={
            "id": "nvidia/nemotron-3.5-content-safety:free",
            "limit": {"context": 128000},
            "tool_call": False,
            "reasoning": True,
            "modalities": {"input": ["text"], "output": ["text"]},
            "description": "Guardrail model",
        },
    )
    router = FreeRoute(
        provider="openrouter",
        route_model="openrouter/free",
        model_name="Free Models Router",
        api_base=None,
        aliases=alias_variants("openrouter/free", "Free Models Router"),
        raw_index=2,
        source="openrouter",
        metadata={
            "id": "openrouter/free",
            "limit": {"context": 200000},
            "tool_call": True,
            "reasoning": True,
            "modalities": {"input": ["text"], "output": ["text"]},
            "description": "A router that selects free models at random",
        },
    )
    unbenchmarked = build_unbenchmarked(
        [pulse, guardrail, router], {}, set(), [], quality_args, include_unscored=True
    )
    assert len(unbenchmarked) == 1, unbenchmarked
    assert unbenchmarked[0]["stealth"] is True
    assert unbenchmarked[0]["benchmark"]["status"] == "no-aa-entry"
    assert unbenchmarked[0]["free_routes"][0]["route_model"] == "pulse-alpha-free"
    # The default report hides a model with no score at all.
    assert build_unbenchmarked([pulse], {}, set(), [], quality_args) == []

    # A known proxy score below the floor hides that route from the unbenchmarked
    # section; an unscored route is not "below" the floor and stays visible.
    low = FreeRoute(
        provider="opencode",
        route_model="low-alpha-free",
        model_name="Low Alpha Free",
        api_base=None,
        aliases=alias_variants("low-alpha-free", "Low Alpha Free"),
        raw_index=0,
        source="opencode",
        metadata={
            "id": "low-alpha-free",
            "name": "Low Alpha Free",
            "limit": {"context": 200000},
            "tool_call": True,
            "reasoning": True,
            "modalities": {"input": ["text"], "output": ["text"]},
            "description": "Stealth model with a known low proxy score",
        },
    )
    floored = build_unbenchmarked(
        [pulse, low],
        {},
        set(),
        [],
        quality_args,
        intelligence_by_route={("opencode", "low-alpha-free"): 10.0},
        include_unscored=True,
    )
    assert len(floored) == 1, floored
    assert floored[0]["free_routes"][0]["route_model"] == "pulse-alpha-free", floored

    # StealthMark is a separate benchmark with its own feed shape. A partial run
    # is a lower bound and must not be promoted as a complete score.
    stealth_doc = {
        "models": [
            {
                "id": "crouton-alpha",
                "name": "Crouton Alpha",
                "identityStatus": "revealed",
                "scoreSummaries": {
                    "": {
                        "score": 48,
                        "countedResults": 8,
                        "prompts": 8,
                        "capabilities": {
                            "general": {"score": 43.5},
                            "visual": {"score": 56.0},
                        },
                    }
                },
            },
            {
                "id": "pixel-canary",
                "name": "Pixel Canary",
                "scoreSummaries": {
                    "": {
                        "score": 49.5,
                        "countedResults": 7,
                        "prompts": 8,
                        "capabilities": {
                            "general": {"score": 49.5},
                            "visual": {"score": 50.0},
                        },
                    }
                },
            },
        ]
    }
    stealth_entries_test = parse_stealthmark(stealth_doc)
    assert len(stealth_entries_test) == 1, stealth_entries_test
    assert stealth_entries_test[0]["general"] == 43.5, stealth_entries_test

    crouton_route = FreeRoute(
        provider="opencode",
        route_model="crouton-alpha-free",
        model_name="Crouton Alpha Free",
        api_base=None,
        aliases=alias_variants("crouton-alpha-free", "Crouton Alpha Free"),
        raw_index=0,
        source="opencode",
        metadata={
            "id": "crouton-alpha-free",
            "name": "Crouton Alpha Free",
            "limit": {"context": 200000},
            "tool_call": True,
            "reasoning": True,
            "modalities": {"input": ["text"], "output": ["text"]},
        },
    )
    crouton_match, crouton_conf, _method = match_stealthmark(
        crouton_route, stealth_entries_test, 0.82
    )
    assert crouton_match and crouton_match["id"] == "crouton-alpha", (
        crouton_conf,
        crouton_match,
    )
    # An AA-covered route must not be re-scored by StealthMark.
    assert (
        stealthmark_only_routes(
            [crouton_route],
            {_route_identity(crouton_route)},
            stealth_entries_test,
            0.82,
        )
        == []
    )
    stealth_scored = stealthmark_only_routes(
        [crouton_route], set(), stealth_entries_test, 0.82
    )
    assert len(stealth_scored) == 1, stealth_scored
    stealth_records_test, stealth_info_test = build_stealthmark_records(
        stealth_scored, [], quality_args
    )
    assert len(stealth_records_test) == 1, stealth_records_test
    assert stealth_records_test[0]["quality"]["score_source"] == "stealthmark-proxy"
    assert stealth_records_test[0]["quality"]["intelligence"] == 43.5
    assert stealth_info_test["models_promoted"] == 1, stealth_info_test

    weak_route = dict(stealth_scored[0])
    weak_route["stealthmark"] = dict(
        stealth_entries_test[0], id="weak-alpha", name="Weak Alpha", general=10.0
    )
    weak_records, weak_info = build_stealthmark_records([weak_route], [], quality_args)
    assert weak_records == [] and weak_info["models_below_floor"] == 1, (
        weak_records,
        weak_info,
    )

    # llm-stats: the leaderboard ships as a React flight stream, so the parser has
    # to undo the JS string escapes before the row objects exist.
    flight = (
        'self.__next_f.push([1,"33:[\\"$\\",\\"$L35\\",null,{\\"initialData\\":[{'
        '\\"model_id\\":\\"ernie-5.5-flash-preview\\",\\"name\\":\\"ERNIE 5.5 Flash Preview\\",'
        '\\"organization\\":\\"Baidu\\",\\"index_general\\":52.33,\\"index_code\\":37.9,'
        '\\"index_agents\\":38.5,\\"index_reasoning\\":50.71,\\"release_date\\":\\"2026-11-11\\"}]'
        '}"])'
    )
    parsed_rows = parse_llmstats_models(flight)
    assert len(parsed_rows) == 1, parsed_rows
    assert parsed_rows[0]["model_id"] == "ernie-5.5-flash-preview", parsed_rows
    ernie_entry = llmstats_entry(parsed_rows[0])
    assert ernie_entry is not None
    assert ernie_entry["general"] == 52.33, ernie_entry
    assert ernie_entry["coding"] == 37.9, ernie_entry
    assert ernie_entry["page_url"].endswith("/models/ernie-5.5-flash-preview"), ernie_entry
    # A model with no overall index is not scored at all.
    assert llmstats_entry({"model_id": "x", "index_reasoning": 10}) is None

    ernie_route = FreeRoute(
        provider="openrouter",
        route_model="baidu/ernie-5.5-flash-preview:free",
        model_name="Baidu: ERNIE-5.5-Flash-Preview (free)",
        api_base=None,
        aliases=alias_variants("baidu/ernie-5.5-flash-preview:free", "ERNIE 5.5 Flash Preview"),
        raw_index=0,
        source="openrouter",
        metadata={
            "context": 1024000,
            "tool_call": True,
            "reasoning": True,
            "modalities": {"output": ["text"]},
        },
    )
    ls_entries = [ernie_entry]
    assert match_llmstats(ernie_route, ls_entries, 0.82)[0] is ernie_entry
    # A route AA already covers must not be claimed by the published lane.
    assert published_only_routes([ernie_route], {_route_identity(ernie_route)}, ls_entries, 0.82) == []
    published_scored = published_only_routes([ernie_route], set(), ls_entries, 0.82)
    assert len(published_scored) == 1, published_scored
    published_records_test, published_info_test = build_published_records(
        published_scored, [], quality_args
    )
    assert len(published_records_test) == 1, published_records_test
    assert published_records_test[0]["quality"]["score_source"] == "published-composite"
    # The AA column stays empty: this is a different scale.
    assert published_records_test[0]["quality"]["intelligence"] is None
    assert published_records_test[0]["quality"]["capability_score"] == 52.33
    assert published_info_test["models_promoted"] == 1, published_info_test
    floor_args = argparse.Namespace(**{**vars(quality_args), "min_published": 60.0})
    floored_records, floored_info = build_published_records(published_scored, [], floor_args)
    assert floored_records == [] and floored_info["models_below_floor"] == 1, (
        floored_records,
        floored_info,
    )

    print("Self-test passed.")
    return 0


SOURCE_CHOICES = ("all", "f24", "openrouter", "opencode")


def collect_routes(args: argparse.Namespace) -> tuple[list[FreeRoute], dict[str, int], list[str]]:
    """Gather free routes from every requested source.

    A single feed being down should not hide models available elsewhere, so
    per-source failures are collected and reported instead of aborting.
    """
    sources = SOURCE_CHOICES[1:] if args.source == "all" else (args.source,)
    routes: list[FreeRoute] = []
    counts: dict[str, int] = {}
    errors: list[str] = []

    for source in sources:
        try:
            if source == "f24":
                found = fetch_f24(args)
            elif source == "openrouter":
                found = fetch_openrouter_routes(args.source_url or DEFAULT_OPENROUTER_URL)
            else:
                found = fetch_opencode_routes(args.source_url or DEFAULT_OPENCODE_URL)
        except Exception as exc:
            message = f"{source}: {exc}"
            errors.append(message)
            eprint(f"Warning: free-route source unavailable ({message})")
            continue
        if args.verbose:
            eprint(f"  {source}: {len(found)} free route(s)")
        counts[source] = len(found)
        routes.extend(found)

    # One physical route is provider + model + endpoint; two feeds listing the
    # same trio collapse, but two endpoints serving the same model id do not.
    unique: dict[tuple[str, str, str], FreeRoute] = {}
    for route in routes:
        unique[(route.provider.lower(), route.route_model, (route.api_base or "").lower())] = route
    return list(unique.values()), counts, errors


def require_aa_api_key(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    """Return the AA key, prompting interactively only when a TTY is available."""
    if args.aa_api_key:
        return args.aa_api_key
    if sys.stdin.isatty():
        try:
            key = getpass.getpass(
                "AA_API_KEY is not set. Paste your Artificial Analysis API key "
                "(input hidden), or press Enter to abort: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            key = ""
        if key:
            return key
    parser.error(
        "Artificial Analysis API key required. Set AA_API_KEY, pass --aa-api-key, "
        "or run interactively to be prompted."
    )
    raise AssertionError("unreachable")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    args.aa_api_key = require_aa_api_key(parser, args)

    if args.source_url and args.source == "all":
        parser.error("--source-url requires a single --source (f24, openrouter, or opencode)")

    if args.all:
        args.no_adaptive = True
        args.min_intelligence = 0.0
        args.min_specialized = 0.0

    start = time.time()

    if args.verbose:
        eprint(f"Fetching free routes from {args.source} ...")
    routes, routes_by_source, source_errors = collect_routes(args)
    if args.provider:
        routes = filter_routes(routes, args.provider)

    # Only a provider-published zero price may be presented as free. F24 routes
    # point at vendor endpoints with the operator's own key and assert a free
    # tier; mirror routes come from a stale third-party copy. Both are excluded
    # unless the operator opts in, and both stay labelled when included.
    unverified = [r for r in routes if r.verification != "zero-priced"]
    free_tier_skipped = sum(1 for r in unverified if r.verification == "free-tier")
    mirror_skipped = sum(1 for r in unverified if r.verification == "mirror-listed")
    if not args.include_free_tier and unverified:
        routes = [r for r in routes if r.verification == "zero-priced"]
        if free_tier_skipped:
            eprint(
                f"Warning: skipped {free_tier_skipped} F24 free-tier route(s): they need your own provider "
                "key and are not proven zero-priced. Re-run with --include-free-tier to use them."
            )
        if mirror_skipped:
            eprint(
                f"Warning: skipped {mirror_skipped} mirror-listed route(s): models.dev lags the provider. "
                "Use the provider catalogue or --include-free-tier to include them."
            )
    if not routes:
        if free_tier_skipped or mirror_skipped:
            raise RuntimeError(
                "Only routes whose zero price the provider does not confirm were found "
                f"({free_tier_skipped} free-tier, {mirror_skipped} mirror-listed). "
                "Re-run with --include-free-tier to include them."
            )
        raise RuntimeError("No free routes found for the current --source/--provider")

    aa_models, index_version = fetch_aa_models(
        args.aa_api_key,
        args.aa_url,
        verbose=args.verbose,
    )
    if not aa_models:
        raise RuntimeError("Artificial Analysis returned no language models")

    models_dev_rows: list[dict[str, Any]] = []
    models_dev_error: str | None = None
    if not args.skip_models_dev:
        try:
            if args.verbose:
                eprint("Fetching models.dev metadata ...")
            md_doc = http_get_json(args.models_dev_url)
            models_dev_rows = flatten_models_dev(md_doc)
            if not models_dev_rows:
                models_dev_error = "models.dev response was readable but no model entries were recognized"
        except Exception as exc:
            models_dev_error = str(exc)
            eprint(f"Warning: models.dev enrichment unavailable: {exc}")

    groups, unmatched = group_routes_by_aa(routes, aa_models, min_confidence=args.match_threshold)

    # StealthMark is best-effort enrichment: it scores stealth models that AA does
    # not list, and a feed failure must not break the AA-scored shortlist.
    stealth_entries: list[dict[str, Any]] = []
    stealth_error: str | None = None
    if not args.skip_stealthmark:
        if args.verbose:
            eprint("Fetching StealthMark ratings ...")
        stealth_entries, stealth_error = fetch_stealthmark(args.stealthmark_url)
        if stealth_error:
            eprint(f"Warning: StealthMark enrichment unavailable: {stealth_error}")

    aa_identities: set[str] = set()
    for group in groups.values():
        for route in group["routes"]:
            aa_identities.add(_route_identity(route))

    # Keep every StealthMark match, including AA-covered models, so a known
    # below-floor score can hide that model from the unbenchmarked section too.
    stealth_scores_by_route: dict[tuple[str, str], float] = {}
    for route in routes:
        entry, _confidence, _method = match_stealthmark(
            route, stealth_entries, args.stealthmark_match_threshold
        )
        general = entry.get("general") if isinstance(entry, dict) else None
        if isinstance(general, (int, float)) and not isinstance(general, bool):
            stealth_scores_by_route[(route.provider.lower(), route.route_model)] = float(general)

    stealth_routes = (
        stealthmark_only_routes(
            routes, aa_identities, stealth_entries, args.stealthmark_match_threshold
        )
        if stealth_entries
        else []
    )

    selected, rejected, summary, stealth_records = build_result(
        groups,
        aa_models,
        models_dev_rows,
        args,
        index_version,
        stealthmark_routes=stealth_routes,
    )

    # A stealth model promoted to its own section must not also appear in the
    # unbenchmarked section; everything StealthMark could not score stays there.
    promoted_keys = {
        (route["provider"].lower(), route["route_model"])
        for record in stealth_records
        for route in record["free_routes"]
    }

    # The published composite is the third lane: AA first, StealthMark second, and
    # llm-stats only for what both left unclaimed. It is a different scale, so it
    # never writes into the AA columns and never feeds the intelligence floor.
    published_entries: list[dict[str, Any]] = []
    published_error: str | None = None
    if not args.skip_llmstats:
        if args.verbose:
            eprint("Fetching llm-stats leaderboard ...")
        published_rows, published_error = fetch_llmstats(args.llmstats_url)
        published_entries = [
            entry
            for entry in (llmstats_entry(row) for row in published_rows)
            if entry is not None
        ]
        if published_error:
            eprint(f"Warning: llm-stats enrichment unavailable: {published_error}")

    claimed_identities = set(aa_identities)
    for route in routes:
        if (route.provider.lower(), route.route_model) in promoted_keys:
            claimed_identities.add(_route_identity(route))
    published_routes = (
        published_only_routes(
            routes, claimed_identities, published_entries, args.llmstats_match_threshold
        )
        if published_entries
        else []
    )
    published_records, published_info = build_published_records(
        published_routes, models_dev_rows, args
    )
    published_info["models_gathered"] = len(published_entries)

    published_keys = {
        (route["provider"].lower(), route["route_model"])
        for record in published_records
        for route in record["free_routes"]
    }

    unbenchmarked_routes = [
        route
        for route in routes
        if (route.provider.lower(), route.route_model) not in promoted_keys
        and (route.provider.lower(), route.route_model) not in published_keys
    ]

    benchmarked_keys: set[tuple[str, str]] = set()
    matched_aa_by_route: dict[tuple[str, str], str | None] = {}
    for record in (*selected, *rejected):
        aa_slug = record["model"].get("aa_slug")
        for route in record["free_routes"]:
            key = (route["provider"].lower(), route["route_model"])
            matched_aa_by_route[key] = aa_slug
            if record["quality"]["score_source"] != "missing":
                benchmarked_keys.add(key)
    unbenchmarked = build_unbenchmarked(
        unbenchmarked_routes,
        matched_aa_by_route,
        benchmarked_keys,
        models_dev_rows,
        args,
        intelligence_by_route=stealth_scores_by_route,
        include_unscored=bool(args.include_unscored),
    )

    summary.update(
        {
            "generated_unix": int(time.time()),
            "runtime_seconds": round(time.time() - start, 3),
            "sources_requested": args.source,
            "routes_by_source": routes_by_source,
            "source_errors": source_errors,
            "free_routes_seen": len(routes),
            "free_routes_unmatched_to_aa": len(unmatched),
            "routes_by_verification": {
                "zero-priced": sum(1 for r in routes if r.verification == "zero-priced"),
                "free-tier": sum(1 for r in routes if r.verification == "free-tier"),
                "mirror-listed": sum(1 for r in routes if r.verification == "mirror-listed"),
            },
            "free_tier_routes_skipped": free_tier_skipped,
            "mirror_routes_skipped": mirror_skipped,
            "include_free_tier": bool(args.include_free_tier),
            "include_unscored": bool(args.include_unscored),
            "unbenchmarked_models": len(unbenchmarked),
            "unbenchmarked_routes": sum(r["route_count"] for r in unbenchmarked),
            "stealthmark_models": len(stealth_records),
            "stealthmark_routes": sum(r["route_count"] for r in stealth_records),
            "stealthmark_models_gathered": len(stealth_entries),
            "stealthmark_feed_error": stealth_error,
            "published_models": len(published_records),
            "published_routes": sum(r["route_count"] for r in published_records),
            "published_models_gathered": len(published_entries),
            "published_feed_error": published_error,
            "published_min_score": args.min_published,
            "models_dev_entries_seen": len(models_dev_rows),
            "models_dev_error": models_dev_error,
            "attribution": {
                "Artificial Analysis": "https://artificialanalysis.ai/",
                "F24 SALES": "https://www.f24-sales.com/",
                "OpenRouter": "https://openrouter.ai/",
                "models.dev": "https://models.dev/",
                "StealthMark": "https://stealthmodels.com/",
                "llm-stats": "https://llm-stats.com/methodology/llm-stats-score",
            },
        }
    )
    summary["stealthmark"].update(
        {
            "enabled": not args.skip_stealthmark,
            "models_gathered": len(stealth_entries),
            "feed_error": stealth_error,
            "models_selected": len(stealth_records),
            "routes_selected": sum(r["route_count"] for r in stealth_records),
        }
    )
    summary["published"] = dict(published_info)
    summary["published"].update(
        {
            "enabled": not args.skip_llmstats,
            "models_gathered": len(published_entries),
            "feed_error": published_error,
            "models_selected": len(published_records),
            "routes_selected": sum(r["route_count"] for r in published_records),
        }
    )

    prefix = Path(args.output_prefix)
    selected_json = prefix.with_suffix(".json")
    selected_csv = prefix.with_suffix(".csv")
    unmatched_json = Path(str(prefix) + "_unmatched.json")
    diagnostics_json = Path(str(prefix) + "_diagnostics.json")
    unbenchmarked_json = Path(str(prefix) + "_unbenchmarked.json")
    unbenchmarked_csv = Path(str(prefix) + "_unbenchmarked.csv")
    stealthmark_json = Path(str(prefix) + "_stealthmark.json")
    stealthmark_csv = Path(str(prefix) + "_stealthmark.csv")
    published_json = Path(str(prefix) + "_published.json")
    published_csv = Path(str(prefix) + "_published.csv")

    write_json(selected_json, {"summary": summary, "models": selected})
    write_csv(selected_csv, selected)
    write_json(unmatched_json, {"summary": summary, "unmatched_routes": unmatched})
    write_json(diagnostics_json, {"summary": summary, "rejected_models": rejected})
    write_json(unbenchmarked_json, {"summary": summary, "models": unbenchmarked})
    write_unbenchmarked_csv(unbenchmarked_csv, unbenchmarked)
    write_json(stealthmark_json, {"summary": summary, "models": stealth_records})
    write_stealthmark_csv(stealthmark_csv, stealth_records)
    write_json(published_json, {"summary": summary, "models": published_records})
    write_published_csv(published_csv, published_records)

    print_console(
        selected,
        len(unmatched),
        free_tier_skipped,
        mirror_skipped,
        min_intelligence=args.min_intelligence,
    )
    print_stealthmark(stealth_records, min_intelligence=args.min_intelligence)
    if stealth_entries and not stealth_records:
        print(
            f"\nStealthMark: {len(stealth_entries)} model(s) scored; no free route "
            "matched a scored model above the current floor."
        )
    print_published(published_records, min_published=args.min_published)
    if published_entries and not published_records:
        print(
            f"\nPublished composite: {len(published_entries)} model(s) scored; no free "
            "route matched a scored model above the current floor."
        )
    print_unbenchmarked(unbenchmarked, min_intelligence=args.min_intelligence)
    print("\nWrote:")
    print(f"  {selected_json}")
    print(f"  {selected_csv}")
    print(f"  {unmatched_json}")
    print(f"  {diagnostics_json}")
    print(f"  {unbenchmarked_json}")
    print(f"  {unbenchmarked_csv}")
    print(f"  {stealthmark_json}")
    print(f"  {stealthmark_csv}")
    print(f"  {published_json}")
    print(f"  {published_csv}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        eprint("Interrupted.")
        raise SystemExit(130)
    except Exception as exc:
        eprint(f"ERROR: {exc}")
        if os.getenv("DEBUG"):
            raise
        raise SystemExit(1)

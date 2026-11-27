#!/usr/bin/env python3
"""Probe the zero-priced free-model routes that are apt for a junior dev agent.

Reads the catalogue published by ``free_model_filter.py``, keeps the models that
are strong enough at coding or agentic work and are agent-ready, probes every
zero-priced route for liveness inside a hard wall-clock budget, and writes a
state file that junior-dev skills read.  Standard library only.

Every probe runs in its own process group so that a hung socket cannot outlive
the sweep.  The child is this same script re-executed with the hidden
``--probe-one`` flag: it signals readiness on an inherited fd, reads one JSON
request from stdin, and writes one JSON result to stdout.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_CATALOGUE = "~/.software-factory/free_models.json"
DEFAULT_STATE = "~/.software-factory/free_models_junior.json"
DEFAULT_ENV_FILE = "~/.software-factory/free-models.env"
DEFAULT_SWEEP_BUDGET = 600.0
DEFAULT_REQUEST_TIMEOUT = 30.0
MAX_CATALOGUE_AGE_SECONDS = 6 * 3600
CLOCK_SKEW_SECONDS = 300
# Minimum coding or agentic score a model needs before it is probed. Only models
# clearing this floor (and agent-ready) are worth a request, so the sweep stays
# inside its budget. Deliberately not a CLI flag: the floor is what makes the
# shortlist short, and the skill's consumers rely on it.
QUALITY_FLOOR = 25.0
PROBE_PROMPT = "Say ok"
PROBE_MAX_TOKENS = 1
READY_FD_ENV = "FREE_MODELS_PROBE_READY_FD"
READY_WAIT_SECONDS = 30.0
KILL_GRACE_SECONDS = 5.0
DRAIN_SECONDS = 1.0
MAX_BODY_BYTES = 1 << 20
STREAM_CAP_BYTES = 1 << 20

# Provider base URLs; "opencode" is a CLI probe and has no base URL.
PROVIDER_BASES = {
    "openrouter": "https://openrouter.ai/api/v1",
    "kilo": "https://ai-gateway.kilo.ai/api/v1",
}
ENV_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class CatalogueError(Exception):
    """The catalogue is missing, unreadable, malformed, or stale."""


class CrossHostRedirect(Exception):
    """A redirect that would leave the host the probe started on."""

    def __init__(self, status: int, location: str) -> None:
        """Record the redirect status and target for the classifier."""
        super().__init__(f"redirect {status} to {location}")
        self.status = status


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow same-host redirects only; refuse cross-host ones."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Raise CrossHostRedirect when the redirect target changes host.

        The scheme is compared too: following an HTTPS to HTTP redirect on the
        same host would resend the bearer token in plaintext, because urllib
        copies the Authorization header across 301/302/303.
        """
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        old_host = (old.hostname or "").lower()
        new_host = (new.hostname or "").lower()
        # Port is compared too: the bearer token must not follow a redirect to
        # another port on the same host, which may be a different service.
        old_port = old.port if old.port is not None else (443 if old.scheme == "https" else 80)
        new_port = new.port if new.port is not None else (443 if new.scheme == "https" else 80)
        if new_host != old_host or new.scheme != old.scheme or new_port != old_port:
            raise CrossHostRedirect(code, newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def elapsed_ms(started: float) -> int:
    """Milliseconds elapsed since a time.monotonic() reading."""
    return round((time.monotonic() - started) * 1000)


def utc_timestamp() -> str:
    """Current UTC time as ISO-8601 with a trailing Z, second precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def redact(text: str, secrets: Sequence[str]) -> str:
    """Replace any credential that appears in captured output.

    Provider errors and CLI stderr are copied into the state file and printed
    by --verbose, so a provider that echoes a token back must not leak it.
    """
    cleaned = text
    for secret in secrets:
        if secret and secret in cleaned:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned


def first_line(value: bytes | str, secrets: Sequence[str] = ()) -> str:
    """First non-empty line of a child's output, redacted then trimmed.

    Redaction happens before truncation so a secret straddling the 200-character
    cut cannot leave a recognisable prefix behind.
    """
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            return redact(cleaned, secrets)[:200]
    return ""


def short_reason(exc: Any, secrets: Sequence[str] = ()) -> str:
    """Short, secret-free reason for a network or OS error.

    Redaction runs before the length cut so a credential straddling the
    boundary cannot leave a recognisable prefix.
    """
    text = redact(" ".join(str(exc or "").split()), secrets)
    lowered = text.lower()
    if isinstance(exc, socket.gaierror) or "name or service not known" in lowered:
        return "dns error"
    if "temporary failure in name resolution" in lowered or "nodename nor servname" in lowered:
        return "dns error"
    if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in lowered:
        return "timeout"
    if "connection refused" in lowered:
        return "connection refused"
    if "connection reset" in lowered:
        return "connection reset"
    if "network is unreachable" in lowered or "no route to host" in lowered:
        return "network unreachable"
    return text[:120] or "connection error"


def as_number(value: Any) -> float | None:
    """A finite float when value is a real number, else None (bools excluded).

    Integers too large for a float are rejected rather than raising OverflowError,
    so a hostile catalogue value fails the freshness gate instead of crashing.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def error_message(value: Any, secrets: Sequence[str] = ()) -> str:
    """Short text for a JSON ``error`` field, redacted before truncation."""
    if isinstance(value, dict):
        for key in ("message", "error", "detail"):
            inner = value.get(key)
            if isinstance(inner, str) and inner.strip():
                return redact(" ".join(inner.split()), secrets)[:200]
        value = json.dumps(value, ensure_ascii=False)
    text = redact(" ".join(str(value).split()), secrets)
    return (text or "body error")[:200]


def classify_body_error(payload: dict[str, Any], status: int, latency_ms: int,
                        secrets: Sequence[str]) -> dict[str, Any]:
    """Turn a provider error body into a redacted failure result."""
    message = error_message(payload["error"], secrets)
    return probe_result(False, status, message or "body error", latency_ms)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def unquote_value(value: str) -> str:
    """Strip one pair of matching single or double quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines as data; blank lines and # comments are ignored."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not ENV_KEY_PATTERN.fullmatch(key):
            continue
        values[key] = unquote_value(value.strip())
    return values


def load_credentials(env_path: Path, repo_env: Path) -> dict[str, str]:
    """Keys from the dedicated env file, falling back to the repo .env.

    The repo fallback is only used when this script is actually running from a
    checkout (a ``.git`` directory at the repo root). An installed copy lives in
    the state dir with no repo, so its computed "repo root" would be ``$HOME``
    and the fallback would read ``~/.env`` -- outside the 0700 state dir and
    possibly world-readable. In that case only the dedicated env file is used.
    """
    if env_path.is_file():
        return read_env_file(env_path)
    if (repo_env.parent / ".git").exists() and repo_env.is_file():
        return read_env_file(repo_env)
    return {}


# ---------------------------------------------------------------------------
# Catalogue loading and model selection
# ---------------------------------------------------------------------------


def load_catalogue(path: Path, expect_sha256: str | None = None) -> tuple[dict[str, Any], bytes]:
    """Read the catalogue bytes and decode them as a JSON object.

    When ``expect_sha256`` is given (a bare hex digest), the bytes actually read
    must match it. Checking here rather than in the caller closes the gap between
    a separate hash pass and this read.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CatalogueError(f"cannot read catalogue {path}: {exc.strerror or exc}") from exc
    if expect_sha256:
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expect_sha256:
            raise CatalogueError(
                f"catalogue {path} changed while being read; refusing to probe it"
            )
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise CatalogueError(f"catalogue {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CatalogueError(f"catalogue {path} is not a JSON object")
    return data, raw


def catalogue_generated_unix(catalogue: dict[str, Any]) -> float:
    """Validate the catalogue freshness gate and return its generated_unix."""
    summary = catalogue.get("summary")
    value = summary.get("generated_unix") if isinstance(summary, dict) else None
    generated = as_number(value)
    if generated is None:
        raise CatalogueError("catalogue summary.generated_unix is missing or not a number")
    now = time.time()
    if generated > now + CLOCK_SKEW_SECONDS:
        raise CatalogueError(
            "catalogue summary.generated_unix is "
            f"{int(generated - now)}s in the future (clock skew tolerance {CLOCK_SKEW_SECONDS}s)"
        )
    age = now - generated
    if age > MAX_CATALOGUE_AGE_SECONDS:
        raise CatalogueError(
            f"catalogue is stale: generated {int(age)}s ago "
            f"(limit {MAX_CATALOGUE_AGE_SECONDS}s); run free_model_filter.py again"
        )
    return generated


def model_name(record: dict[str, Any]) -> str:
    """Display name from a catalogue model record."""
    model = record.get("model") if isinstance(record.get("model"), dict) else {}
    return str(model.get("name") or model.get("llmstats_id") or "")


def model_selected(record: dict[str, Any]) -> bool:
    """True when the model clears the coding/agentic floor and is agent-ready."""
    quality = record.get("quality")
    if not isinstance(quality, dict):
        return False
    coding = as_number(quality.get("coding"))
    agentic = as_number(quality.get("agentic"))
    strong = (coding is not None and coding >= QUALITY_FLOOR) or (
        agentic is not None and agentic >= QUALITY_FLOOR
    )
    if not strong:
        return False
    readiness = record.get("agent_readiness")
    return isinstance(readiness, dict) and readiness.get("status") is True


def model_sort_key(record: dict[str, Any]) -> tuple[float, float, str]:
    """Rank order: best coding, then best agentic, then name."""
    quality = record.get("quality") if isinstance(record.get("quality"), dict) else {}
    coding = as_number(quality.get("coding"))
    agentic = as_number(quality.get("agentic"))
    floor = float("-inf")
    return (
        -(coding if coding is not None else floor),
        -(agentic if agentic is not None else floor),
        model_name(record).casefold(),
    )


def select_models(catalogue: dict[str, Any]) -> list[dict[str, Any]]:
    """The selected, agent-ready model records in ranked order."""
    records = catalogue.get("models")
    if not isinstance(records, list):
        return []
    selected = [record for record in records if isinstance(record, dict) and model_selected(record)]
    selected.sort(key=model_sort_key)
    return selected


# ---------------------------------------------------------------------------
# Probe planning (parent side)
# ---------------------------------------------------------------------------


def probe_result(
    available: bool | None,
    http_status: int | None,
    error: str | None,
    latency_ms: int | None = None,
) -> dict[str, Any]:
    """The normalized probe result shape shared by child and parent."""
    return {
        "available": available,
        "http_status": http_status,
        "error": error,
        "latency_ms": latency_ms,
    }


def http_request(
    base: str, route_model: str, timeout: float, token: str | None
) -> dict[str, Any]:
    """Describe the chat-completion POST for one HTTP provider route."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return {
        "mode": "http",
        "url": f"{base}/chat/completions",
        "headers": headers,
        "body": {
            "model": route_model,
            "messages": [{"role": "user", "content": PROBE_PROMPT}],
            "max_tokens": PROBE_MAX_TOKENS,
            "temperature": 0,
        },
        "timeout": timeout,
    }


def plan_probe(
    provider: str,
    route_model: str,
    credentials: dict[str, str],
    timeout: float,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Build the child request for a route, or an immediate classification."""
    key = provider.strip().lower()
    if key == "openrouter":
        token = credentials.get("OPENROUTER_API_KEY", "").strip()
        if not token:
            return None, probe_result(None, None, "no api key")
        request = http_request(PROVIDER_BASES["openrouter"], route_model, timeout, token)
        request["secrets"] = [token]
        return request, None
    if key == "kilo":
        return http_request(PROVIDER_BASES["kilo"], route_model, timeout, None), None
    if key == "opencode":
        binary = credentials.get("OPENCODE_BIN", "").strip() or shutil.which("opencode") or ""
        if not binary:
            return None, probe_result(None, None, "no opencode binary")
        if not os.path.isabs(binary):
            return None, probe_result(None, None, "opencode binary is not an absolute path")
        command = [binary, "run", "--model", f"opencode/{route_model}", PROBE_PROMPT]
        request = {"mode": "cli", "command": command, "timeout": timeout}
        # The CLI's stderr is captured into state, so every credential in the
        # environment must be redactable, not just the OpenRouter token.
        request["secrets"] = [value for value in credentials.values() if value]
        return request, None
    return None, probe_result(None, None, f"unsupported provider: {provider or 'unknown'}")


def route_entry(route: dict[str, Any]) -> dict[str, Any]:
    """The output record for one catalogue route, before probing."""
    route_model = str(route.get("route_model") or "")
    return {
        "provider": str(route.get("provider") or ""),
        "route_model": route_model,
        "model_name": str(route.get("model_name") or route_model),
        "available": None,
        "latency_ms": None,
        "http_status": None,
        "error": None,
        "last_probed": None,
    }


def verbose_line(entry: dict[str, Any]) -> str:
    """One stderr line describing a route probe outcome."""
    return (
        f"{entry.get('provider')}/{entry.get('route_model')} "
        f"available={entry.get('available')} status={entry.get('http_status')} "
        f"latency_ms={entry.get('latency_ms')} error={entry.get('error')}"
    )


# ---------------------------------------------------------------------------
# Probe execution (child side)
# ---------------------------------------------------------------------------


def classify_http_error(status: int, latency_ms: int) -> dict[str, Any]:
    """Map an HTTP error status to the documented classification."""
    if status in (401, 403):
        return probe_result(False, status, "auth failed", latency_ms)
    if status == 429:
        return probe_result(None, status, "rate limited", latency_ms)
    if 500 <= status < 600:
        return probe_result(None, status, "server error", latency_ms)
    if 300 <= status < 400:
        # A same-host redirect was refused on purpose; anything else here is a
        # redirect the probe declined to follow rather than a dead route.
        return probe_result(None, status, f"unfollowed redirect {status}", latency_ms)
    return probe_result(None, status, f"http {status}", latency_ms)


def classify_chat_body(raw: bytes, status: int, latency_ms: int,
                       secrets: Sequence[str] = ()) -> dict[str, Any]:
    """Classify a 200 chat-completion body by its JSON shape."""
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return probe_result(None, status, "invalid json body", latency_ms)
    if not isinstance(payload, dict):
        return probe_result(None, status, "invalid json body", latency_ms)
    # A present-but-null "error" with no usable choices is not a success: the
    # route answered, but not with a completion.
    if "error" in payload and payload["error"] is not None:
        return classify_body_error(payload, status, latency_ms, secrets)
    choices = payload.get("choices")
    if (
        isinstance(choices, list)
        and choices
        and isinstance(choices[0], dict)
        and isinstance(choices[0].get("message"), dict)
    ):
        # An empty message object is not evidence the route can generate.
        if not choices[0]["message"]:
            return probe_result(None, status, "empty message in body", latency_ms)
        return probe_result(True, status, None, latency_ms)
    return probe_result(None, status, "no choices in body", latency_ms)


def run_http_probe(request: dict[str, Any], timeout: float,
                   secrets: Sequence[str] = ()) -> dict[str, Any]:
    """POST one chat-completion request and classify the response."""
    url = str(request.get("url") or "")
    if not url.startswith(("http://", "https://")):
        return probe_result(None, None, "bad probe url")
    raw_headers = request.get("headers")
    headers = (
        {str(key): str(value) for key, value in raw_headers.items()}
        if isinstance(raw_headers, dict)
        else {}
    )
    raw_body = request.get("body")
    body = json.dumps(raw_body if isinstance(raw_body, dict) else {}).encode("utf-8")
    prepared = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(SafeRedirectHandler())
    started = time.monotonic()
    try:
        with opener.open(prepared, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
            payload = response.read(MAX_BODY_BYTES)
    except CrossHostRedirect as exc:
        return probe_result(None, exc.status, "unfollowed cross-host redirect")
    except urllib.error.HTTPError as exc:
        return classify_http_error(int(exc.code), elapsed_ms(started))
    except TimeoutError:
        return probe_result(None, None, "timeout")
    except urllib.error.URLError as exc:
        return probe_result(None, None, short_reason(exc.reason, secrets))
    except OSError as exc:
        return probe_result(None, None, short_reason(exc, secrets))
    latency = elapsed_ms(started)
    if status != 200:
        return probe_result(None, status, f"http {status}", latency)
    return classify_chat_body(payload, status, latency, secrets)


def run_cli_probe(request: dict[str, Any], timeout: float,
                  secrets: Sequence[str] = ()) -> dict[str, Any]:
    """Run the OpenCode CLI for one route and classify the outcome.

    The CLI runs inside a PID namespace, which is the only reliable containment
    here: the namespace init is the CLI itself, so tearing it down takes every
    descendant with it, including one that called setsid().  When bwrap is
    unavailable the route is reported as unknown rather than probed, because a
    bare CLI probe could leave processes running after the sweep.
    """
    command = request.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) for part in command)
    ):
        return probe_result(None, None, "bad probe command")
    # The binary must be an absolute path: a bare name would resolve through
    # PATH and a relative one could pick a workspace executable.
    if not os.path.isabs(command[0]):
        return probe_result(None, None, "opencode binary is not an absolute path")
    sandbox = build_cli_sandbox(os.getcwd())
    argv = namespace_command(command, sandbox)
    if argv is None:
        cleanup_cli_sandbox(sandbox)
        return probe_result(None, None, "no pid namespace available (bwrap missing)")
    # bwrap registers its parent-death signal during startup, so a deadline that
    # lands before that registration cannot be caught by --die-with-parent. That
    # window is covered by the caller's process-group kill of this worker, not by
    # kill_live_children: this set only holds probe-worker pids.
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=sandbox["env"],
        )
    except OSError as exc:
        cleanup_cli_sandbox(sandbox)
        return probe_result(None, None, short_reason(exc))
    # bwrap is the namespace init (PID 1 in the namespace) and --die-with-parent
    # ties its lifetime to this worker, so a deadline kill tears the tree down.
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_group(process.pid)
        try:
            stdout, stderr = process.communicate(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
    finally:
        if process.poll() is None:
            terminate_group(process.pid)
        close_pipes(process)
        cleanup_cli_sandbox(sandbox)
    latency = elapsed_ms(started)
    if timed_out:
        return probe_result(None, None, "timeout")
    if process.returncode != 0:
        captured = first_line(stderr or "", secrets)
        # A bwrap setup failure means the route was never reached; reporting it
        # as dead would be a false negative.
        if captured.startswith("bwrap:"):
            return probe_result(None, None, captured, latency)
        reason = captured or f"exit {process.returncode}"
        return probe_result(False, None, reason, latency)
    if (stdout or "").strip():
        return probe_result(True, None, None, latency)
    return probe_result(False, None, "empty stdout", latency)


def namespace_command(command: list[str], sandbox: dict[str, Any]) -> list[str] | None:
    """Wrap argv in a PID namespace via bwrap, or None when unavailable.

    The host filesystem is bound read-only; /tmp stays a writable host bind.
    The OpenCode data dir (auth/DB) is bound read-write so the CLI can run, and
    cache/runtime are fresh tmpfs. ~/.ssh is masked with an empty tmpfs. Network
    is left open because the CLI must reach the model API.

    Note: this is filesystem/credential containment, not tool denial. The
    OpenCode CLI talks to the user's background service, which loads the user's
    real config, so per-tool permission rules cannot be enforced here; the
    protection is that the child environment carries none of the probe's
    provider keys and ~/.ssh is unreadable.
    """
    binary = shutil.which("bwrap")
    if not binary:
        return None
    argv = [
        binary,
        "--unshare-pid",
        # bwrap becomes PID 1 of the new namespace and --die-with-parent ties
        # its lifetime to the probe worker, so a deadline kill tears it down.
        "--die-with-parent",
        "--as-pid-1",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--ro-bind",
        "/",
        "/",
    ]
    if os.path.isdir("/tmp"):
        argv += ["--bind", "/tmp", "/tmp"]
    # Mask the user's SSH keys with an empty tmpfs.
    ssh_dir = os.path.expanduser("~/.ssh")
    if os.path.isdir(ssh_dir):
        argv += ["--tmpfs", ssh_dir]
    # The OpenCode data dir (auth/DB) must stay writable for the CLI to run.
    data_home = sandbox.get("data_home")
    if data_home and os.path.isdir(data_home):
        argv += ["--bind", data_home, data_home]
    # Throwaway cache/runtime tmpfs so the CLI does not touch the real caches.
    for key in ("cache", "runtime"):
        argv += ["--tmpfs", sandbox[key]]
    argv += list(command)
    return argv


def build_cli_sandbox(state_dir: str) -> dict[str, Any]:
    """Build a throwaway sandbox for the OpenCode CLI probe.

    The probe runs an untrusted free model. This provides filesystem and
    credential containment: the child environment drops the probe's own
    provider keys (AA_API_KEY, OPENROUTER_API_KEY, ...) so they are not
    reachable, and ~/.ssh is masked. The OpenCode data dir is kept (the CLI
    needs its auth) and cache/runtime are throwaway tmpfs.
    """
    base = tempfile.mkdtemp(prefix="fm-probe-")
    cache = os.path.join(base, "cache")
    runtime = os.path.join(base, "runtime")
    for path in (cache, runtime):
        os.makedirs(path, exist_ok=True)
    os.chmod(runtime, 0o700)
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    # Minimal environment: no provider keys, no inherited secrets.
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/root"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "XDG_DATA_HOME": data_home,
        "XDG_CACHE_HOME": cache,
        "XDG_RUNTIME_DIR": runtime,
    }
    return {
        "base": base,
        "cache": cache,
        "runtime": runtime,
        "data_home": data_home,
        "env": env,
        "secrets": state_dir,
    }


def cleanup_cli_sandbox(sandbox: dict[str, Any]) -> None:
    """Remove the throwaway sandbox directory tree."""
    base = sandbox.get("base")
    if base:
        shutil.rmtree(base, ignore_errors=True)


def close_pipes(process: subprocess.Popen[str]) -> None:
    """Close the child's pipes so an escaped descendant cannot block us."""
    for stream in (process.stdout, process.stderr):
        try:
            if stream is not None:
                stream.close()
        except OSError:
            pass


def dispatch_probe(request: dict[str, Any], timeout: float,
                   secrets: Sequence[str] = ()) -> dict[str, Any]:
    """Run the probe described by one child request."""
    mode = request.get("mode")
    if mode == "http":
        return run_http_probe(request, timeout, secrets)
    if mode == "cli":
        return run_cli_probe(request, timeout, secrets)
    return probe_result(None, None, "bad probe request")


def send_ready() -> None:
    """Signal the parent that this child is alive and about to wait for work."""
    fd_text = os.environ.pop(READY_FD_ENV, "")
    if not fd_text:
        return
    try:
        fd = int(fd_text)
    except ValueError:
        return
    try:
        os.write(fd, b"ready")
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def probe_one_main() -> int:
    """Hidden child mode: one probe request on stdin, one JSON line on stdout."""
    send_ready()
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except (ValueError, OSError):
        request = {}
    if not isinstance(request, dict):
        request = {}
    timeout = DEFAULT_REQUEST_TIMEOUT
    declared = as_number(request.get("timeout"))
    if declared is not None and declared > 0:
        timeout = declared
    raw_secrets = request.get("secrets")
    secrets = (
        [str(item) for item in raw_secrets if isinstance(item, str) and item]
        if isinstance(raw_secrets, list)
        else []
    )
    try:
        result = dispatch_probe(request, timeout, secrets)
    except Exception as exc:  # noqa: BLE001 - the child must always answer
        result = probe_result(None, None, f"probe crashed: {type(exc).__name__}")
    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()
    return 0


# ---------------------------------------------------------------------------
# Probe orchestration (parent side)
# ---------------------------------------------------------------------------

# Probe children are tracked so the sweep can take their groups down. In a
# PID namespace the group kill is sufficient; the set is a belt-and-braces
# record for the case where a child is still starting up.
_LIVE_CHILDREN: set[int] = set()


def register_child(child: subprocess.Popen[bytes]) -> None:
    """Remember a probe child so cleanup can reach it later."""
    _LIVE_CHILDREN.add(child.pid)


def kill_live_children() -> None:
    """Terminate every still-registered probe child and its group.

    The group is signalled even when the leader has already exited, because a
    descendant that outlived its parent is exactly what needs killing.
    """
    for pid in sorted(_LIVE_CHILDREN):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                continue
    _LIVE_CHILDREN.clear()


def spawn_probe_child(ready_fd: int) -> subprocess.Popen[bytes]:
    """Start this script in child mode with its own session and group."""
    env = dict(os.environ)
    env[READY_FD_ENV] = str(ready_fd)
    command = [sys.executable, str(Path(__file__).resolve()), "--probe-one"]
    return subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
        pass_fds=(ready_fd,),
    )


def send_request(child: subprocess.Popen[bytes], request: dict[str, Any],
                 deadline: float) -> bool:
    """Write the JSON request to the child's stdin without blocking forever.

    The pipe is set non-blocking and drained with ``select`` bounded by the
    sweep deadline: a child that never reads must not stall the parent.
    """
    stream = child.stdin
    if stream is None:
        return False
    payload = json.dumps(request).encode("utf-8")
    fd = stream.fileno()
    if not set_nonblocking(fd):
        # Without O_NONBLOCK a write could block forever on a child that never
        # reads; refuse rather than risk hanging the sweep.
        try:
            stream.close()
        except OSError:
            pass
        return False
    sent = 0
    wrote = True
    try:
        while sent < len(payload):
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return False
            _readable, writable, _ = select.select([], [fd], [], remaining)
            if not writable:
                return False
            sent += os.write(fd, payload[sent:])
    except (BrokenPipeError, OSError):
        wrote = False
    finally:
        try:
            stream.close()
        except OSError:
            pass
    return wrote


def set_nonblocking(fd: int) -> bool:
    """Put a file descriptor into non-blocking mode; False when unsupported."""
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        return True
    except OSError:
        return False


def wait_for_ready(fd: int, deadline: float) -> bool:
    """Wait for the child's readiness byte, bounded by the deadline."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            return False
        try:
            readable, _, _ = select.select([fd], [], [], remaining)
        except OSError:
            return False
        if not readable:
            return False
        try:
            return bool(os.read(fd, 16))
        except OSError:
            return False


def read_streams(
    child: subprocess.Popen[bytes], deadline: float
) -> tuple[bytes, bytes, bool]:
    """Read stdout and stderr until EOF or the deadline; report which came first."""
    stdout, stderr = child.stdout, child.stderr
    if stdout is None or stderr is None:
        return b"", b"", child.poll() is not None
    out_buffer = bytearray()
    err_buffer = bytearray()
    open_fds: dict[int, bytearray] = {
        stdout.fileno(): out_buffer,
        stderr.fileno(): err_buffer,
    }
    while open_fds:
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            break
        try:
            readable, _, _ = select.select(list(open_fds), [], [], remaining)
        except OSError:
            break
        if not readable:
            break
        for fd in readable:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                open_fds.pop(fd, None)
                continue
            buffer = open_fds[fd]
            room = STREAM_CAP_BYTES - len(buffer)
            if room > 0:
                buffer.extend(chunk[:room])
    return bytes(out_buffer), bytes(err_buffer), not open_fds


def terminate_group(pgid: int) -> None:
    """SIGTERM a process group, then SIGKILL only if it is still there."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        return
    try:
        time.sleep(KILL_GRACE_SECONDS)
        os.killpg(pgid, 0)
    except OSError:
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass


def terminate_process_group(child: subprocess.Popen[bytes]) -> None:
    """SIGTERM the child's group, then SIGKILL if it is still there after 5 s."""
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return
    try:
        child.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(child.pid, 0)
    except OSError:
        return
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        child.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def reap_child(child: subprocess.Popen[bytes]) -> None:
    """Wait for an already-finished child without blocking for long."""
    try:
        child.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def normalize_probe_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Coerce a child's JSON result into the documented types."""
    available = payload.get("available")
    status = payload.get("http_status")
    error = payload.get("error")
    latency = payload.get("latency_ms")
    return probe_result(
        available if isinstance(available, bool) else None,
        int(status) if isinstance(status, int) and not isinstance(status, bool) else None,
        str(error)[:200] if isinstance(error, str) and error else None,
        int(latency) if isinstance(latency, (int, float)) and not isinstance(latency, bool) else None,
    )


def child_secrets(request: dict[str, Any]) -> list[str]:
    """The redaction list a child request carries, for parent-side use."""
    raw = request.get("secrets")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item]


def parse_probe_output(stdout: bytes, stderr: bytes,
                       secrets: Sequence[str] = ()) -> dict[str, Any]:
    """Read the child's JSON result, falling back to a stderr-derived failure."""
    for line in reversed(stdout.decode("utf-8", "replace").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict) and "available" in payload:
            return normalize_probe_result(payload)
    return probe_result(None, None, first_line(stderr, secrets) or "probe failed")


def failed_probe(
    child: subprocess.Popen[bytes], fallback: str, already_killed: bool = False,
    secrets: Sequence[str] = (),
) -> dict[str, Any]:
    """Kill a stuck child, collect its stderr, and classify the failure.

    The whole cleanup is bounded so a hung child cannot stretch the sweep past
    its deadline: SIGKILL is immediate and the stderr drain is short.
    """
    if not already_killed:
        terminate_process_group(child)
    _out, err, _finished = read_streams(child, time.monotonic() + DRAIN_SECONDS)
    return probe_result(None, None, first_line(err, secrets) or fallback)


def probe_in_child(request: dict[str, Any], deadline: float) -> dict[str, Any]:
    """Run one probe in its own process group and return its classification."""
    if deadline - time.monotonic() <= 0.0:
        return probe_result(None, None, "sweep deadline")
    ready_r, ready_w = os.pipe()
    child: subprocess.Popen[bytes] | None = None
    try:
        try:
            child = spawn_probe_child(ready_w)
        except OSError as exc:
            return probe_result(None, None, short_reason(exc))
        register_child(child)
        os.close(ready_w)
        ready_w = -1
        # The child is registered before the request goes out, so a deadline or
        # interrupt during its startup still finds it.
        if not send_request(child, request, deadline):
            return failed_probe(child, "probe failed to start", secrets=child_secrets(request))
        ready_deadline = min(deadline, time.monotonic() + READY_WAIT_SECONDS)
        if not wait_for_ready(ready_r, ready_deadline):
            return failed_probe(child, "probe did not start", secrets=child_secrets(request))
        out, err, finished = read_streams(child, deadline)
        if not finished:
            return failed_probe(child, "sweep deadline", secrets=child_secrets(request))
        reap_child(child)
        return parse_probe_output(out, err, child_secrets(request))
    finally:
        if ready_w != -1:
            try:
                os.close(ready_w)
            except OSError:
                pass
        try:
            os.close(ready_r)
        except OSError:
            pass
        if child is not None:
            # Always signal the group, even if the leader already exited: a
            # descendant outliving its parent is the case that matters.
            terminate_process_group(child)
            _LIVE_CHILDREN.discard(child.pid)


# ---------------------------------------------------------------------------
# Sweep and state assembly
# ---------------------------------------------------------------------------


def probe_routes(
    record: dict[str, Any],
    credentials: dict[str, str],
    deadline: float,
    request_timeout: float,
    verbose: bool,
) -> tuple[list[dict[str, Any]], int]:
    """Probe every route of one selected model; return (route records, probes run)."""
    routes: list[dict[str, Any]] = []
    probed = 0
    raw_routes = record.get("free_routes")
    for route in raw_routes if isinstance(raw_routes, list) else []:
        if not isinstance(route, dict):
            continue
        entry = route_entry(route)
        if str(route.get("verification") or "") != "zero-priced":
            entry["error"] = "skipped: not zero-priced"
        else:
            request, immediate = plan_probe(
                str(route.get("provider") or ""),
                str(route.get("route_model") or ""),
                credentials,
                request_timeout,
            )
            if immediate is not None:
                entry.update(immediate)
            elif time.monotonic() >= deadline:
                entry["error"] = "sweep deadline"
            else:
                entry.update(probe_in_child(request or {}, deadline))
                entry["last_probed"] = utc_timestamp()
                probed += 1
        if verbose:
            print(verbose_line(entry), file=sys.stderr)
        routes.append(entry)
    return routes, probed


def best_route(routes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Lowest-latency available route; null latencies sort last."""
    candidates = [route for route in routes if route.get("available") is True]
    if not candidates:
        return None

    def sort_key(route: dict[str, Any]) -> tuple[int, int, str, str]:
        """Prefer measured latency, then provider name, then route model."""
        latency = route.get("latency_ms")
        measured = isinstance(latency, int) and not isinstance(latency, bool)
        return (
            0 if measured else 1,
            int(latency) if measured else 0,
            str(route.get("provider") or ""),
            str(route.get("route_model") or ""),
        )

    chosen = min(candidates, key=sort_key)
    return {
        "provider": str(chosen.get("provider") or ""),
        "route_model": str(chosen.get("route_model") or ""),
        # Carry the freshness the skill filters on, so it does not have to join
        # back into routes[] to find it.
        "last_probed": chosen.get("last_probed"),
        "latency_ms": chosen.get("latency_ms"),
    }


def model_entry(
    record: dict[str, Any], routes: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build one output model record from a catalogue entry and its probes."""
    model = record.get("model") if isinstance(record.get("model"), dict) else {}
    quality = record.get("quality") if isinstance(record.get("quality"), dict) else {}
    readiness = (
        record.get("agent_readiness")
        if isinstance(record.get("agent_readiness"), dict)
        else {}
    )
    return {
        "model": model_name(record),
        "llmstats_id": model.get("llmstats_id"),
        "organization": model.get("organization"),
        "coding": as_number(quality.get("coding")),
        "agentic": as_number(quality.get("agentic")),
        "intelligence": as_number(quality.get("intelligence")),
        "agent_ready": readiness.get("status") is True,
        "routes": routes,
        "best_available_route": best_route(routes),
    }


def summary_counts(models: list[dict[str, Any]], routes_probed: int) -> dict[str, int]:
    """Count routes by trust level across every swept model."""
    available = unknown = dead = 0
    for model in models:
        for route in model["routes"]:
            value = route.get("available")
            if value is True:
                available += 1
            elif value is False:
                dead += 1
            else:
                unknown += 1
    return {
        "models": len(models),
        "routes_probed": routes_probed,
        "available": available,
        "unknown": unknown,
        "dead": dead,
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON via a temp file in the same directory, then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def source_repo_root() -> Path:
    """Repository root holding this script (scripts/ + one level up)."""
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the documented command line."""
    parser = argparse.ArgumentParser(
        prog="free-models-probe.py",
        description="Probe zero-priced free-model routes and write the junior-dev state file.",
    )
    parser.add_argument("--catalogue", default=DEFAULT_CATALOGUE, help="catalogue JSON path")
    parser.add_argument("--state", default=DEFAULT_STATE, help="state JSON output path")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="credentials env file")
    parser.add_argument(
        "--expect-sha256",
        default=None,
        help="require the catalogue bytes to match this bare hex digest",
    )
    parser.add_argument(
        "--sweep-budget",
        type=float,
        default=DEFAULT_SWEEP_BUDGET,
        help="total wall-clock budget for the sweep, in seconds",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=DEFAULT_REQUEST_TIMEOUT,
        help="per-request timeout, in seconds",
    )
    parser.add_argument("--verbose", action="store_true", help="print one line per route")
    args = parser.parse_args(argv)
    if not math.isfinite(args.sweep_budget) or args.sweep_budget <= 0:
        parser.error("--sweep-budget must be a positive number of seconds")
    if not math.isfinite(args.request_timeout) or args.request_timeout <= 0:
        parser.error("--request-timeout must be a positive number of seconds")
    return args


def run_sweep(argv: list[str]) -> int:
    """Load, select, probe within the budget, and publish the state file."""
    started = time.monotonic()
    args = parse_args(argv)
    catalogue_path = Path(os.path.expanduser(args.catalogue))
    state_path = Path(os.path.expanduser(args.state))
    env_path = Path(os.path.expanduser(args.env_file))
    repo_env = source_repo_root() / ".env"
    try:
        # The caller may have verified a hash it read separately; pass it so the
        # check is made against the exact bytes parsed here, not a second read.
        catalogue, raw_bytes = load_catalogue(catalogue_path, args.expect_sha256)
        catalogue_unix = catalogue_generated_unix(catalogue)
    except CatalogueError as exc:
        print(f"free-models-probe: {exc}", file=sys.stderr)
        return 3
    credentials = load_credentials(env_path, repo_env)
    deadline = started + float(args.sweep_budget)
    selected = select_models(catalogue)
    models: list[dict[str, Any]] = []
    routes_probed = 0
    try:
        for record in selected:
            routes, probed = probe_routes(
                record,
                credentials,
                deadline,
                float(args.request_timeout),
                bool(args.verbose),
            )
            routes_probed += probed
            models.append(model_entry(record, routes))
    finally:
        # Whether the sweep finished, hit its deadline, or was interrupted, no
        # probe child may outlive this process.
        kill_live_children()
    payload = {
        "generated_unix": int(time.time()),
        "catalogue_generated_unix": int(catalogue_unix),
        "catalogue_generation_id": "sha256:" + hashlib.sha256(raw_bytes).hexdigest(),
        "source_repo": str(source_repo_root()) if (source_repo_root() / ".git").exists() else "",
        "sweep_budget_seconds": float(args.sweep_budget),
        "models": models,
    }
    write_json_atomic(state_path, payload)
    print(json.dumps(summary_counts(models, routes_probed)))
    return 0


def exit_code(exc: SystemExit) -> int:
    """Translate an argparse SystemExit into a return code."""
    code = exc.code
    if code is None:
        return 0
    return code if isinstance(code, int) else 2


def main(argv: list[str] | None = None) -> int:
    """Dispatch the hidden child mode, or run the sweep."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    try:
        if "--probe-one" in args_list:
            return probe_one_main()
        return run_sweep(args_list)
    except KeyboardInterrupt:
        print("free-models-probe: interrupted", file=sys.stderr)
        return 130
    except SystemExit as exc:
        return exit_code(exc)
    except Exception:  # noqa: BLE001 - top-level guard for unexpected failures
        traceback.print_exc()
        return 4


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import asyncio
import random
import re
import time
from copy import deepcopy
from typing import Any, Awaitable, Callable

import httpx

from .config import settings
from .cache import TTLCache

# OpenRouter's free-tier model slugs rotate on their own schedule — three
# hardcoded slugs in this file 404'd within months of being set (see git
# history / the .env.example NOTE). Rather than keep guessing new slugs
# that will just die again, ask OpenRouter's own public model catalog
# (https://openrouter.ai/api/v1/models — no auth required) which models
# are currently free, and use those. Falls back to whatever's configured
# in OPENROUTER_MODELS only if the live lookup itself fails.
_openrouter_model_cache = TTLCache(ttl_seconds=1800)


async def _live_openrouter_free_models(client: httpx.AsyncClient) -> list[str]:
    cached = _openrouter_model_cache.get("free_models")
    if cached is not None:
        return cached
    try:
        response = await client.get("https://openrouter.ai/api/v1/models", timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return []
    free: list[str] = []
    for model in data.get("data", []) if isinstance(data, dict) else []:
        if not isinstance(model, dict):
            continue
        pricing = model.get("pricing") or {}
        model_id = model.get("id")
        try:
            is_free = float(pricing.get("prompt", 1)) == 0 and float(pricing.get("completion", 1)) == 0
        except (TypeError, ValueError):
            is_free = False
        if is_free and model_id and str(model_id).endswith(":free"):
            free.append(str(model_id))
    # Prefer larger/more capable-sounding free models first; this is a best
    # effort ordering, not a guarantee — OpenRouter doesn't rank by quality.
    free.sort(key=lambda m: ("70b" not in m and "72b" not in m, m))
    free = free[:6]
    if free:
        _openrouter_model_cache.set("free_models", free)
    return free


# =========================================================
# CONSTANTS
# =========================================================

JSON_RETRY_SUFFIX = (
    "\n\nReturn ONLY the JSON object requested by the schema. "
    "No markdown. No explanation. No code fences."
)

# Groq has an 8K TPM limit on the user's current tier.
#
# We therefore keep provider prompts bounded. This is NOT
# intended to replace good prompt engineering in agent.py;
# it is a safety net so a large research context cannot
# consume the entire provider budget.
MAX_GROQ_PROMPT_CHARS = 10000
MAX_OPENROUTER_PROMPT_CHARS = 14000
MAX_GOOGLE_PROMPT_CHARS = 14000


class ProviderError(RuntimeError):
    """A model provider failed or returned unusable output."""


# =========================================================
# PROMPT COMPACTION
# =========================================================

def _compact_prompt(
    prompt: str,
    max_chars: int,
) -> str:
    """
    Keep provider requests within predictable input size.

    We preserve the beginning and end because:
      - the beginning normally contains the task/context
      - the end often contains formatting/schema instructions

    This prevents accidentally sending enormous research
    contexts to a provider with a small TPM allowance.
    """

    if not isinstance(prompt, str):
        prompt = str(prompt)

    prompt = prompt.strip()

    if len(prompt) <= max_chars:
        return prompt

    # Preserve both ends.
    head_size = int(max_chars * 0.72)
    tail_size = max_chars - head_size

    return (
        prompt[:head_size]
        + "\n\n[CONTEXT COMPACTED FOR PROVIDER LIMITS]\n\n"
        + prompt[-tail_size:]
    )


# =========================================================
# JSON PARSING
# =========================================================

def _extract_json(
    text: str,
) -> dict[str, Any]:

    if not isinstance(
        text,
        str,
    ):
        raise ValueError(
            "Model returned non-text output."
        )

    text = text.strip()

    if not text:
        raise ValueError(
            "Model returned empty output."
        )

    # -----------------------------------------------------
    # Handle accidental markdown fences.
    # -----------------------------------------------------

    fence = re.search(
        r"```(?:json)?\s*(.*?)```",
        text,
        re.DOTALL | re.IGNORECASE,
    )

    if fence:
        text = fence.group(1).strip()

    # -----------------------------------------------------
    # Direct JSON
    # -----------------------------------------------------

    try:

        value = json.loads(
            text
        )

    except json.JSONDecodeError:

        # -------------------------------------------------
        # Recover JSON object embedded in text.
        # -------------------------------------------------

        start = text.find("{")
        end = text.rfind("}")

        if (
            start < 0
            or end <= start
        ):

            raise ValueError(
                f"No JSON object found: {text[:400]}"
            )

        try:

            value = json.loads(
                text[
                    start : end + 1
                ]
            )

        except json.JSONDecodeError as exc:

            raise ValueError(
                f"Malformed JSON: {text[:600]}"
            ) from exc

    if not isinstance(
        value,
        dict,
    ):

        raise ValueError(
            "Model returned JSON, "
            "but the top level is not an object."
        )

    return value


# =========================================================
# PROVIDER ERROR PARSING
# =========================================================

def _error_detail(
    response: httpx.Response,
) -> str:

    try:

        data = response.json()

        if isinstance(
            data,
            dict,
        ):

            error = data.get(
                "error"
            )

            if isinstance(
                error,
                dict,
            ):

                message = error.get(
                    "message"
                )

                code = error.get(
                    "code"
                )

                if message:

                    if code:

                        return (
                            f"{message} "
                            f"({code})"
                        )

                    return str(
                        message
                    )

            if isinstance(
                error,
                str,
            ):

                return error

        return response.text[:700]

    except Exception:

        return response.text[:700]


# =========================================================
# GROQ SCHEMA
# =========================================================

def _groq_schema(
    schema: dict[str, Any],
) -> dict[str, Any]:

    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema["name"],
            "strict": True,
            "schema": schema["schema"],
        },
    }


# =========================================================
# GROQ
# =========================================================

async def _groq_call(
    client: httpx.AsyncClient,
    prompt: str,
    system: str,
    schema: dict[str, Any] | None = None,
) -> str:

    if not settings.GROQ_API_KEY:

        raise ProviderError(
            "GROQ_API_KEY is not configured."
        )

    # -----------------------------------------------------
    # IMPORTANT:
    #
    # Groq's TPM limit includes prompt + completion.
    # Bound the input before sending it.
    # -----------------------------------------------------

    prompt = _compact_prompt(
        prompt,
        MAX_GROQ_PROMPT_CHARS,
    )

    system = _compact_prompt(
        system,
        4000,
    )

    models_to_try = settings.GROQ_MODELS or [settings.GROQ_MODEL]
    errors: list[str] = []

    for model in models_to_try:

        payload: dict[str, Any] = {

            "model": model,

            "messages": [
                {
                    "role": "system",
                    "content": system,
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],

            "reasoning_effort": "low",

            "include_reasoning": False,

            "temperature": 0.1,

            "max_completion_tokens": int(
                settings.GROQ_MAX_COMPLETION_TOKENS
            ),

            "stream": False,
        }

        if schema:

            payload[
                "response_format"
            ] = _groq_schema(
                schema
            )

        else:

            payload[
                "response_format"
            ] = {
                "type": "json_object"
            }

        try:

            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": (
                        f"Bearer "
                        f"{settings.GROQ_API_KEY}"
                    ),
                    "Content-Type": (
                        "application/json"
                    ),
                },
                json=payload,
                timeout=30,
            )

        except httpx.TimeoutException:

            errors.append(f"{model}: timeout")
            continue

        except httpx.RequestError as exc:

            errors.append(f"{model}: connection error: {exc}")
            continue

        # -------------------------------------------------
        # 429 on one Groq model: try the NEXT Groq model
        # (different models have independent daily/TPM
        # budgets) before giving up on Groq entirely.
        # -------------------------------------------------

        if response.status_code == 429:

            retry_after = response.headers.get("retry-after")
            suffix = f"; retry-after={retry_after}s" if retry_after else ""
            errors.append(
                f"{model}: rate limited{suffix}: {_error_detail(response)}"
            )
            continue

        if response.status_code >= 400:

            errors.append(
                f"{model}: HTTP {response.status_code}: {_error_detail(response)}"
            )
            continue

        try:

            data = response.json()

            message = (
                data[
                    "choices"
                ][0]["message"]
            )

            content = message.get(
                "content"
            )

        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ):

            errors.append(
                f"{model}: unexpected response: {response.text[:400]}"
            )
            continue

        if not content:

            errors.append(f"{model}: empty content")
            continue

        return str(
            content
        )

    # -----------------------------------------------------
    # All Groq models failed. If every failure was a 429,
    # surface "rate limit" so the router puts Groq as a
    # whole into cooldown; otherwise it's a real error.
    # -----------------------------------------------------

    if errors and all("rate limited" in e for e in errors):

        retry = re.search(r"retry-after=(\d+)s", errors[0])
        suffix = f"; retry-after={retry.group(1)}s" if retry else ""
        raise ProviderError(
            f"Groq rate limit reached{suffix}: " + " | ".join(errors)
        )

    raise ProviderError(
        "All Groq models failed:\n" + "\n".join(errors)
    )


# =========================================================
# OPENROUTER
# =========================================================

async def _openrouter_call(
    client: httpx.AsyncClient,
    prompt: str,
    system: str,
    schema: dict[str, Any] | None = None,
) -> str:

    if not settings.OPENROUTER_API_KEY:

        raise ProviderError(
            "OPENROUTER_API_KEY is not configured."
        )

    prompt = _compact_prompt(
        prompt,
        MAX_OPENROUTER_PROMPT_CHARS,
    )

    system = _compact_prompt(
        system,
        5000,
    )

    errors: list[str] = []

    # settings.OPENROUTER_MODELS starts with "openrouter/free" (OpenRouter's
    # own auto-router, which can't 404 the way a hardcoded slug does) — try
    # that and any other explicitly configured slugs FIRST, then fall back
    # to the live free-model catalog lookup for extra options.
    live_models = await _live_openrouter_free_models(client)
    models_to_try = list(dict.fromkeys(settings.OPENROUTER_MODELS + live_models))
    if not models_to_try:
        raise ProviderError(
            "No OpenRouter free models available (live lookup failed and "
            "OPENROUTER_MODELS is empty)."
        )

    for model in models_to_try:

        payload: dict[str, Any] = {

            "model": model,

            "messages": [
                {
                    "role": "system",
                    "content": system,
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],

            "temperature": 0.1,

            "max_tokens": int(
                settings.OPENROUTER_MAX_TOKENS
            ),

            # Suppress chain-of-thought/"thinking" tokens on reasoning-style
            # free models. Without this, a model can spend its entire token
            # budget on a preamble ("Here's a thinking process: ...") and
            # get cut off before ever emitting the JSON answer — this was
            # the exact cause of the "Malformed JSON" errors in production.
            # Unified across providers per OpenRouter's reasoning-tokens
            # docs; models that don't support it simply ignore the field.
            "reasoning": {
                "effort": "none",
                "exclude": True,
            },

            # Prevent tool-call garbage.
            "tool_choice": "none",

            "stream": False,
        }

        if schema:

            payload[
                "response_format"
            ] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema["name"],
                    "strict": True,
                    "schema": schema["schema"],
                },
            }

        else:

            payload[
                "response_format"
            ] = {
                "type": "json_object"
            }

        try:

            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": (
                        f"Bearer "
                        f"{settings.OPENROUTER_API_KEY}"
                    ),
                    "Content-Type": (
                        "application/json"
                    ),
                    "HTTP-Referer": (
                        "https://scout-agent.local"
                    ),
                    "X-Title": "Scout Agent",
                },
                json=payload,
                timeout=35,
            )

        except httpx.TimeoutException:

            errors.append(
                f"{model}: timeout"
            )

            continue

        except httpx.RequestError as exc:

            errors.append(
                f"{model}: "
                f"connection error: {exc}"
            )

            continue

        # -------------------------------------------------
        # Any 429 is passed to the router.
        # Do not waste another request.
        # -------------------------------------------------

        if response.status_code == 429:

            errors.append(
                f"{model}: HTTP 429 "
                f"rate limited: "
                f"{_error_detail(response)}"
            )

            continue

        if response.status_code >= 400:

            errors.append(
                f"{model}: HTTP "
                f"{response.status_code}: "
                f"{_error_detail(response)}"
            )

            continue

        try:

            data = response.json()

            content = (
                data[
                    "choices"
                ][0]["message"].get(
                    "content"
                )
            )

        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ):

            errors.append(
                f"{model}: malformed API response: "
                f"{response.text[:400]}"
            )

            continue

        if not content:

            errors.append(
                f"{model}: empty content"
            )

            continue

        return str(
            content
        )

    raise ProviderError(
        "All OpenRouter models failed:\n"
        + "\n".join(errors)
    )


# =========================================================
# GOOGLE GEMINI
# =========================================================

def _google_response_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert our strict OpenAI-compatible schemas to Gemini's REST shape.

    Scout keeps ``additionalProperties: false`` for providers that enforce
    strict schemas. The deployed Gemini GenerateContent endpoint rejects that
    keyword in nested response-schema objects, so sending the shared schema
    directly makes Gemini unavailable as a fallback. Removing only that
    provider-incompatible constraint preserves every field, type, bound and
    required-property quality guard; final application validation remains the
    authoritative safety boundary.
    """
    converted = deepcopy(schema)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("additionalProperties", None)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(converted)
    return converted

async def _google_call(
    client: httpx.AsyncClient,
    prompt: str,
    system: str,
    schema: dict[str, Any] | None = None,
) -> str:

    if not settings.GOOGLE_API_KEY:

        raise ProviderError(
            "GOOGLE_API_KEY is not configured."
        )

    prompt = _compact_prompt(
        prompt,
        MAX_GOOGLE_PROMPT_CHARS,
    )

    system = _compact_prompt(
        system,
        5000,
    )

    generation_config: dict[str, Any] = {

        "temperature": 0.1,

        "maxOutputTokens": int(
            settings.GOOGLE_MAX_OUTPUT_TOKENS
        ),

        # Disable Gemini's internal "thinking" budget for these structured
        # extraction/ranking calls — same fix as Groq's reasoning_effort and
        # OpenRouter's reasoning.effort above: without it, a thinking-enabled
        # Gemini model can burn its output budget on internal reasoning and
        # never emit the actual JSON.
        "thinkingConfig": {
            "thinkingBudget": 0
        },

        "responseMimeType": (
            "application/json"
        ),
    }

    if schema:

        generation_config[
            "responseSchema"
        ] = _google_response_schema(schema["schema"])

    models_to_try = settings.GOOGLE_MODELS or [settings.GOOGLE_MODEL]
    errors: list[str] = []

    for model in models_to_try:

        try:

            response = await client.post(
                (
                    "https://generativelanguage.googleapis.com/"
                    "v1beta/models/"
                    f"{model}:generateContent"
                ),
                params={
                    "key": settings.GOOGLE_API_KEY
                },
                json={
                    "contents": [
                        {
                            "parts": [
                                {
                                    "text": (
                                        f"{system}\n\n"
                                        f"{prompt}\n\n"
                                        "Return only the requested "
                                        "JSON object."
                                    )
                                }
                            ]
                        }
                    ],
                    "generationConfig": (
                        generation_config
                    ),
                },
                timeout=35,
            )

        except httpx.TimeoutException:

            errors.append(f"{model}: timeout")
            continue

        except httpx.RequestError as exc:

            errors.append(f"{model}: connection error: {exc}")
            continue

        # A 429 or 404 on one Gemini model (e.g. a new preview model with a
        # tiny free-tier daily quota, or a retired model name) should try
        # the NEXT Gemini model, not immediately give up on Google as a
        # whole provider.
        if response.status_code == 429:

            errors.append(f"{model}: rate limited: {_error_detail(response)}")
            continue

        if response.status_code >= 400:

            errors.append(
                f"{model}: HTTP {response.status_code}: {_error_detail(response)}"
            )
            continue

        try:

            data = response.json()

            parts = (
                data[
                    "candidates"
                ][0][
                    "content"
                ][
                    "parts"
                ]
            )

            content = "".join(
                str(
                    part.get(
                        "text",
                        "",
                    )
                )
                for part in parts
            )

        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ):

            errors.append(f"{model}: unexpected response: {response.text[:400]}")
            continue

        if not content:

            errors.append(f"{model}: empty content")
            continue

        return content

    if errors and all("rate limited" in e for e in errors):
        raise ProviderError(
            "Google rate limit reached: " + " | ".join(errors)
        )

    raise ProviderError(
        "All Google models failed:\n" + "\n".join(errors)
    )


# =========================================================
# OLLAMA (local — zero rate limit, zero cost, zero network dependency)
# =========================================================

async def _ollama_call(
    client: httpx.AsyncClient,
    prompt: str,
    system: str,
    schema: dict[str, Any] | None = None,
) -> str:

    if not settings.OLLAMA_ENABLED:
        raise ProviderError(
            "Ollama is not enabled (set OLLAMA_ENABLED=true in .env)."
        )

    payload: dict[str, Any] = {
        "model": settings.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.1},
        # Ollama's own structured-output mode — forces the model to emit
        # syntactically valid JSON, the same guarantee response_format
        # gives on the cloud providers above.
        "format": "json",
    }

    try:
        response = await client.post(
            f"{settings.OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=120,  # local inference on CPU can be slow; no per-minute
                          # rate limit exists here, so a generous timeout is
                          # the only real constraint.
        )
    except httpx.ConnectError as exc:
        raise ProviderError(
            f"Ollama is enabled but not reachable at {settings.OLLAMA_BASE_URL} "
            "— is `ollama serve` running?"
        ) from exc

    if response.status_code == 404:
        raise ProviderError(
            f"Ollama model '{settings.OLLAMA_MODEL}' is not pulled locally. "
            f"Run: ollama pull {settings.OLLAMA_MODEL}"
        )
    response.raise_for_status()

    content = (response.json().get("message") or {}).get("content", "")
    if not content:
        raise ProviderError("Ollama returned empty content.")
    return content


# =========================================================
# PROVIDER FUNCTION TYPE
# =========================================================

ProviderFn = Callable[
    [
        httpx.AsyncClient,
        str,
        str,
        dict[str, Any] | None,
    ],
    Awaitable[str],
]


# =========================================================
# ROUTER
# =========================================================

class ModelRouter:

    _health: dict[str, dict[str, Any]] = {}
    _health_lock = asyncio.Lock()

    def __init__(self):
        # A transient TCP/TLS failure used to immediately consume a provider
        # in every fallback chain.  Keep retries at the transport layer so a
        # request is retried before Scout declares that provider unavailable.
        # httpx retries only connection failures (never 4xx responses), which
        # avoids masking invalid credentials, schema errors, or rate limits.
        self.client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(retries=3),
            timeout=httpx.Timeout(connect=10.0, read=45.0, write=30.0, pool=15.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

        # A provider that tells us to slow down is skipped by later chains in
        # the same Scout run. This prevents fallback storms from turning one
        # exhausted quota into several duplicate failures.
        self._cooldowns: dict[str, float] = {}

        # The actual per-provider error text from the most recent failed
        # chain (e.g. "Groq: ProviderError: GROQ_API_KEY is not configured.").
        # Surfaced into the UI's degraded-mode banner so a config/quota
        # problem doesn't just look like an unexplained "unavailable".
        self.last_error: str = ""

    @classmethod
    async def _record_health(cls, provider: str, *, success: bool, error: str = "") -> None:
        async with cls._health_lock:
            state = cls._health.setdefault(provider, {"failure_count": 0, "success_count": 0, "cooldown_until": 0.0, "last_error": "", "last_status": None, "last_success_at": None, "last_failure_at": None})
            now = time.monotonic()
            if success:
                state["success_count"] += 1; state["failure_count"] = 0; state["last_success_at"] = now
                return
            state["failure_count"] += 1; state["last_error"] = error[:300]; state["last_failure_at"] = now
            if any(token in error.lower() for token in ("timeout", "connection", "network", "rate limit", "http 429", "http 5")):
                state["cooldown_until"] = now + min(60, (2 ** min(state["failure_count"], 5)) + random.uniform(0, .75))

    @classmethod
    async def _available(cls, provider: str) -> bool:
        async with cls._health_lock:
            return cls._health.get(provider, {}).get("cooldown_until", 0.0) <= time.monotonic()

    async def groq_decision(self, prompt: str, system: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Sole non-Anakin decision fallback; never routes to other models."""
        if not await self._available("groq"):
            raise ProviderError("Groq is in cooldown.")
        started = time.monotonic()
        try:
            result = await self._call_with_json_retry(_groq_call, prompt, system, schema)
        except Exception as exc:
            await self._record_health("groq", success=False, error=str(exc))
            raise
        await self._record_health("groq", success=True)
        print(f"[AI] provider=groq operation=decision status=success latency_ms={round((time.monotonic()-started)*1000)}")
        return result

    async def close(self):

        await self.client.aclose()

    # =====================================================
    # JSON CALL
    # =====================================================

    async def _call_with_json_retry(
        self,
        call_fn: ProviderFn,
        prompt: str,
        system: str,
        schema: dict[str, Any] | None,
    ) -> dict[str, Any]:

        raw = await call_fn(
            self.client,
            prompt,
            system,
            schema,
        )

        try:

            return _extract_json(
                raw
            )

        except ValueError as first_error:

            # -------------------------------------------------
            # Only malformed JSON gets a same-provider retry.
            #
            # 429 / timeout / connection / HTTP errors are
            # already ProviderError and are NOT retried here.
            # -------------------------------------------------

            try:

                raw_retry = await call_fn(
                    self.client,
                    prompt
                    + JSON_RETRY_SUFFIX,
                    system,
                    schema,
                )

                return _extract_json(
                    raw_retry
                )

            except Exception as retry_error:

                raise ProviderError(
                    "Invalid JSON from provider. "
                    f"First: {first_error}; "
                    f"retry: {retry_error}"
                ) from retry_error

    # =====================================================
    # PROVIDER CHAIN
    # =====================================================

    async def _try_chain(
        self,
        prompt: str,
        system: str,
        schema: dict[str, Any] | None,
        providers: list[
            tuple[str, ProviderFn]
        ],
    ) -> dict[str, Any]:

        errors: list[str] = []

        for name, fn in providers:

            cooldown_until = self._cooldowns.get(name, 0.0)
            if cooldown_until > time.monotonic():
                remaining = max(1, round(cooldown_until - time.monotonic()))
                errors.append(f"{name}: temporarily rate limited ({remaining}s remaining)")
                continue

            try:

                result = await (
                    self._call_with_json_retry(
                        fn,
                        prompt,
                        system,
                        schema,
                    )
                )

                print(f"[AI] provider={name} status=success")
                return result

            except Exception as exc:

                message = str(exc)
                if "rate limit" in message.lower() or "HTTP 429" in message:
                    retry = re.search(r"retry-after=(\d+)s", message, re.IGNORECASE)
                    # APIs sometimes omit Retry-After; sixty seconds is a
                    # conservative bounded cooldown for a TPM window.
                    delay = int(retry.group(1)) if retry else 60
                    self._cooldowns[name] = time.monotonic() + max(1, delay)

                print(f"[AI] provider={name} status=FAILED error={type(exc).__name__}: {exc}")

                errors.append(
                    f"{name}: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

                # Immediately fail over.
                continue

        print("[AI] ALL PROVIDERS IN CHAIN FAILED:\n" + "\n".join(errors))
        self.last_error = " | ".join(errors)
        raise RuntimeError(
            "All model providers failed:\n"
            + "\n".join(errors)
        )

    # =====================================================
    # FAST
    # =====================================================

    async def fast(
        self,
        prompt: str,
        system: str,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        return await self._try_chain(
            prompt,
            system,
            schema,
            [
                (
                    "Ollama",
                    _ollama_call,
                ),
                (
                    "Groq",
                    _groq_call,
                ),
                (
                    "OpenRouter",
                    _openrouter_call,
                ),
                (
                    "Google",
                    _google_call,
                ),
            ],
        )

    # =====================================================
    # DEEP
    # =====================================================

    async def deep(
        self,
        prompt: str,
        system: str,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        return await self._try_chain(
            prompt,
            system,
            schema,
            [
                (
                    "Ollama",
                    _ollama_call,
                ),
                (
                    "OpenRouter",
                    _openrouter_call,
                ),
                (
                    "Google",
                    _google_call,
                ),
                (
                    "Groq",
                    _groq_call,
                ),
            ],
        )

    # =====================================================
    # ENSEMBLE B
    # =====================================================

    async def ensemble_b(
        self,
        prompt: str,
        system: str,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        return await self._try_chain(
            prompt,
            system,
            schema,
            [
                (
                    "Google",
                    _google_call,
                ),
                (
                    "OpenRouter",
                    _openrouter_call,
                ),
                (
                    "Groq",
                    _groq_call,
                ),
                (
                    "Ollama",
                    _ollama_call,
                ),
            ],
        )

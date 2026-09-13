import os

from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name, str(default)).strip()

    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"{name} must be an integer, got {raw!r}"
        ) from exc

    if value < minimum:
        raise RuntimeError(
            f"{name} must be >= {minimum}, got {value}"
        )

    return value


class Settings:
    # =========================================================
    # Existing providers
    # =========================================================

    SERPER_API_KEY = os.getenv(
        "SERPER_API_KEY",
        "",
    ).strip()

    GROQ_API_KEY = os.getenv(
        "GROQ_API_KEY",
        "",
    ).strip()

    OPENROUTER_API_KEY = os.getenv(
        "OPENROUTER_API_KEY",
        "",
    ).strip()

    GOOGLE_API_KEY = os.getenv(
        "GOOGLE_API_KEY",
        "",
    ).strip()

    DISCORD_WEBHOOK_URL = os.getenv(
        "DISCORD_WEBHOOK_URL",
        "",
    ).strip()

    # =========================================================
    # Razorpay test checkout and email confirmations
    # =========================================================

    RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
    RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
    RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()
    # Account enablement is distinct from currencies Scout understands.
    RAZORPAY_ENABLED_CURRENCIES = {
        value.strip().upper()
        for value in os.getenv("RAZORPAY_ENABLED_CURRENCIES", "INR").split(",")
        if value.strip()
    }

    BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()
    EMAIL_FROM = os.getenv("EMAIL_FROM", "").strip()

    # =========================================================
    # Anakin
    # =========================================================

    ANAKIN_BASE_URL = os.getenv(
        "ANAKIN_BASE_URL",
        "https://api.anakin.io/v1",
    ).strip().rstrip("/")

    ANAKIN_API_KEY = os.getenv(
        "ANAKIN_API_KEY",
        "",
    ).strip()

    # Our own per-session safety limits.
    #
    # Search is currently intentionally limited to ONE paid
    # search per Scout run.
    ANAKIN_MAX_SEARCH_CALLS = _int_env(
        "ANAKIN_MAX_SEARCH_CALLS",
        1,
    )

    # Was 1 — now used by two different call sites in a single Scout run:
    # the candidate-extraction fallback (when all 3 LLM providers fail) and
    # the last-resort search (when candidates are still empty after that).
    # Each Agentic Search job is its own budgeted, billed Anakin call, so
    # this is a real spend increase — raise ANAKIN_MAX_AGENTIC_SEARCH_CALLS
    # back down in your .env if you want to cap it at 1.
    ANAKIN_MAX_AGENTIC_SEARCH_CALLS = _int_env(
        "ANAKIN_MAX_AGENTIC_SEARCH_CALLS",
        2,
    )

    ANAKIN_MAX_SCRAPE_CALLS = _int_env(
        "ANAKIN_MAX_SCRAPE_CALLS",
        2,
    )

    ANAKIN_MAX_WIRE_TASK_CALLS = _int_env(
        "ANAKIN_MAX_WIRE_TASK_CALLS",
        1,
    )

    # =========================================================
    # LLM models
    # =========================================================

    # Groq's daily-token budget is set PER MODEL, not per account, so a
    # single hardcoded model (previously just "openai/gpt-oss-20b", TPD
    # limit 200,000) exhausts itself on real usage and then stays dead for
    # the rest of the day even though Groq has other models with much
    # bigger free-tier budgets. Try several, cheapest/highest-quota first:
    #   llama-3.1-8b-instant   — 14,400 req/day   (by far the biggest budget)
    #   openai/gpt-oss-20b     — 1,000 req/day, 200,000 tokens/day
    #   llama-3.3-70b-versatile— 1,000 req/day, higher per-request quality
    # Verify current numbers at https://console.groq.com/docs/rate-limits
    # since Groq can change these independent of this codebase.
    # Groq deprecated llama-3.1-8b-instant and llama-3.3-70b-versatile on
    # 2026-06-17 (see https://console.groq.com/docs/deprecations) — both now
    # 404. Current production models, cheapest/highest-quota first:
    #   openai/gpt-oss-120b     — best quality, Groq's recommended default
    #   openai/gpt-oss-20b      — smaller budget (200k tokens/day), use 2nd
    #   moonshotai/kimi-k2-instruct — third independent option
    # Verify current numbers/names at https://console.groq.com/docs/models
    # since Groq can change these independent of this codebase.
    GROQ_MODELS = [
        model.strip()
        for model in os.getenv(
            "GROQ_MODELS",
            "openai/gpt-oss-120b,"
            "openai/gpt-oss-20b,"
            "moonshotai/kimi-k2-instruct",
        ).split(",")
        if model.strip()
    ]
    # Kept for logging/health-check display and any other call site that
    # only wants "the" Groq model name.
    GROQ_MODEL = GROQ_MODELS[0] if GROQ_MODELS else "openai/gpt-oss-120b"

    # OpenRouter's free-tier slug lineup rotates fast (models get pulled to
    # paid-only or replaced with no notice) — that's WHY "openrouter/free"
    # (OpenRouter's own auto-router, picks a currently-live free model
    # server-side) is tried FIRST and is the actually-durable part of this
    # chain. The explicit slugs below are a secondary attempt; expect them
    # to occasionally 404 and need refreshing against
    # https://openrouter.ai/models?max_price=0 — that's a config change,
    # not a code bug.
    OPENROUTER_MODELS = [
        model.strip()
        for model in os.getenv(
            "OPENROUTER_MODELS",
            "openrouter/free,"
            "x-ai/grok-4.1-fast:free,"
            "meta-llama/llama-3.3-70b-instruct:free",
        ).split(",")
        if model.strip()
    ]

    # NOTE: os.getenv("GOOGLE_MODEL", "gemini-2.5-flash") only falls back to
    # the default when the env var is ABSENT. .env.example ships
    # "GOOGLE_MODEL=" (present, empty), so the fallback never applied and
    # every Google call hit .../models/:generateContent with no model name.
    # Treat empty string the same as unset.
    #
    # gemini-2.5-flash-lite and gemini-2.0-flash-lite are RETIRED (both
    # 404 as of Sept 2026 — Google's error message points to
    # gemini-3.5-flash-lite as the replacement). Current free-tier,
    # rate-limited models, cheapest/highest-quota first:
    #   gemini-3.1-flash-lite — Google's most cost-efficient current model
    #   gemini-3.5-flash-lite — Google's suggested migration target
    #   gemini-3.5-flash      — higher quality, still free with rate limits
    # Verify current numbers at https://ai.google.dev/gemini-api/docs/rate-limits
    # since Google can change these independent of this codebase.
    GOOGLE_MODELS = [
        model.strip()
        for model in os.getenv(
            "GOOGLE_MODELS",
            "gemini-3.1-flash-lite,"
            "gemini-3.5-flash-lite,"
            "gemini-3.5-flash",
        ).split(",")
        if model.strip()
    ]
    # Backward-compatible single-name form. GOOGLE_MODEL, if explicitly set
    # in .env, is treated as an override and put first in the chain.
    _google_model_override = os.getenv("GOOGLE_MODEL", "").strip()
    if _google_model_override:
        GOOGLE_MODELS = [_google_model_override] + [
            m for m in GOOGLE_MODELS if m != _google_model_override
        ]
    GOOGLE_MODEL = GOOGLE_MODELS[0]

    # Optional local model (Ollama) — zero rate limit, zero cost, zero
    # network dependency, since it runs on your own machine. When enabled,
    # it is tried FIRST in every chain, ahead of every cloud provider above.
    # Install: https://ollama.com, then `ollama pull llama3.1:8b` (or any
    # model you prefer) and leave the default OLLAMA_BASE_URL as-is.
    OLLAMA_ENABLED = os.getenv("OLLAMA_ENABLED", "false").strip().lower() in ("1", "true", "yes")
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip()
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b").strip()

    # =========================================================
    # Token budgets
    # =========================================================

    # 700 tokens was too small: a "thinking"/reasoning-style free model can
    # spend its entire budget on chain-of-thought preamble and get cut off
    # before emitting any JSON at all (this is what "Malformed JSON: Here's
    # a thinking process..." in the logs actually was). 1800 leaves room for
    # both a reasoning preamble AND a real multi-candidate JSON answer.
    GROQ_MAX_COMPLETION_TOKENS = _int_env(
        "GROQ_MAX_COMPLETION_TOKENS",
        1800,
        minimum=64,
    )

    OPENROUTER_MAX_TOKENS = _int_env(
        "OPENROUTER_MAX_TOKENS",
        1800,
        minimum=64,
    )

    GOOGLE_MAX_OUTPUT_TOKENS = _int_env(
        "GOOGLE_MAX_OUTPUT_TOKENS",
        1800,
        minimum=64,
    )

    # =========================================================
    # Agent
    # =========================================================

    MAX_ITERATIONS = _int_env(
        "MAX_ITERATIONS",
        3,
        minimum=1,
    )

    # Serper results per query / how many of those get a full-page Jina fetch.
    # 5/3 was too thin to reliably surface a top-10 list across 3-6 queries.
    SEARCH_RESULTS_PER_QUERY = _int_env("SEARCH_RESULTS_PER_QUERY", 8, minimum=1)
    FETCH_DEPTH_PER_QUERY = _int_env("FETCH_DEPTH_PER_QUERY", 5, minimum=1)

    # Locale hint for Serper (gl=country, hl=language). Indian shorthand like
    # "3k"/"60k" implies INR pricing and Indian retailers/reviewers; without
    # this, Serper often returns US-centric results with mismatched currency.
    SEARCH_COUNTRY = os.getenv("SEARCH_COUNTRY", "in").strip().lower()
    SEARCH_LANGUAGE = os.getenv("SEARCH_LANGUAGE", "en").strip().lower()
    DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "INR").strip().upper()

    CACHE_TTL_SECONDS = _int_env(
        "CACHE_TTL_SECONDS",
        3600,
        minimum=0,
    )

    # =========================================================
    # Reports
    # =========================================================

    REPORTS_DIR = os.getenv(
        "REPORTS_DIR",
        "./reports",
    ).strip()

    # =========================================================
    # Frontend
    # =========================================================

    FRONTEND_ORIGIN = os.getenv(
        "FRONTEND_ORIGIN",
        "http://localhost:5173",
    ).strip()


settings = Settings()


os.makedirs(
    settings.REPORTS_DIR,
    exist_ok=True,
)

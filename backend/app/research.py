"""Research planning and source-quality utilities.

Two things used to be done with ~700 lines of regex/keyword heuristics
(_detect_task_type, _detect_geography, _detect_budget, _criteria_for, ...):
turning a goal into a structured brief, and turning a brief into search
queries. Both are now single structured LLM calls — more accurate than
keyword matching, and a fraction of the code.

Budget is the one exception: "under 3k" / "under 60k" style ceilings are
parsed deterministically (see parse_budget_ceiling) and enforced as a hard
filter, rather than left as a free-text hint an LLM might or might not
respect. Everything else stays LLM-driven.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .config import settings

HIGH_QUALITY_HINTS = (
    "rtings", "soundguys", "whathifi", "theverge", "techradar", "tomsguide",
    "gadgets360", "91mobiles", "digit.in", "notebookcheck", "consumerreports",
    "pcmag", "cnet", "wired", ".gov", ".edu",
)
RETAIL_HINTS = (
    "amazon", "flipkart", "bestbuy", "walmart", "target", "ebay",
    "booking.com", "expedia", "skyscanner", "makemytrip",
)
LOW_VALUE_HOSTS = {"pinterest.com", "quora.com"}

BRIEF_SYSTEM = (
    "You turn a user's research goal into a structured research brief for an "
    "AI agent that will search the live web. Respond with ONLY this JSON shape: "
    '{"task_type": "product|flight|hotel|ticket|restaurant|service|general_research", '
    '"objective": "one sentence restating the goal", '
    '"geography": string or null, "budget": string or null, "date_constraint": string or null, '
    '"candidate_count": integer between 8 and 12 — Scout always aims to surface a full top-10 list, '
    'so ask for at least 8 distinct real candidates whenever the goal category plausibly has that many, '
    '"criteria": [3 to 6 short strings — what actually distinguishes good options here], '
    '"evidence_requirements": [2 to 5 short strings — facts that must be verified before trusting a candidate, '
    'e.g. "current price", "availability", "real review coverage"]}'
)

QUERY_SYSTEM = (
    "You are a search query planner for a research agent. Given a research brief, produce 3 to 6 "
    "distinct web search queries that together will surface real, named candidates plus the evidence "
    "needed to compare them on the given criteria. Vary the angle of each query — identity/best-of, "
    "price, review/quality, availability. If a hard budget ceiling is given, at least one query must "
    "include that ceiling explicitly (e.g. 'best X under <amount> <currency>') so search results are "
    "actually filtered by price, not just generic best-of lists. Do not repeat any query already tried. "
    'Respond with ONLY this JSON shape: {"queries": ["...", ...]}'
)

CANDIDATE_SYSTEM = (
    "You extract real, named candidates from web content for a research agent. Given a research brief "
    "and freshly fetched web sources, extract every distinct real-world candidate (a specific product, "
    "flight, hotel, ticket, restaurant, or service — never a generic category or an article title). "
    "Extract as many distinct real candidates as the sources actually support — the agent wants a full "
    "top-10 list, so do not stop at one or two if the sources mention more. "
    "For each, extract only what the sources actually state — never invent a price, url, or fact. "
    "If a hard budget ceiling is given, still extract candidates above it (a later filter decides what "
    "to keep) but never invent a lower price to make something appear to fit. "
    'Respond with ONLY this JSON shape: {"candidates": [{'
    '"name": str, "source_url": str (must be one of the given source URLs), '
    '"reason": "one sentence on why this is a real candidate", '
    '"price": number or null, "currency": "USD|INR|EUR|GBP" or null, '
    '"price_verified": true only if THIS source explicitly states that exact numeric price for this exact '
    "candidate (never true for a vague, estimated, or 'starting from' price on a different item), "
    '"purchase_url": str or null — a direct link to buy/book this exact candidate (the retailer, airline, '
    "hotel, ticketing, or booking page itself — not an article, review, or search-results page), "
    '"availability": str or null, '
    '"availability_verified": true only if THIS source explicitly confirms this candidate is currently '
    "purchasable/bookable (in stock, seats/rooms open, tickets on sale) — false or omitted otherwise, "
    '"evidence": [{"criterion": str, "claim": str, "source_url": str, "confidence": number 0-1}]'
    "}]}"
)


@dataclass
class ResearchBrief:
    task_type: str = "general_research"
    objective: str = ""
    geography: str | None = None
    budget: str | None = None
    date_constraint: str | None = None
    candidate_count: int = 10
    criteria: list[str] = field(default_factory=lambda: ["relevance", "evidence quality"])
    evidence_requirements: list[str] = field(
        default_factory=lambda: ["candidate exists", "claim-level evidence"]
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type, "objective": self.objective,
            "geography": self.geography, "budget": self.budget,
            "date_constraint": self.date_constraint, "candidate_count": self.candidate_count,
            "criteria": self.criteria, "evidence_requirements": self.evidence_requirements,
        }


def infer_search_locale(goal: str, geography: str | None = None) -> tuple[str, str]:
    """Pick a Serper gl/hl pair from the goal's OWN currency/geography signal.

    Previously this was a fixed config default (accidentally "in" for
    everyone), which broke non-Indian queries like "under $300" by forcing
    Indian search results onto a USD budget. Detect per-query instead, and
    only fall back to the configured default when nothing in the goal says
    otherwise.
    """
    text = goal or ""
    if "₹" in text or re.search(r"\b(?:inr|rs\.?|rupees)\b", text, re.IGNORECASE):
        return "in", "en"
    if "$" in text or re.search(r"\busd\b", text, re.IGNORECASE):
        return "us", "en"
    if "€" in text or re.search(r"\beur\b", text, re.IGNORECASE):
        return "de", "en"
    if "£" in text or re.search(r"\bgbp\b", text, re.IGNORECASE):
        return "gb", "en"
    if geography:
        geo = geography.lower()
        if "india" in geo:
            return "in", "en"
        if "us" in geo or "united states" in geo or "america" in geo:
            return "us", "en"
        if "uk" in geo or "united kingdom" in geo or "britain" in geo:
            return "gb", "en"
    return settings.SEARCH_COUNTRY, settings.SEARCH_LANGUAGE


async def build_research_brief(goal: str, router: Any) -> ResearchBrief:
    try:
        raw = await router.fast(goal, BRIEF_SYSTEM)
    except Exception as exc:
        print(f"[research] build_research_brief: all providers failed: {exc}")
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    criteria = [str(c)[:60] for c in (raw.get("criteria") or []) if str(c).strip()][:6]
    evidence = [str(c)[:80] for c in (raw.get("evidence_requirements") or []) if str(c).strip()][:6]
    try:
        candidate_count = max(8, min(12, int(raw.get("candidate_count") or 10)))
    except (TypeError, ValueError):
        candidate_count = 10
    return ResearchBrief(
        task_type=str(raw.get("task_type") or "general_research"),
        objective=str(raw.get("objective") or goal)[:400],
        geography=(str(raw["geography"]) if raw.get("geography") else None),
        budget=(str(raw["budget"]) if raw.get("budget") else None),
        date_constraint=(str(raw["date_constraint"]) if raw.get("date_constraint") else None),
        candidate_count=candidate_count,
        criteria=criteria or ["relevance", "evidence quality"],
        evidence_requirements=evidence or ["candidate exists", "claim-level evidence"],
    )


# ---------------------------------------------------------------- budget --
# "under 3k" / "under 60k" / "under ₹3,000" / "budget of $500" never used to
# become an enforced numeric ceiling anywhere in the pipeline — brief.budget
# was a free-text string only ever shown to the LLM as a soft hint, so
# over-budget or currency-mismatched candidates could survive ranking. This
# parses a hard ceiling straight from the user's own goal text, independent
# of whether the brief-extraction LLM call even succeeds.
_K_SUFFIX = re.compile(r"(\d+(?:\.\d+)?)\s*k\b", re.IGNORECASE)
_PLAIN_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)")
_UNDER_WORDS = re.compile(r"\b(?:under|below|less than|up to|within|budget of|max|upto)\b", re.IGNORECASE)


@dataclass
class BudgetCeiling:
    amount: float
    currency: str


def parse_budget_ceiling(goal: str, geography: str | None = None) -> BudgetCeiling | None:
    text = goal or ""
    if not _UNDER_WORDS.search(text) and "k" not in text.lower():
        return None

    currency = settings.DEFAULT_CURRENCY
    if "₹" in text or re.search(r"\b(?:inr|rs\.?|rupees)\b", text, re.IGNORECASE):
        currency = "INR"
    elif "$" in text or re.search(r"\busd\b", text, re.IGNORECASE):
        currency = "USD"
    elif "€" in text or re.search(r"\beur\b", text, re.IGNORECASE):
        currency = "EUR"
    elif "£" in text or re.search(r"\bgbp\b", text, re.IGNORECASE):
        currency = "GBP"
    elif geography and re.search(r"\bindia\b", geography, re.IGNORECASE):
        currency = "INR"

    # Prefer a number near an "under/below/..." cue if one exists; otherwise
    # take the first number+k in the whole string ("headphones under 3k").
    window = text
    cue = _UNDER_WORDS.search(text)
    if cue:
        window = text[cue.end():]

    k_match = _K_SUFFIX.search(window) or _K_SUFFIX.search(text)
    if k_match:
        try:
            return BudgetCeiling(amount=float(k_match.group(1)) * 1000, currency=currency)
        except ValueError:
            return None

    if cue:
        plain = _PLAIN_NUM.search(window)
        if plain:
            try:
                return BudgetCeiling(amount=float(plain.group(1).replace(",", "")), currency=currency)
            except ValueError:
                return None
    return None


def filter_candidates_by_budget(
    candidates: list[dict[str, Any]], ceiling: BudgetCeiling | None, tolerance: float = 1.10
) -> list[dict[str, Any]]:
    """Hard-drop candidates that verifiably blow the stated budget.

    Only filters when we actually have a verified price in a matching
    currency — an unverified/unknown price is a research gap, not proof the
    item is over budget, so it's left in for the ranking model to weigh
    against the evidence gap instead of being silently discarded here.
    """
    if not ceiling or not candidates:
        return candidates
    kept = []
    for c in candidates:
        price = c.get("price")
        currency = str(c.get("currency") or "").upper()
        if c.get("price_verified") and price and currency == ceiling.currency:
            if float(price) > ceiling.amount * tolerance:
                continue
        kept.append(c)
    return kept


async def build_query_matrix(
    goal: str, brief: ResearchBrief, router: Any, exclude: list[str] | None = None
) -> list[str]:
    prompt = f"Goal: {goal}\nBrief: {json.dumps(brief.to_dict(), ensure_ascii=False)}"
    ceiling = parse_budget_ceiling(goal, brief.geography)
    if ceiling:
        prompt += f"\nHard budget ceiling: {ceiling.amount:.0f} {ceiling.currency} (do not suggest options clearly above this)"
    if exclude:
        prompt += f"\nAlready tried, do not repeat: {exclude}"
    try:
        raw = await router.fast(prompt, QUERY_SYSTEM)
        queries = [str(q).strip() for q in (raw.get("queries") or []) if str(q).strip()]
    except Exception as exc:
        print(f"[research] build_query_matrix: all providers failed: {exc}")
        queries = []
    queries = [q for q in queries if q not in (exclude or [])]
    if not queries:
        # Deterministic fallback so a planner failure never stalls the loop.
        base = [goal, f"best {goal}", f"{goal} price"]
        queries = [q for q in base if q not in (exclude or [])] or base
    return queries[:6]


async def extract_candidates(
    goal: str, brief: ResearchBrief, sources: list[dict[str, Any]], router: Any
) -> list[dict[str, Any]]:
    """One structured call turns raw fetched content into strict candidates."""
    if not sources:
        return []
    # Kept in sync with agent.py's rank_sources(...)[:14] slice — this used
    # to silently re-cap to 8, discarding half the widened source list before
    # it ever reached the model.
    context = "\n\n".join(
        f"SOURCE: {s.get('url')}\nTITLE: {s.get('title')}\n"
        f"CONTENT: {(s.get('content') or s.get('snippet') or '')[:1200]}"
        for s in sources[:14]
    )
    ceiling = parse_budget_ceiling(goal, brief.geography)
    ceiling_line = (
        f"\nHard budget ceiling: {ceiling.amount:.0f} {ceiling.currency}. Extract candidates "
        "regardless of price so evidence isn't lost, but read price/currency precisely from the "
        "source (India-shorthand like '3k' or '60k' in the goal means thousands of INR, not the "
        "source's literal currency unless the source states one).\n"
        if ceiling else "\n"
    )
    prompt = f"Goal: {goal}\nBrief: {json.dumps(brief.to_dict(), ensure_ascii=False)}{ceiling_line}\n{context}"
    try:
        raw = await router.fast(prompt, CANDIDATE_SYSTEM)
        raw_candidates = raw.get("candidates") or []
    except Exception as exc:
        print(f"[research] extract_candidates: all providers failed on {len(sources)} sources: {exc}")
        raw_candidates = []
    return raw_candidates if isinstance(raw_candidates, list) else []


# ------------------------------------------------------- deep-dive query --
# Once a candidate is shortlisted (real name, decent evidence), Scout should
# stop relying on whatever the original broad search happened to surface and
# go looking specifically for THIS candidate's own retailer/booking page —
# that's what actually gets a verified price + "Buy Now" lit up, instead of
# leaving purchase_url empty because the round-1 source was a review site.
_BUY_VERB = {
    "flight": "book flight",
    "hotel": "book hotel rooms",
    "ticket": "buy tickets",
    "restaurant": "reserve table",
    "service": "book appointment",
    "product": "buy price",
    "general_research": "buy price",
}


def build_candidate_deepdive_query(name: str, entity_type: str) -> str:
    verb = _BUY_VERB.get(entity_type, "buy price")
    return f"{name} {verb}"


# ------------------------------------------------------- Anakin fallback --
# Anakin's Agentic Search is a research agent, not a chat-completion API —
# it takes one "prompt" (no separate system/schema) and does its own live
# web research server-side, returning generatedJson. That makes it a real
# extraction fallback (still LLM-quality, still grounded in fresh sources)
# when Groq/OpenRouter/Google are all down, rather than jumping straight to
# the zero-LLM regex fallback in entities.py.
def build_anakin_extraction_prompt(goal: str, brief: ResearchBrief) -> str:
    return (
        f"{CANDIDATE_SYSTEM}\n\n"
        f"Goal: {goal}\nBrief: {json.dumps(brief.to_dict(), ensure_ascii=False)}\n\n"
        "Search the live web yourself right now to find real, named candidates "
        "for this goal, then return ONLY the JSON object described above — "
        "no other text."
    )


VERIFY_SYSTEM = (
    "You verify purchase details for specific named candidates using the FULL page content given below "
    "(not a snippet — read the whole page). For each candidate listed, check whether ITS OWN page (matched "
    "by candidate_index) states a current, exact numeric price and confirms it is currently buyable/bookable "
    "(in stock, seats available, rooms available, tickets on sale). Never guess or reuse a price from a "
    "different candidate or a different page. If the page is a review/article/comparison rather than a "
    "retailer, airline, hotel, or ticketing page, price_verified and availability_verified must both be false "
    "even if a price number appears in the text (it may be stale or for a different variant). "
    'Respond with ONLY this JSON shape: {"verified": [{"candidate_index": integer, '
    '"price": number or null, "currency": "USD|INR|EUR|GBP" or null, '
    '"price_verified": boolean, "purchase_url": str or null, "availability_verified": boolean}]}'
)


async def verify_purchase_info(
    candidates: list[dict[str, Any]], pages: list[str], router: Any
) -> dict[int, dict[str, Any]]:
    """Second-pass verification using FULL fetched pages (not the 1.2k-char
    extraction snippet). This is what actually lets 'Buy Now' light up: round-1
    extraction only sees a short clip of each source and correctly refuses to
    claim a verified price from it; this pass gives the model the whole page
    for exactly the candidates that need checking.
    """
    if not candidates or not pages:
        return {}
    blocks = []
    for i, (c, page) in enumerate(zip(candidates, pages)):
        if not page:
            continue
        blocks.append(
            f"CANDIDATE_INDEX: {i}\nNAME: {c.get('name')}\nPAGE_URL: {c.get('source_url')}\n"
            f"FULL_PAGE_CONTENT:\n{page[:6000]}"
        )
    if not blocks:
        return {}
    prompt = "\n\n---\n\n".join(blocks)
    try:
        raw = await router.fast(prompt, VERIFY_SYSTEM)
        rows = raw.get("verified") or []
    except Exception as exc:
        print(f"[research] verify_purchase_info: all providers failed: {exc}")
        rows = []
    out: dict[int, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("candidate_index"))
        except (TypeError, ValueError):
            continue
        out[idx] = row
    return out


# ---------------------------------------------------------------- sources --
def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def source_quality(source: dict[str, Any]) -> float:
    url = str(source.get("url", ""))
    title = str(source.get("title", ""))
    content = str(source.get("content", "") or source.get("snippet", ""))
    host = _host(url)
    text = f"{title} {content}".lower()

    score = 0.35
    if host in LOW_VALUE_HOSTS:
        score -= 0.18
    if any(h in host for h in HIGH_QUALITY_HINTS):
        score += 0.20
    if any(h in host for h in RETAIL_HINTS):
        score += 0.12
    score += 0.08 if len(content) >= 500 else (0.04 if len(content) >= 180 else 0)

    useful_terms = (
        "price", "specification", "review", "battery", "rating", "features",
        "availability", "dimensions", "processor", "display", "warranty",
        "amenities", "menu", "duration", "salary", "curriculum",
    )
    hits = sum(1 for term in useful_terms if term in text)
    score += min(hits * 0.025, 0.15)
    if source.get("source") == "anakin":
        score += 0.04
    return round(max(0.0, min(score, 1.0)), 3)


def normalize_source(source: dict[str, Any]) -> dict[str, Any]:
    item = dict(source)
    item["title"] = " ".join(str(item.get("title", "")).split())[:300]
    item["url"] = " ".join(str(item.get("url", "")).split())[:2000]
    item["snippet"] = " ".join(str(item.get("snippet", "")).split())[:1000]
    item["content"] = str(item.get("content", "") or "")[:7000]
    item["retrieval_ok"] = bool(item.get("content") or item.get("snippet"))
    item["quality_score"] = source_quality(item)
    return item


def deduplicate_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    output: list[dict[str, Any]] = []
    for raw in sources:
        item = normalize_source(raw)
        url = item["url"].rstrip("/").lower()
        title = item["title"].lower()
        if url and url in seen_urls:
            continue
        if not url and title and title in seen_titles:
            continue
        seen_urls.add(url) if url else None
        seen_titles.add(title) if title else None
        output.append(item)
    return output


def rank_sources(sources: list[dict[str, Any]], minimum_quality: float = 0.0) -> list[dict[str, Any]]:
    ranked = [normalize_source(s) for s in sources]
    ranked = [s for s in ranked if s["quality_score"] >= minimum_quality]
    ranked.sort(key=lambda s: s.get("quality_score", 0), reverse=True)
    return ranked


def calculate_evidence_coverage(
    candidates: list[dict[str, Any]], brief: ResearchBrief
) -> dict[str, Any]:
    """How well the current candidate set covers the brief's evidence requirements."""
    if not candidates:
        return {"coverage": 0.0, "candidates_with_evidence": 0, "missing": list(brief.evidence_requirements)}
    with_evidence = sum(1 for c in candidates if c.get("evidence"))
    coverage = round(with_evidence / max(1, len(candidates)), 2)
    return {
        "coverage": coverage,
        "candidates_with_evidence": with_evidence,
        "candidate_count": len(candidates),
        "missing": [] if coverage >= 0.6 else list(brief.evidence_requirements),
    }

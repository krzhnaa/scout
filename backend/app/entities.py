"""Entity normalization, evidence validation, and action eligibility.

This module deliberately contains no product/brand/merchant catalogue.  It only
normalizes facts that Scout's research providers actually return.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

ARTICLE_PATTERNS = (
    r"^(?:the\s+)?best\b", r"^\d+\s+best\b", r"\bbest\b[^|]{0,40}\bof\s+20\d{2}\b",
    r"^top\s*\d*\b", r"\btop\s+\d+\b",
    r"\bcomparison\b", r"\bcompar(ed|ison)\b",
    r"\breview(s)?\b", r"\bbuying guide\b", r"\bhow to\b", r"\blisticle\b",
    r"\bguide\b", r"\bvs\.?\b",
    # Was `\bunder\s+[₹$€£]\s*\d`, which required a currency symbol right
    # after "under" — "gaming monitors under 300" (no symbol) slipped
    # through and got treated as a real product name. Budget-style listicle
    # titles almost never appear in an actual product/flight/hotel name, so
    # the currency symbol is now optional.
    r"\bunder\s+[₹$€£]?\s*\d",
    r"\bfor\s+20\d{2}\b",  # "... for 2026" style listicles
)
GENERIC_CATEGORY_WORDS = {
    "monitor", "monitors", "laptop", "laptops", "phone", "phones", "headphone",
    "headphones", "earbud", "earbuds", "camera", "cameras", "tv", "tvs",
    "speaker", "speakers", "keyboard", "keyboards", "mouse", "mice", "watch",
    "watches", "tablet", "tablets", "flight", "flights", "hotel", "hotels",
    "restaurant", "restaurants", "ticket", "tickets", "review", "reviews",
    "guide", "guides", "product", "products", "deal", "deals",
}
EDITORIAL_HOSTS = {
    "rtings.com", "soundguys.com", "whathifi.com", "theverge.com", "techradar.com",
    "tomsguide.com", "gadgets360.com", "91mobiles.com", "digit.in", "notebookcheck.net",
    "consumerreports.org", "pcmag.com", "cnet.com", "wired.com",
}


def _text(value: Any, limit: int = 2000) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def valid_url(value: Any) -> bool:
    try:
        parsed = urlparse(_text(value, 4000))
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def host(value: Any) -> str:
    if not valid_url(value):
        return ""
    return urlparse(str(value)).netloc.lower().split(":", 1)[0].removeprefix("www.")


def entity_type_for(goal: str) -> str:
    """Classify the transaction/research object from user intent, not a catalogue."""
    text = _text(goal, 10000).lower()
    if re.search(r"\b(flight|flights|airline|fare|airport|non[- ]?stop|direct flight)\b", text):
        return "flight"
    if re.search(r"\b(ticket|tickets|concert|event|show|match|festival)\b", text):
        return "ticket"
    if re.search(r"\b(hotel|hotels|resort|stay|room|accommodation|hostel)\b", text):
        return "hotel"
    if re.search(r"\b(restaurant|restaurants|cafe|cafes|dining|dinner|lunch)\b", text):
        return "restaurant"
    if re.search(r"\b(service|appointment|repair|cleaning|consultation)\b", text):
        return "service"
    return "product"


def is_entity_name(name: str, entity_type: str) -> bool:
    value = _text(name, 200)
    if len(value) < 3 or len(value) > 180:
        return False
    low = value.lower()
    if any(re.search(p, low) for p in ARTICLE_PATTERNS):
        return False
    if re.search(r"\b(read more|latest news|news update|homepage|official website)\b", low):
        return False
    # An airline/company alone is not an individual flight.
    if entity_type == "flight":
        return bool(re.search(r"\b[A-Z]{2}\s?[- ]?\d{1,4}\b", value, re.I))
    # A single bare category word ("monitor", "laptop") is not a specific,
    # purchasable candidate — it's what's left over after a URL-slug guess
    # ("/reviews/monitor" -> "monitor") strips away everything distinctive.
    # Real product/hotel/restaurant names either have more than one word or
    # include a model number/digit.
    words = low.split()
    if len(words) == 1 and words[0] in GENERIC_CATEGORY_WORDS and not re.search(r"\d", value):
        return False
    return True



def _is_bare_publisher_name(name: str, url: Any) -> bool:
    """Reject a candidate name that is really just the source's own site name.

    The zero-LLM fallback (deterministic_candidates_from_sources) used to
    happily turn a source titled "Tom's Hardware" into a "candidate" called
    "Tom's Hardware" — a publisher, not a product/flight/hotel/anything
    purchasable. Compare the normalized name against the URL's own host
    label (e.g. tomshardware.com -> "tomshardware") and reject a match.
    """
    h = host(url)
    if not h:
        return False
    label = h.split(".")[0]
    norm_name = re.sub(r"[^a-z0-9]", "", name.lower())
    norm_label = re.sub(r"[^a-z0-9]", "", label.lower())
    if not norm_name or not norm_label or len(norm_label) < 4:
        return False
    if norm_name == norm_label:
        return True
    # "tomshardware" appearing inside "tomshardware" plus at most a couple
    # of stray characters ("tomshardwareteam") still counts as bare-site.
    return norm_label in norm_name and len(norm_name) <= len(norm_label) + 4


def candidate_name_from_source(title: Any, url: Any, entity_type: str) -> str:
    """Derive a likely entity name from a live source without a product catalogue.

    This is intentionally generic: it removes common editorial wrappers and URL
    noise, then lets is_entity_name decide whether the result is specific enough.
    """
    raw_title = _text(title, 300)
    raw_url = _text(url, 4000)
    candidates: list[str] = []

    if raw_title:
        candidates.append(raw_title)
        # Common search-result wrappers: "Site: Entity", "Entity | Review", etc.
        for sep in (" | ", " — ", " – ", " : ", ": "):
            if sep in raw_title:
                parts = [x.strip() for x in raw_title.split(sep) if x.strip()]
                candidates.extend(parts)
        cleaned = re.sub(r"^\s*(?:best|top\s*\d*|buying guide|review|comparison)\s*[:|-]\s*", "", raw_title, flags=re.I)
        cleaned = re.sub(r"\s*(?:[-|:]\s*)?(?:review|price|specifications?|features?|pros and cons|buying guide|comparison)\s*$", "", cleaned, flags=re.I).strip()
        candidates.append(cleaned)

    # Product/property pages frequently encode the entity in their final URL slug.
    if raw_url and valid_url(raw_url):
        path = urlparse(raw_url).path.strip("/")
        if path:
            slug = path.split("/")[-1]
            slug = re.sub(r"[-_]+", " ", slug)
            slug = re.sub(r"\b(?:dp|product|products|item|p)\b", " ", slug, flags=re.I)
            candidates.append(slug)

    for value in candidates:
        value = _text(value, 180)
        if not value:
            continue
        # Strip obvious publisher prefixes/suffixes without naming any publisher.
        value = re.sub(r"^[^:]{1,40}:\s*", "", value) if value.count(":") == 1 else value
        value = re.sub(r"\s*(?:[-|:]\s*)?(?:review|price|specifications?|features?|pros and cons|buying guide|comparison)\s*$", "", value, flags=re.I).strip()
        if _is_bare_publisher_name(value, raw_url):
            continue
        if is_entity_name(value, entity_type):
            return value
    return ""

def entity_id(name: str, entity_type: str, variant: str = "") -> str:
    raw = f"{name} {variant}".strip().lower()
    slug = "-".join(re.findall(r"[a-z0-9]+", raw))[:120]
    return f"{entity_type}:{slug}"


def _parse_price(value: Any) -> tuple[float | None, str]:
    if value is None:
        return None, ""
    if isinstance(value, (int, float)):
        return (float(value) if float(value) > 0 else None), ""
    text = _text(value, 100)
    currency = ""
    if "₹" in text or re.search(r"\b(?:INR|Rs\.?|rupees)\b", text, re.I): currency = "INR"
    elif "$" in text or re.search(r"\bUSD\b", text, re.I): currency = "USD"
    elif "€" in text or re.search(r"\bEUR\b", text, re.I): currency = "EUR"
    elif "£" in text or re.search(r"\bGBP\b", text, re.I): currency = "GBP"
    nums = re.findall(r"\d[\d,]*(?:\.\d+)?", text)
    if not nums:
        return None, currency
    try:
        return float(nums[0].replace(",", "")), currency
    except ValueError:
        return None, currency


def _as_list(value: Any) -> list[Any]:
    if value is None: return []
    return value if isinstance(value, list) else [value]


def _evidence_rows(raw: dict[str, Any], fallback_url: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_evidence = raw.get("evidence") or raw.get("sources") or raw.get("citations") or []
    for item in _as_list(raw_evidence):
        if isinstance(item, str):
            if fallback_url: rows.append({"criterion": "source", "claim": item[:500], "source_url": fallback_url, "confidence": 0.35})
            continue
        if not isinstance(item, dict): continue
        url = _text(item.get("source_url") or item.get("url") or item.get("link") or fallback_url, 2000)
        claim = _text(item.get("claim") or item.get("text") or item.get("evidence") or item.get("snippet"), 900)
        criterion = _text(item.get("criterion") or item.get("field") or item.get("type") or "evidence", 120)
        try: confidence = max(0.0, min(1.0, float(item.get("confidence", 0.5))))
        except (TypeError, ValueError): confidence = 0.5
        if claim and valid_url(url): rows.append({"criterion": criterion, "claim": claim, "source_url": url, "confidence": confidence})
    if fallback_url and not rows:
        text = _text(raw.get("content") or raw.get("snippet") or raw.get("reason"), 900)
        if text: rows.append({"criterion": "identity", "claim": text, "source_url": fallback_url, "confidence": 0.35})
    return rows[:20]


def normalize_candidate(raw: dict[str, Any], entity_type: str) -> dict[str, Any] | None:
    """Turn heterogeneous provider output into one safe candidate contract."""
    if not isinstance(raw, dict): return None
    name = _text(raw.get("name") or raw.get("title") or raw.get("product_name") or raw.get("property_name") or raw.get("event_name"), 180)
    if not is_entity_name(name, entity_type): return None
    source_url = _text(raw.get("source_url") or raw.get("url") or raw.get("link"), 2000)
    if not valid_url(source_url): return None
    source_urls = []
    for u in _as_list(raw.get("source_urls")) + [source_url]:
        u = _text(u, 2000)
        if valid_url(u) and u not in source_urls: source_urls.append(u)
    brand = _text(raw.get("brand") or raw.get("manufacturer"), 120)
    model = _text(raw.get("model") or raw.get("model_name"), 160)
    variant = _text(raw.get("variant") or raw.get("configuration") or raw.get("room_type") or raw.get("fare_type"), 180)
    price, detected_currency = _parse_price(raw.get("price") or raw.get("current_price") or raw.get("amount"))
    currency = _text(raw.get("currency") or detected_currency, 12).upper()
    # If no separate price_source_url was given, the candidate's own (already
    # validated) source_url backs the price. But if one WAS given, it must
    # actually be a real URL — never trust price_verified=True on its own,
    # since a bad explicit price_source_url signals a hallucinated price.
    claimed_price_verified = bool(raw.get("price_verified")) and price is not None and bool(currency)
    raw_price_source_url = _text(raw.get("price_source_url") or "", 2000)
    price_source_url = raw_price_source_url or (source_url if claimed_price_verified else "")
    price_verified = claimed_price_verified and valid_url(price_source_url)
    purchase_url = _text(raw.get("purchase_url") or raw.get("buy_url") or raw.get("booking_url") or raw.get("checkout_url"), 2000)
    booking_url = _text(raw.get("booking_url") or purchase_url, 2000)
    seller = _text(raw.get("seller") or raw.get("merchant") or raw.get("retailer"), 160)
    provider = _text(raw.get("provider") or raw.get("airline") or raw.get("hotel") or raw.get("venue"), 160)
    availability = _text(raw.get("availability") or raw.get("inventory") or "unknown", 120).lower()
    availability_verified = bool(raw.get("availability_verified"))
    if availability in {"available", "in stock", "instock", "yes", "true", "open"} and raw.get("availability_verified") is None:
        availability_verified = False
    facts = raw.get("facts") if isinstance(raw.get("facts"), dict) else {}
    evidence = _evidence_rows(raw, source_url)
    if not evidence: evidence = [{"criterion": "identity", "claim": name, "source_url": source_url, "confidence": 0.3}]
    pros = [ _text(x, 300) for x in _as_list(raw.get("pros")) if _text(x, 300) ][:8]
    cons = [ _text(x, 300) for x in _as_list(raw.get("cons") or raw.get("weaknesses")) if _text(x, 300) ][:8]
    criteria_scores = raw.get("criteria_scores") if isinstance(raw.get("criteria_scores"), dict) else {}
    review_summary = _text(raw.get("review_summary") or raw.get("reviews") or raw.get("review_synthesis"), 1500)
    purchase_host = host(purchase_url)
    if purchase_host in EDITORIAL_HOSTS:
        purchase_url = ""
        booking_url = ""
    if purchase_url and not valid_url(purchase_url): purchase_url = ""
    if booking_url and not valid_url(booking_url): booking_url = ""
    if not purchase_url: booking_url = booking_url if valid_url(booking_url) and entity_type in {"flight","hotel","ticket","restaurant","service"} else ""
    purchasable = bool(price_verified and (purchase_url or booking_url))
    evidence_confidence = round(sum(float(e.get("confidence", 0)) for e in evidence) / max(1, len(evidence)), 3)
    return {
        "entity_id": entity_id(name, entity_type, variant), "entity_type": entity_type, "name": name,
        "brand": brand, "model": model, "variant": variant, "source_url": source_url, "source_urls": source_urls,
        "seller": seller, "provider": provider,
        "price": round(price, 2) if (price is not None and price_verified) else None,
        "currency": currency, "price_verified": price_verified, "price_source_url": price_source_url if price_verified else "",
        "availability": availability, "availability_verified": availability_verified,
        "purchase_url": purchase_url, "booking_url": booking_url, "purchasable": purchasable,
        "purchase_type": _text(raw.get("purchase_type") or ("booking" if entity_type in {"flight","hotel","ticket","restaurant","service"} else "purchase"), 40),
        "transaction_details": raw.get("transaction_details") if isinstance(raw.get("transaction_details"), list) else [],
        "facts": facts, "pros": pros, "cons": cons, "review_summary": review_summary,
        "reason": _text(raw.get("reason"), 300),
        "criteria_scores": criteria_scores, "evidence": evidence, "evidence_confidence": evidence_confidence,
        "evidence_count": len(evidence), "action_eligibility": {},
    }


def deterministic_candidates_from_sources(
    sources: list[dict[str, Any]], entity_type: str, limit: int = 12
) -> list[dict[str, Any]]:
    """Build real candidates straight from search results, with zero LLM calls.

    This exists so a single flaky/rate-limited/misconfigured provider can
    never reduce Scout's output to nothing. It intentionally never invents a
    price or purchase link — only what a source's own title/URL states — so
    every candidate it produces is either honestly unverified (a research
    gap the ranking stage can see and score down) or backed by whatever
    snippet text is actually present. This is the fallback that guarantees
    "select candidates -> compare -> pick the best one" always has something
    real to work with, for products, flights, hotels, or anything else.
    """
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for source in sources:
        if len(out) >= limit:
            break
        url = str(source.get("url") or "")
        title = str(source.get("title") or "")
        if not valid_url(url):
            continue
        name = candidate_name_from_source(title, url, entity_type)
        if not name:
            continue
        eid = entity_id(name, entity_type)
        if eid in seen_ids:
            continue
        seen_ids.add(eid)
        snippet = _text(source.get("content") or source.get("snippet"), 900)
        price, currency = _parse_price(snippet)
        out.append({
            "entity_id": eid, "entity_type": entity_type, "name": name,
            "brand": "", "model": "", "variant": "",
            "source_url": url, "source_urls": [url],
            "seller": host(url), "provider": "",
            # Never mark price_verified true here — a number that merely
            # appears in a snippet is not a confirmed current price.
            "price": None, "currency": currency, "price_verified": False, "price_source_url": "",
            "availability": "unknown", "availability_verified": False,
            "purchase_url": "", "booking_url": "", "purchasable": False,
            "purchase_type": "booking" if entity_type in {"flight", "hotel", "ticket", "restaurant", "service"} else "purchase",
            "transaction_details": [],
            "facts": {}, "pros": [], "cons": [],
            "review_summary": "",
            "reason": "Surfaced directly from search results (fallback extraction).",
            "criteria_scores": {},
            "evidence": [{
                "criterion": "identity", "claim": (snippet or title or name)[:900],
                "source_url": url, "confidence": 0.3,
            }],
            "evidence_confidence": 0.3, "evidence_count": 1, "action_eligibility": {},
        })
    return out


def merge_candidates(existing: list[dict[str, Any]], incoming: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    by_id = {str(x.get("entity_id")): dict(x) for x in existing if isinstance(x, dict) and x.get("entity_id")}
    for item in incoming:
        if not isinstance(item, dict): continue
        cid = str(item.get("entity_id") or "")
        if not cid: continue
        if cid not in by_id: by_id[cid] = item; continue
        cur = by_id[cid]
        cur["source_urls"] = list(dict.fromkeys((cur.get("source_urls") or []) + (item.get("source_urls") or [])))[:20]
        cur["evidence"] = (cur.get("evidence") or []) + (item.get("evidence") or [])
        cur["evidence"] = cur["evidence"][:30]
        for key in ("price","currency","price_verified","price_source_url","purchase_url","booking_url","seller","provider","review_summary","reason"):
            if not cur.get(key) and item.get(key): cur[key] = item[key]
        cur["evidence_count"] = len(cur["evidence"])
        cur["evidence_confidence"] = round(sum(float(e.get("confidence",0)) for e in cur["evidence"]) / max(1,len(cur["evidence"])),3)
    return list(by_id.values())[:limit]


def get_action_eligibility(candidate: dict[str, Any]) -> dict[str, Any]:
    entity_type = str(candidate.get("entity_type") or "product")
    url = candidate.get("purchase_url") or candidate.get("booking_url")
    if not valid_url(url): return {"available": False, "reason": "No legitimate transaction URL."}
    if not bool(candidate.get("price_verified")) or not candidate.get("price") or float(candidate.get("price") or 0) <= 0:
        return {"available": False, "reason": "Current price is not verified."}
    if entity_type in {"flight","hotel","ticket","restaurant","service"} and not bool(candidate.get("availability_verified")):
        return {"available": False, "reason": "Current availability is not verified."}
    labels = {"product":"Buy Now","flight":"Book Now","hotel":"Book Now","ticket":"Get Tickets","restaurant":"Reserve","service":"Book Now"}
    return {"available": True, "action_type": "booking" if entity_type != "product" else "purchase", "label": labels.get(entity_type,"Open"), "url": url, "mode":"external", "price_verified":True, "availability_verified":bool(candidate.get("availability_verified",False)), "reason":"Transaction evidence is verified."}

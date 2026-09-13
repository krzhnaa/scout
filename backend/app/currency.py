"""Convert a researched price into the Razorpay account's settlement
currency (INR on a standard India test account) so checkout never has to
reject an order just because the product was researched in USD/EUR/etc.

Razorpay India accounts can only settle in INR unless International
Payments has been separately enabled by Razorpay for that business — that
is an account-level approval, not something this codebase can turn on.
Rather than blocking checkout for every non-INR option, Scout now always
creates the Razorpay order in the account's settlement currency and shows
the researched price/currency alongside it for transparency. This is
consistent with the rest of the flow already being a labelled simulation
("This simulates the researched transaction... is not charged").
"""

from __future__ import annotations

import httpx

from .cache import TTLCache

# Static fallback table (approximate, updated periodically). Used only if
# the live lookup below fails, so a network hiccup never blocks checkout.
# Values are "1 unit of currency = N INR".
_FALLBACK_TO_INR = {
    "INR": 1.0,
    "USD": 87.0,
    "EUR": 94.0,
    "GBP": 110.0,
    "AED": 23.7,
    "SGD": 65.0,
    "AUD": 57.0,
    "CAD": 63.0,
    "JPY": 0.58,
}

_fx_cache = TTLCache(ttl_seconds=6 * 3600)


async def _live_rate_to_inr(currency: str, client: httpx.AsyncClient) -> float | None:
    cache_key = f"fx:{currency}:INR"
    cached = _fx_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        # frankfurter.dev is a free, keyless FX-rate API (ECB reference
        # rates). No account or API key required.
        response = await client.get(
            "https://api.frankfurter.dev/v1/latest",
            params={"base": currency, "symbols": "INR"},
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()
        rate = float(data["rates"]["INR"])
    except Exception:
        return None
    if rate <= 0:
        return None
    _fx_cache.set(cache_key, rate)
    return rate


async def rate_to_inr(currency: str) -> float:
    """Best-effort exchange rate for 1 unit of `currency` in INR."""
    currency = currency.upper()
    if currency == "INR":
        return 1.0
    async with httpx.AsyncClient() as client:
        live = await _live_rate_to_inr(currency, client)
    if live is not None:
        return live
    return _FALLBACK_TO_INR.get(currency, 1.0)


async def convert_to_settlement_currency(
    price: float,
    currency: str,
    settlement_currency: str,
) -> tuple[float, float | None]:
    """Convert `price` (in `currency`) into `settlement_currency`.

    Returns (converted_price, exchange_rate_used). exchange_rate_used is
    None when no conversion was needed (same currency).
    """
    currency = currency.upper()
    settlement_currency = settlement_currency.upper()
    if currency == settlement_currency:
        return price, None
    if settlement_currency != "INR":
        # Only INR conversion is implemented; if the settlement currency
        # is ever changed to something else, fail loudly rather than
        # silently mis-charging.
        raise ValueError(
            f"No conversion path configured for settlement currency {settlement_currency!r}."
        )
    rate = await rate_to_inr(currency)
    return round(price * rate, 2), rate
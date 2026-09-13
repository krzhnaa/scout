"""Razorpay test-mode helpers. Amounts always come from verified session data."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import math
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import settings

RAZORPAY_API = "https://api.razorpay.com/v1"
SUPPORTED_CURRENCIES = {"INR", "USD", "EUR", "GBP", "AED", "SGD", "AUD", "CAD", "JPY"}
ZERO_DECIMAL_CURRENCIES = {"JPY"}
MAX_AMOUNT_SUBUNITS = 10_000_000_00  # INR 10 lakh: conservative demo ceiling


class PaymentError(ValueError):
    """A safe error intended for an API client."""


def is_absolute_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def option_is_purchasable(option: dict[str, Any]) -> bool:
    """Defence in depth for options produced before/without schema validation."""
    try:
        price = float(option.get("price"))
    except (TypeError, ValueError):
        return False
    return (
        bool(option.get("purchasable"))
        and bool(option.get("price_verified"))
        and math.isfinite(price)
        and price > 0
        and str(option.get("currency", "")).upper() in SUPPORTED_CURRENCIES
        and is_absolute_http_url(option.get("purchase_url"))
        and is_absolute_http_url(option.get("price_source_url"))
    )


def amount_in_subunits(price: Any, currency: str) -> int:
    currency = str(currency).upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise PaymentError("This researched currency is not supported by Scout checkout.")
    try:
        decimal_price = Decimal(str(price))
    except (InvalidOperation, ValueError) as exc:
        raise PaymentError("The researched price is invalid.") from exc
    if not decimal_price.is_finite() or decimal_price <= 0:
        raise PaymentError("The researched price must be positive.")
    multiplier = Decimal("1") if currency in ZERO_DECIMAL_CURRENCIES else Decimal("100")
    amount = int((decimal_price * multiplier).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if amount <= 0 or amount > MAX_AMOUNT_SUBUNITS:
        raise PaymentError("The researched price is outside the supported demo range.")
    return amount


def ensure_currency_enabled(currency: str) -> str:
    """Validate that `currency` is one Scout knows how to price at all.

    This no longer rejects a currency just because the Razorpay account
    can't settle in it — main.py now converts to the settlement currency
    before calling create_order. This only guards against a currency Scout
    doesn't recognize/support pricing for in the first place.
    """
    normalized = str(currency or "").upper()
    if normalized not in SUPPORTED_CURRENCIES:
        raise PaymentError("This researched currency is not supported by Scout checkout.")
    return normalized


def settlement_currency() -> str:
    """The single currency Razorpay will actually create/settle orders in.

    A standard Razorpay India test account can only settle in INR unless
    Razorpay has separately approved International Payments for the
    business — so, unless configured otherwise, everything is charged in
    INR regardless of the currency it was researched in.
    """
    enabled = sorted(settings.RAZORPAY_ENABLED_CURRENCIES)
    return enabled[0] if enabled else "INR"


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    if not all(isinstance(value, str) and value.strip() for value in (order_id, payment_id, signature)):
        return False
    secret = settings.RAZORPAY_KEY_SECRET
    if not secret:
        return False
    payload = f"{order_id}|{payment_id}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def verify_webhook_signature(raw_body: bytes, signature: str | None) -> bool:
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _credentials() -> tuple[str, str]:
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise PaymentError("Razorpay test checkout is not configured on this server.")
    return settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET


async def create_order(*, amount: int, currency: str, session_id: str, option: dict[str, Any]) -> dict[str, Any]:
    currency = ensure_currency_enabled(currency)
    key_id, key_secret = _credentials()
    payload = {
        "amount": amount,
        "currency": currency,
        "receipt": f"scout_{session_id[:12]}_{uuid.uuid4().hex[:12]}",
        "notes": {
            "scout_session_id": session_id,
            "option_name": str(option["name"])[:200],
            "purchase_type": str(option.get("purchase_type", "unknown"))[:40],
        },
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{RAZORPAY_API}/orders", auth=(key_id, key_secret), json=payload)
    except httpx.RequestError as exc:
        raise PaymentError("Could not reach Razorpay. Please try again.") from exc
    if response.status_code >= 400:
        raise PaymentError("Razorpay could not create the test order.")
    try:
        order = response.json()
        if not isinstance(order.get("id"), str) or order.get("amount") != amount or order.get("currency") != currency:
            raise ValueError("unexpected order")
    except (ValueError, TypeError) as exc:
        raise PaymentError("Razorpay returned an invalid order response.") from exc
    return order


async def retrieve_payment_and_order(payment_id: str, order_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    key_id, key_secret = _credentials()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            payment_response, order_response = await asyncio.gather(
                client.get(f"{RAZORPAY_API}/payments/{payment_id}", auth=(key_id, key_secret)),
                client.get(f"{RAZORPAY_API}/orders/{order_id}", auth=(key_id, key_secret)),
            )
    except httpx.RequestError as exc:
        raise PaymentError("Could not verify the Razorpay payment. Please try again.") from exc
    if payment_response.status_code >= 400 or order_response.status_code >= 400:
        raise PaymentError("Razorpay could not verify this payment.")
    try:
        return payment_response.json(), order_response.json()
    except ValueError as exc:
        raise PaymentError("Razorpay returned an invalid verification response.") from exc


def verify_remote_payment(*, payment: dict[str, Any], order: dict[str, Any], expected_order_id: str, expected_amount: int, expected_currency: str, session_id: str, option_name: str) -> None:
    if payment.get("id") is None or payment.get("order_id") != expected_order_id:
        raise PaymentError("Payment does not belong to this order.")
    if payment.get("status") != "captured":
        raise PaymentError("Razorpay has not captured this payment yet.")
    if payment.get("amount") != expected_amount or payment.get("currency") != expected_currency:
        raise PaymentError("Payment amount or currency did not match the researched option.")
    if order.get("id") != expected_order_id or order.get("amount") != expected_amount or order.get("currency") != expected_currency:
        raise PaymentError("Order amount or currency did not match the researched option.")
    notes = order.get("notes") or {}
    if notes.get("scout_session_id") != session_id or notes.get("option_name") != option_name:
        raise PaymentError("Order metadata did not match this Scout session.")
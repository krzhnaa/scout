import asyncio
import hashlib
import hmac
import math
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.agent import AgentSession
import app.agent as agent_module
from app.config import settings
from app.entities import normalize_candidate
from app.payments import PaymentError, amount_in_subunits, ensure_currency_enabled, option_is_purchasable, verify_checkout_signature
from app.main import PAYMENT_IDS, PAYMENT_TRANSACTIONS, SESSIONS, _finalize_payment, _get_purchase_option, app


class PaymentValidationTests(unittest.TestCase):
    def test_inr_amount_is_calculated_server_side(self):
        self.assertEqual(amount_in_subunits("100.00", "INR"), 10000)

    def test_rejects_invalid_prices(self):
        for price in (0, -1, "not-a-price", math.inf):
            with self.assertRaises(ValueError):
                amount_in_subunits(price, "INR")

    def test_non_enabled_international_currency_fails_before_order_creation(self):
        old_enabled = settings.RAZORPAY_ENABLED_CURRENCIES
        settings.RAZORPAY_ENABLED_CURRENCIES = {"INR"}
        try:
            with self.assertRaisesRegex(PaymentError, "not configured for USD"):
                ensure_currency_enabled("USD")
        finally:
            settings.RAZORPAY_ENABLED_CURRENCIES = old_enabled

    def test_non_purchasable_option_is_rejected(self):
        option = {
            "purchasable": False, "price_verified": True, "price": 100,
            "currency": "INR", "purchase_url": "https://shop.example/item",
            "price_source_url": "https://shop.example/item",
        }
        self.assertFalse(option_is_purchasable(option))

    def test_unproven_decision_purchase_fields_are_forced_safe(self):
        # A claimed price_verified=True is never trusted on its own — normalize_candidate
        # re-checks that price_source_url is an actual URL before honoring it.
        candidate = normalize_candidate({
            "name": "Option A", "source_url": "https://example.com/review",
            "purchase_url": "https://example.com/review", "price": 100,
            "currency": "INR", "price_verified": True, "price_source_url": "not a URL",
        }, "product")
        self.assertFalse(candidate["purchasable"])
        self.assertIsNone(candidate["price"])
        self.assertFalse(candidate["price_verified"])

    def test_verified_price_without_purchase_url_remains_visible_but_safe(self):
        candidate = normalize_candidate({
            "name": "Option A", "source_url": "https://example.com/a",
            "purchase_url": "", "price": 100, "currency": "INR",
            "price_verified": True, "price_source_url": "https://store.example/a",
        }, "product")
        self.assertTrue(candidate["price_verified"])
        self.assertEqual(candidate["price"], 100)
        self.assertFalse(candidate["purchasable"])

    def test_test_checkout_uses_verified_source_when_purchase_url_is_missing(self):
        session = AgentSession("test goal")
        session.decision = {"options": [{
            "name": "Option A", "price": 100, "currency": "INR",
            "price_verified": True, "price_source_url": "https://shop.example/item",
            "source_url": "https://shop.example/item", "purchase_url": "", "purchasable": False,
        }]}
        option = _get_purchase_option(session, "Option A")
        self.assertTrue(option["purchasable"])
        self.assertEqual(option["purchase_url"], "https://shop.example/item")

    def test_verified_source_page_becomes_purchase_url_when_model_omits_it(self):
        async def verify_source_page():
            session = AgentSession("test goal")
            session.candidates = [normalize_candidate({
                "name": "Option A", "source_url": "https://shop.example/item",
            }, "product")]
            with patch.object(agent_module, "jina_fetch", AsyncMock(return_value="live product page")), patch.object(
                agent_module, "verify_purchase_info", AsyncMock(return_value={0: {
                    "price": 100, "currency": "INR", "price_verified": True,
                    "availability_verified": True, "purchase_url": None,
                }})
            ):
                await session._verify_top_candidates()
            return session.candidates[0]

        candidate = asyncio.run(verify_source_page())
        self.assertEqual(candidate["purchase_url"], "https://shop.example/item")
        self.assertTrue(candidate["purchasable"])

    def test_decision_is_sorted_and_winner_is_highest_ranked_option(self):
        session = AgentSession("test goal")
        session.candidates = [
            normalize_candidate({"name": f"Option {score}", "source_url": f"https://example.com/{score}"}, "product")
            for score in range(1, 11)
        ]
        merged_scores = {f"Option {score}": [float(score)] for score in range(1, 11)}
        options = session._score_candidates(merged_scores)
        options.sort(key=lambda o: o["score"], reverse=True)
        self.assertEqual(len(options), 10)
        self.assertEqual(options[0]["name"], "Option 10")

    def test_checkout_signature_uses_constant_time_verified_value(self):
        old_secret = settings.RAZORPAY_KEY_SECRET
        settings.RAZORPAY_KEY_SECRET = "test_secret"
        try:
            signature = hmac.new(b"test_secret", b"order_1|pay_1", hashlib.sha256).hexdigest()
            self.assertTrue(verify_checkout_signature("order_1", "pay_1", signature))
            self.assertFalse(verify_checkout_signature("order_1", "pay_1", "bad"))
        finally:
            settings.RAZORPAY_KEY_SECRET = old_secret

    def test_invalid_option_endpoint_does_not_create_an_order(self):
        session = type("Session", (), {"decision": {"options": [{"name": "Read only", "purchasable": False}]}})()
        SESSIONS["test-session"] = session
        try:
            response = TestClient(app).post("/payments/create-order", json={"session_id": "test-session", "option_name": "Read only"})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(PAYMENT_TRANSACTIONS, {})
        finally:
            SESSIONS.pop("test-session", None)

    def test_finalize_is_idempotent_without_email(self):
        import asyncio

        PAYMENT_IDS.clear()
        transaction = {
            "session_id": "session", "option_name": "Option", "purchase_type": "product",
            "purchase_url": "https://shop.example/item", "price": 100, "currency": "INR",
            "amount": 10000, "order_id": "order", "email": "", "verified": False, "email_sent": False,
        }
        first = asyncio.run(_finalize_payment(transaction, "payment"))
        second = asyncio.run(_finalize_payment(transaction, "payment"))
        self.assertTrue(first["success"])
        self.assertEqual(first["payment_id"], second["payment_id"])
        self.assertTrue(transaction["verified"])

    def test_invoice_endpoint_rejects_invalid_email_after_payment(self):
        response = TestClient(app).post("/payments/send-invoice", json={
            "session_id": "session", "razorpay_order_id": "order", "email": "not-an-email",
        })
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()

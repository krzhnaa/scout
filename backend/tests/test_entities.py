import unittest
from app.entities import candidate_name_from_source, get_action_eligibility, normalize_candidate

class EntitySafetyTests(unittest.TestCase):
    def test_generic_source_identity_recovery(self):
        name = candidate_name_from_source(
            "Amazon.in: Sony WH-CH520 Wireless Headphones",
            "https://www.amazon.in/dp/example",
            "product",
        )
        self.assertEqual(name, "Sony WH-CH520 Wireless Headphones")

    def test_review_title_can_normalize_after_clean_identity(self):
        candidate = normalize_candidate({
            "name": "Sony WH-1000XM5",
            "source_url": "https://example.com/review",
            "evidence": [{"criterion": "sound", "claim": "evidence", "source_url": "https://example.com/review", "confidence": 0.8}],
        }, "product")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["name"], "Sony WH-1000XM5")

    def test_editorial_purchase_url_is_rejected(self):
        candidate = normalize_candidate({
            "name": "Example Product",
            "source_url": "https://rtings.com/example",
            "price": 100,
            "currency": "USD",
            "price_verified": True,
            "purchase_url": "https://rtings.com/example",
            "availability_verified": True,
        }, "product")
        self.assertFalse(get_action_eligibility(candidate)["available"])

    def test_verified_transaction_is_actionable(self):
        candidate = normalize_candidate({
            "name": "Example Product",
            "source_url": "https://example.com/review",
            "price": 100,
            "currency": "USD",
            "price_verified": True,
            "purchase_url": "https://shop.example.com/p",
            "availability_verified": True,
        }, "product")
        self.assertTrue(get_action_eligibility(candidate)["available"])

if __name__ == "__main__":
    unittest.main()

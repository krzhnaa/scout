import unittest

from app.providers import _google_response_schema


class GoogleSchemaCompatibilityTests(unittest.TestCase):
    def test_google_conversion_removes_only_incompatible_additional_properties(self):
        original = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"name": {"type": "string", "maxLength": 200}},
                        "required": ["name"],
                    },
                },
            },
            "required": ["options"],
        }
        converted = _google_response_schema(original)
        self.assertNotIn("additionalProperties", str(converted))
        self.assertEqual(converted["properties"]["options"]["items"]["required"], ["name"])
        self.assertEqual(converted["properties"]["options"]["items"]["properties"]["name"]["maxLength"], 200)
        self.assertFalse(original["additionalProperties"])


if __name__ == "__main__":
    unittest.main()

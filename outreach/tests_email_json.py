from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from .tasks import _email_generation_config, _parse_structured_email_response


class EmailResponseTests(SimpleTestCase):
    def test_truncated_response_is_rejected_even_with_parseable_json(self):
        response = SimpleNamespace(
            candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")],
            parsed={"subject": "Hello", "body": "Incomplete"},
        )
        with self.assertRaisesRegex(ValueError, "MAX_TOKENS"):
            _parse_structured_email_response(response)

    def test_complete_json_is_accepted(self):
        response = SimpleNamespace(
            candidates=[SimpleNamespace(finish_reason="STOP")], parsed=None,
            text='{"subject":"Hello","body":"Complete email"}',
        )
        self.assertEqual(_parse_structured_email_response(response),
                         ("Hello", "Complete email"))

    def test_non_object_json_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "JSON object"):
            _parse_structured_email_response(SimpleNamespace(text="[]"))

    @override_settings(GEMINI_MODEL="gemini-3.6-flash")
    def test_email_budget_and_thinking_level(self):
        config = _email_generation_config(use_search=True)
        self.assertEqual(config.max_output_tokens, 4096)
        self.assertEqual(config.thinking_config.thinking_level, "LOW")
        self.assertTrue(config.tools)

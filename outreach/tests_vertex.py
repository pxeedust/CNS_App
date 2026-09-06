from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .tasks import _gemini_configured, _new_gemini_client


class VertexConfigurationTests(SimpleTestCase):
    @override_settings(GOOGLE_GENAI_USE_VERTEXAI=True,
                       GOOGLE_CLOUD_PROJECT="test-project",
                       GOOGLE_CLOUD_LOCATION="global", GEMINI_API_KEY="")
    @patch("outreach.tasks.genai.Client")
    def test_vertex_uses_project_credentials_without_developer_key(self, client):
        self.assertTrue(_gemini_configured())
        _new_gemini_client("unused-developer-key")
        args = client.call_args.kwargs
        self.assertTrue(args["vertexai"])
        self.assertEqual(args["project"], "test-project")
        self.assertNotIn("api_key", args)

    @override_settings(GOOGLE_GENAI_USE_VERTEXAI=True, GOOGLE_CLOUD_PROJECT="")
    @patch("outreach.tasks.genai.Client")
    def test_missing_project_does_not_fall_back_to_developer_api(self, client):
        self.assertFalse(_gemini_configured())
        with self.assertRaisesRegex(ValueError, "GOOGLE_CLOUD_PROJECT"):
            _new_gemini_client("developer-key")
        client.assert_not_called()

    @override_settings(GOOGLE_GENAI_USE_VERTEXAI=False, GEMINI_API_KEY="test-key")
    @patch("outreach.tasks.genai.Client")
    def test_developer_api_remains_explicit(self, client):
        self.assertTrue(_gemini_configured())
        _new_gemini_client("test-key")
        self.assertFalse(client.call_args.kwargs["vertexai"])
        self.assertEqual(client.call_args.kwargs["api_key"], "test-key")

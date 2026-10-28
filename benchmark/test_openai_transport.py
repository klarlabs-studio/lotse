import http.client
import unittest
from unittest.mock import Mock, patch

from openai_transport import OpenAITransport


class OpenAITransportTests(unittest.TestCase):
    def setUp(self):
        self.response = Mock()
        self.response.status = 200
        self.response.getheader.return_value = None
        self.response.read.return_value = b'{"ok":true}'
        self.connection = Mock()
        self.connection.getresponse.return_value = self.response
        self.tls = Mock(name="verified_default_context")
        self.context_patch = patch("openai_transport.ssl.create_default_context",
                                   return_value=self.tls)
        self.https_patch = patch("openai_transport.http.client.HTTPSConnection",
                                 return_value=self.connection)
        self.create_context = self.context_patch.start()
        self.https = self.https_patch.start()
        self.addCleanup(self.context_patch.stop)
        self.addCleanup(self.https_patch.stop)
        self.transport = OpenAITransport("test-secret")

    def test_uses_fixed_verified_https_target_and_only_fixed_headers(self):
        body = b'{"model":"example","input":"hello"}'
        result = self.transport("/v1/responses", body)
        self.assertEqual(result, (200, b'{"ok":true}'))
        self.create_context.assert_called_once_with()
        self.https.assert_called_once_with("api.openai.com", 443, timeout=10,
                                           context=self.tls)
        self.connection.request.assert_called_once_with(
            "POST", "/v1/responses", body=body,
            headers={"Authorization": "Bearer test-secret",
                     "Content-Type": "application/json"})
        self.response.read.assert_called_once_with(1024 * 1024 + 1)
        self.connection.close.assert_called_once_with()

    def test_allows_only_exact_api_paths_and_rejects_before_connecting(self):
        for path in ("/v1/responses/input_tokens", "/v1/responses"):
            with self.subTest(path=path):
                self.connection.reset_mock()
                self.transport(path, b"{}")
        for path in ("https://api.openai.com/v1/responses", "//evil.test/x",
                     "/v1/responses?x=1", "/v1/responses#x",
                     "/v1/responses/", "", [], None):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "invalid_path"):
                    self.transport(path, b"{}")
        self.assertEqual(self.https.call_count, 2)

    def test_request_size_and_type_are_bounded_before_connecting(self):
        for body in ("{}", bytearray(b"{}"), b"x" * (1024 * 1024 + 1)):
            with self.subTest(body_type=type(body).__name__, size=len(body)):
                with self.assertRaisesRegex(ValueError, "invalid_request_body"):
                    self.transport("/v1/responses", body)
        self.https.assert_not_called()

    def test_non_2xx_and_redirect_are_returned_without_retry_or_followup(self):
        self.response.status = 503
        self.response.getheader.return_value = None
        self.assertEqual(self.transport("/v1/responses", b"{}"),
                         (503, b'{"ok":true}'))
        self.assertEqual(self.https.call_count, 1)
        self.connection.request.assert_called_once()

        self.https.reset_mock()
        self.connection.reset_mock()
        self.response.status = 307
        self.response.getheader.side_effect = lambda key: (
            "https://evil.test" if key == "Location" else None)
        self.assertEqual(self.transport("/v1/responses", b"{}"),
                         (307, b'{"ok":true}'))
        self.assertEqual(self.https.call_count, 1)
        self.connection.request.assert_called_once()

    def test_response_size_and_content_encoding_are_rejected(self):
        small = OpenAITransport("key", max_response_bytes=3)
        self.response.read.return_value = b"four"
        with self.assertRaisesRegex(RuntimeError, "response_too_large"):
            small("/v1/responses", b"{}")

        self.response.read.return_value = b"{}"
        self.response.getheader.return_value = "gzip"
        with self.assertRaisesRegex(RuntimeError, "unsupported_content_encoding"):
            self.transport("/v1/responses", b"{}")

    def test_content_length_mismatch_and_network_errors_are_sanitized(self):
        self.response.getheader.side_effect = lambda key: "4" if key == "Content-Length" else None
        with self.assertRaisesRegex(RuntimeError, "response_length_mismatch"):
            self.transport("/v1/responses", b"{}")

        self.connection.getresponse.side_effect = OSError("provider secret test-secret")
        with self.assertRaisesRegex(RuntimeError, "^transport_error$") as caught:
            self.transport("/v1/responses", b"{}")
        self.assertNotIn("test-secret", str(caught.exception))

    def test_credentials_are_validated_and_not_shown_in_repr_or_errors(self):
        for key in ("", "bad\rkey", "bad\nkey", "clé"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "invalid_api_key"):
                    OpenAITransport(key)
        for timeout in (0, -1, 31, float("inf"), True, "10"):
            with self.subTest(timeout=timeout):
                with self.assertRaisesRegex(ValueError, "invalid_timeout"):
                    OpenAITransport("key", timeout=timeout)
        for limit in (0, -1, 1024 * 1024 + 1, True, 1.5):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(ValueError, "invalid_response_limit"):
                    OpenAITransport("key", max_response_bytes=limit)
        self.assertNotIn("test-secret", repr(self.transport))

    def test_http_library_protocol_error_is_sanitized(self):
        self.connection.getresponse.side_effect = http.client.HTTPException(
            "private body test-secret")
        with self.assertRaisesRegex(RuntimeError, "^transport_error$") as caught:
            self.transport("/v1/responses", b"{}")
        self.assertNotIn("test-secret", str(caught.exception))

    def test_library_runtime_error_is_sanitized(self):
        self.connection.getresponse.side_effect = RuntimeError(
            "provider secret test-secret")
        with self.assertRaisesRegex(RuntimeError, "^transport_error$") as caught:
            self.transport("/v1/responses", b"{}")
        self.assertNotIn("test-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

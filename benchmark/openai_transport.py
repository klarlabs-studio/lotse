"""Small, single-attempt HTTPS transport for the OpenAI Responses API."""
from __future__ import annotations

import http.client
import ssl


MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
ALLOWED_PATHS = frozenset(("/v1/responses/input_tokens", "/v1/responses"))


class _TransportFailure(RuntimeError):
    """An intentional, already-sanitized transport rejection."""


class OpenAITransport:
    """Send one bounded POST to a fixed OpenAI API endpoint.

    The API key is supplied explicitly and is never included in the object's
    representation or in transport exceptions.
    """

    def __init__(self, api_key: str, timeout: float = 10,
                 max_response_bytes: int = MAX_RESPONSE_BYTES):
        if (not isinstance(api_key, str) or not api_key or
                not api_key.isascii() or "\r" in api_key or "\n" in api_key):
            raise ValueError("invalid_api_key")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("invalid_timeout")
        if not 0 < timeout <= 30:
            raise ValueError("invalid_timeout")
        if (type(max_response_bytes) is not int or
                not 0 < max_response_bytes <= MAX_RESPONSE_BYTES):
            raise ValueError("invalid_response_limit")
        self._api_key = api_key
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    def __call__(self, path: str, body: bytes) -> tuple[int, bytes]:
        # Validate all caller-controlled input before opening a connection.
        if not isinstance(path, str) or path not in ALLOWED_PATHS:
            raise ValueError("invalid_path")
        if not isinstance(body, bytes) or len(body) > MAX_REQUEST_BYTES:
            raise ValueError("invalid_request_body")

        connection = None
        try:
            context = ssl.create_default_context()
            connection = http.client.HTTPSConnection(
                "api.openai.com", 443, timeout=self.timeout, context=context)
            connection.request(
                "POST", path, body=body,
                headers={"Authorization": "Bearer " + self._api_key,
                         "Content-Type": "application/json"})
            response = connection.getresponse()
            encoding = response.getheader("Content-Encoding")
            if encoding is not None and encoding.strip().lower() != "identity":
                raise _TransportFailure("unsupported_content_encoding")

            response_body = response.read(self.max_response_bytes + 1)
            if len(response_body) > self.max_response_bytes:
                raise _TransportFailure("response_too_large")

            content_length = response.getheader("Content-Length")
            if content_length is not None:
                if not content_length.isascii() or not content_length.isdecimal():
                    raise _TransportFailure("invalid_content_length")
                if int(content_length) != len(response_body):
                    raise _TransportFailure("response_length_mismatch")
            return response.status, response_body
        except _TransportFailure:
            raise
        except Exception:
            # Do not propagate library exceptions: they can contain request
            # details, response data, or provider-controlled text.
            raise RuntimeError("transport_error") from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def close(self) -> None:
        """Provided for callers that use the transport as a managed resource."""
        return None

    def __enter__(self) -> "OpenAITransport":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

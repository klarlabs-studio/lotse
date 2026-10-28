import hashlib
import http.client
import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit
from unittest import mock

import codex_otel
from codex_otel import MAX_BODY_BYTES, Collector, decode_logs


def sample_payload():
    return {
        "resourceLogs": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "codex"}}]},
                "scopeLogs": [
                    {
                        "scope": {"name": "codex.instrumentation"},
                        "logRecords": [
                            {
                                "timeUnixNano": "1720000000000000000",
                                "observedTimeUnixNano": "1720000000000000100",
                                "eventName": "tool.completed",
                                "attributes": [
                                    {"key": "event.name", "value": {"stringValue": "fallback.event"}},
                                    {"key": "count", "value": {"intValue": "3"}},
                                    {"key": "ratio", "value": {"doubleValue": 1.25}},
                                    {"key": "ok", "value": {"boolValue": True}},
                                ],
                                "body": {"stringValue": "untrusted body text"},
                            }
                        ],
                    }
                ],
            }
        ]
    }


class CollectorTests(unittest.TestCase):
    def post(self, endpoint, body, content_type="application/json", extra_headers=None):
        parts = urlsplit(endpoint)
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=3)
        headers = {"Content-Type": content_type}
        if extra_headers:
            headers.update(extra_headers)
        connection.request("POST", parts.path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_roundtrip_flushes_raw_capture_and_receipt_before_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "captures"
            raw = json.dumps(sample_payload(), separators=(",", ":")).encode()
            with Collector(directory) as collector:
                self.assertTrue(collector.endpoint.startswith("http://127.0.0.1:"))
                status, response_body = self.post(collector.endpoint, raw, "application/json; charset=utf-8")
                self.assertEqual((status, response_body), (200, b"{}"))
                capture = directory / "capture-0001.json"
                self.assertEqual(capture.read_bytes(), raw)
                receipt = json.loads((directory / "receipts.jsonl").read_text().splitlines()[0])
                self.assertEqual(receipt["path"], "capture-0001.json")
                self.assertEqual(receipt["sha256"], hashlib.sha256(raw).hexdigest())
                self.assertEqual(receipt["content_type"], "application/json; charset=utf-8")
                self.assertTrue(receipt["received_time"])
                self.assertEqual(os.stat(capture).st_mode & 0o777, 0o600)
                self.assertEqual(os.stat(directory / "receipts.jsonl").st_mode & 0o777, 0o600)
                self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)

    def test_rejects_malformed_oversize_and_wrong_format_with_receipts(self):
        with tempfile.TemporaryDirectory() as tmp, Collector(Path(tmp) / "captures") as collector:
            self.assertEqual(self.post(collector.endpoint, b"not json")[0], 400)
            self.assertEqual(self.post(collector.endpoint, b"{}", "text/plain")[0], 415)
            parts = urlsplit(collector.endpoint)
            connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=3)
            connection.putrequest("POST", parts.path)
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(MAX_BODY_BYTES + 1))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
            connection.close()
            self.assertEqual(self.post(collector.endpoint + "/wrong", b"{}")[0], 404)
            entries = [json.loads(line) for line in (Path(tmp) / "captures" / "receipts.jsonl").read_text().splitlines()]
            self.assertEqual([entry["status"] for entry in entries], [400, 415, 413, 404])
            self.assertFalse(list((Path(tmp) / "captures").glob("capture-*.json")))

    def test_decoder_extracts_event_fields_and_ignores_malformed_values(self):
        payload = sample_payload()
        payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"].append(
            {"key": "bad", "value": {"arrayValue": {"values": []}}}
        )
        records = decode_logs(payload)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["event_name"], "tool.completed")
        self.assertEqual(record["attributes"], {"event.name": "fallback.event", "count": 3, "ratio": 1.25, "ok": True})
        self.assertEqual(record["resource_attributes"], {"service.name": "codex"})
        self.assertEqual(record["scope_name"], "codex.instrumentation")
        self.assertEqual(record["timeUnixNano"], "1720000000000000000")
        self.assertEqual(record["observedTimeUnixNano"], "1720000000000000100")
        self.assertEqual(decode_logs(b"invalid"), [])
        self.assertEqual(decode_logs({"resourceLogs": [None, {"scopeLogs": [None]}]}), [])

    def test_receipt_persistence_failure_returns_error_and_exposes_local_status(self):
        with tempfile.TemporaryDirectory() as tmp, Collector(Path(tmp) / "captures") as collector:
            original_open = os.open

            def fail_receipt(path, flags, mode=0o777):
                if Path(path).name == "receipts.jsonl":
                    raise OSError("synthetic receipt failure")
                return original_open(path, flags, mode)

            raw = json.dumps(sample_payload()).encode()
            with mock.patch.object(codex_otel.os, "open", side_effect=fail_receipt):
                status, _ = self.post(collector.endpoint, raw)
            self.assertEqual(status, 500)
            self.assertEqual(collector.errors, ["receipt_persist_failed"])
            self.assertEqual((Path(tmp) / "captures" / "capture-0001.json").read_bytes(), raw)

    def test_body_string_fallback_only_accepts_bounded_codex_event_names(self):
        payload = sample_payload()
        record = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        record.pop("eventName")
        record["attributes"] = []
        record["body"] = {"stringValue": "tool input may contain private text"}
        self.assertIsNone(decode_logs(payload)[0]["event_name"])
        record["body"] = {"stringValue": "codex.tool.completed"}
        self.assertEqual(decode_logs(payload)[0]["event_name"], "codex.tool.completed")


if __name__ == "__main__":
    unittest.main()

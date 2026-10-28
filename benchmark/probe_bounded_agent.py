"""Offline multi-turn accounting fixtures. No providers or native retrievers run."""
import argparse
import json
from pathlib import Path

from bounded_agent import run as run_loop, wire, sha, _save
from provider_request import ProviderRequestSession
from recorded_transport import RecordedTransport
from probe_recorded_transport import reconcile


TOOLS = [{"name": name, "inputSchema": {"type": "object"}}
         for name in ("retrieve", "read")]
ACTIONS = [{"tool": "retrieve", "arguments": {"query": "answer"}},
           {"tool": "read", "arguments": {"path": "src.py"}},
           {"final": "Fixture answer: 42."}]


def run(directory):
    if not __debug__:
        raise RuntimeError("diagnostic requires assertions enabled")
    directory = Path(directory)
    directory.mkdir(mode=0o700, exist_ok=False)
    scenarios = []
    for name in ("completed", "cumulative_cap", "generation_error"):
        case = directory / name
        case.mkdir(mode=0o700)
        forwarded, tool_calls = [], []
        generations = 0

        def transport(path, body):
            nonlocal generations
            forwarded.append({"path": path, "request_sha256": sha(body)})
            if path.endswith("input_tokens"):
                status, response = 200, wire({"object": "response.input_tokens", "input_tokens": 8})
            else:
                if name == "generation_error":
                    forwarded[-1]["outcome"] = "transport_error"
                    raise OSError("fixture-private-exception")
                action = ACTIONS[generations]
                generations += 1
                status, response = 200, wire({"model": "fixture-model", "status": "completed",
                    "usage": {"input_tokens": 8, "output_tokens": 16, "total_tokens": 24},
                    "output": [{"type": "message", "role": "assistant", "content": [
                        {"type": "output_text", "text": wire(action).decode()}]}]})
            forwarded[-1].update(status=status, response_sha256=sha(response))
            return status, response

        class Broker:
            def handle(self, request):
                tool_calls.append(json.loads(wire(request)))
                return {"jsonrpc": "2.0", "id": request["id"], "result": {
                    "content": [{"type": "text", "text": "src.py:1 fixture return 42"}], "isError": False}}

        with RecordedTransport(case / "transport", transport, attempt_limit=6) as recorder:
            with ProviderRequestSession(case / "provider", recorder,
                    token_limit=144 if name == "cumulative_cap" else 216,
                    attempt_limit=3, count_limit=3) as provider:
                report = run_loop(case / "loop", provider, Broker(), task="Fixture: inspect answer.",
                    tools=TOOLS, model="fixture-model", max_turns=3, max_actions=2, max_output_tokens=64)
                summary = provider.summary()
            recorded = recorder.summary()
        _save(case / "fixture-calls.json", wire({"transport": forwarded, "tools": tool_calls}))
        verified = reconcile(case / "transport", forwarded, recorded)
        expected = {"completed": ("final_answer", 6, 2, 0),
                    "cumulative_cap": ("provider_admission_denied", 5, 2, 0),
                    "generation_error": ("provider_transport_error", 2, 0, 1)}[name]
        assert report["reason"] == expected[0]
        assert (len(forwarded), len(tool_calls), summary["generation"]["unknown_usage_attempts"]) == expected[1:]
        assert not report["complete_accounting"] and not report["product_promotion_pass"]
        for number, call in enumerate(tool_calls, 1):
            saved = json.loads((case / "loop" / f"tool-response-{number:06d}.json").read_bytes())
            assert saved["id"] == call["id"]
        # Independently inspect the final generation request: complete prior results
        # and the inventory must be present, not just action counts in the report.
        if name == "completed":
            request = json.loads((case / "provider/generation-request-000003.json").read_bytes())
            history = json.loads(request["input"])
            assert history["tools"] == TOOLS and len(history["history"]) == 4
            for number in (1, 2):
                saved = json.loads((case / "loop" / f"tool-response-{number:06d}.json").read_bytes())
                assert history["history"][number * 2 - 1]["response"] == saved
        scenarios.append({"id": name, "loop": report, "provider": summary,
                          "recorded": recorded, "verification": verified})
    report = {"scope": "offline_loop_accounting_only", "diagnostic_pass": True,
              "scenarios": scenarios, "network_requests": 0, "native_retrievals": 0,
              "solver_calls": 0, "fixture_forwarded_calls": sum(
                  s["verification"]["attempts_verified"] for s in scenarios),
              "fixture_token_values": "invented; not tokenizer or provider measurements",
              "complete_accounting": False, "live_provider_verified": False,
              "product_promotion_pass": False, "agent_savings": "unmeasured",
              "graphify_comparison": "unchanged"}
    _save(directory / "report.json", wire(report))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.output_directory)
    print(json.dumps({k: report[k] for k in ("diagnostic_pass", "network_requests", "fixture_forwarded_calls")}))

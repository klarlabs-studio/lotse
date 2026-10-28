"""Offline worker fixtures for interruption accounting; never opens a network."""
import argparse
from pathlib import Path
import time

from bounded_agent import run, wire
from probe_bounded_agent import run as probe
from provider_request import ProviderRequestSession
from recorded_transport import RecordedTransport


def worker(mode, directory):
    directory = Path(directory)
    if mode == "completed":
        probe(directory)
        return
    if mode not in ("provider_hang", "broker_hang"):
        raise ValueError("unsupported fixture")
    directory.mkdir(mode=0o700, exist_ok=False)

    def transport(path, body):
        if path.endswith("input_tokens"):
            return 200, wire({"object": "response.input_tokens", "input_tokens": 8})
        if mode == "provider_hang":
            print("fixture generation entered", flush=True)
            time.sleep(3600)
        return 200, wire({"model": "fixture-model", "status": "completed",
            "usage": {"input_tokens": 8, "output_tokens": 16, "total_tokens": 24},
            "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": wire({"tool": "read", "arguments": {
                    "path": "src.py"}}).decode()}]}]})

    class Broker:
        def handle(self, request):
            print("fixture broker entered", flush=True)
            time.sleep(3600)
            raise RuntimeError("fixture should have been terminated")

    with RecordedTransport(directory / "transport", transport, attempt_limit=2) as recorder:
        with ProviderRequestSession(directory / "provider", recorder,
                token_limit=72, attempt_limit=1, count_limit=1) as provider:
            run(directory / "loop", provider, Broker(), task="Offline interrupted fixture.",
                tools=[{"name": name} for name in ("retrieve", "read")],
                model="fixture-model", max_turns=1, max_actions=1, max_output_tokens=64)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("completed", "provider_hang", "broker_hang"), required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    worker(args.mode, args.output_directory)

"""Experimental, temporary profiler for the frozen read-only brokers.

The profiler patches only the imported broker modules while its context is
active. It records inclusive and exclusive wall time; exclusive buckets are
disjoint and can be summed. This is a diagnostic, not benchmark evidence.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
import importlib
import threading
import time


class ReadonlyProfile:
    """Collect per-call timing aggregates for broker validation and I/O.

    Example::

        with ReadonlyProfile() as profile:
            broker.handle(message)
        report = profile.snapshot()

    Use ``profile.measure('native_runner')`` around the configured child
    process call to include its time in the same nested accounting.
    """

    _active_lock = threading.Lock()

    def __init__(self, *, broker_module="readonly_broker",
                 v2_module="readonly_broker_v2"):
        self.broker_module = broker_module
        self.v2_module = v2_module
        self._data = defaultdict(lambda: {"count": 0, "inclusive_ns": 0,
                                          "exclusive_ns": 0})
        self._stack = threading.local()
        self._patches = []
        self._active = False
        self._wall_start = None
        self._wall_ns = 0

    def _stack_items(self):
        if not hasattr(self._stack, "items"):
            self._stack.items = []
        return self._stack.items

    @contextmanager
    def measure(self, category):
        """Measure an external operation as a nested timing category."""
        items = self._stack_items()
        start = time.perf_counter_ns()
        frame = [category, start, 0]
        items.append(frame)
        try:
            yield
        finally:
            elapsed = time.perf_counter_ns() - start
            items.pop()
            self._record(category, elapsed, max(0, elapsed - frame[2]))
            if items:
                items[-1][2] += elapsed

    def _record(self, category, inclusive, exclusive):
        row = self._data[category]
        row["count"] += 1
        row["inclusive_ns"] += inclusive
        row["exclusive_ns"] += exclusive

    def _instrument(self, obj, attr, category_fn):
        original = getattr(obj, attr)

        def wrapped(*args, **kwargs):
            category = category_fn(args, kwargs)
            items = self._stack_items()
            start = time.perf_counter_ns()
            frame = [category, start, 0]
            items.append(frame)
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = time.perf_counter_ns() - start
                items.pop()
                self._record(category, elapsed, max(0, elapsed - frame[2]))
                if items:
                    items[-1][2] += elapsed

        self._patches.append((obj, attr, original))
        setattr(obj, attr, wrapped)

    def __enter__(self):
        if self._active or not self._active_lock.acquire(blocking=False):
            raise RuntimeError("another read-only profile context is already active")
        try:
            base = importlib.import_module(self.broker_module)
            v2 = importlib.import_module(self.v2_module)
            self._wall_start = time.perf_counter_ns()
            self._active = True
            self._instrument(base, "tokens", lambda _a, _k: "tokenization")
            self._instrument(base, "_json", lambda _a, _k: "serialization")
            self._instrument(base.Broker, "_verify_inventory", lambda _a, _k: "_verify_inventory")
            self._instrument(base.Broker, "_path", lambda _a, _k: "source_validation")
            self._instrument(base.Broker, "_append", lambda _a, _k: "journal_persistence")
            if hasattr(base.Broker, "_verify_file"):
                self._instrument(base.Broker, "_verify_file", lambda _a, _k: "file_verification")
            self._instrument(v2.Broker, "_check_pins", lambda _a, _k: "_check_pins")

            def sha_category(args, _kwargs):
                path = str(args[0]) if args else ""
                name = path.rsplit("/", 1)[-1]
                if name == "index.bin" or name.endswith((".db", ".graph", ".json")):
                    return "sha_artifact"
                if name:
                    return "sha_binary"
                return "sha_unclassified"

            self._instrument(v2, "_sha", sha_category)
        except BaseException:
            for obj, attr, original in reversed(self._patches):
                setattr(obj, attr, original)
            self._patches.clear()
            self._active = False
            self._active_lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            for obj, attr, original in reversed(self._patches):
                setattr(obj, attr, original)
            self._patches.clear()
        finally:
            self._wall_ns = time.perf_counter_ns() - self._wall_start
            self._active = False
            self._active_lock.release()
        return False

    def snapshot(self):
        """Return JSON-serializable per-category aggregates and root wall time."""
        categories = {}
        for name, row in sorted(self._data.items()):
            categories[name] = {
                "count": row["count"],
                "inclusive_ms": row["inclusive_ns"] / 1_000_000,
                "exclusive_ms": row["exclusive_ns"] / 1_000_000,
            }
        return {"categories": categories,
                "instrumented_root_wall_ms": self._wall_ns / 1_000_000,
                "stack_accounting": "exclusive categories are disjoint; only wrapped calls and explicit measure() scopes are counted"}


def profile_readonly(*args, **kwargs):
    """Short alias for constructing :class:`ReadonlyProfile`."""
    return ReadonlyProfile(*args, **kwargs)


# Name used by the paired probe harness.
RequestProfile = ReadonlyProfile


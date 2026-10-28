"""Reusable, bounded request session over one verified private snapshot.

Experimental lifecycle API for measuring repeated requests against a single
snapshot and broker. It does not alter the stdio v4 launcher lifecycle.
"""
from __future__ import annotations

import copy
import subprocess
import time

from readonly_broker_stdio_v4 import artifact_pin, index_options, staged_plan
from readonly_broker_v2 import Broker
from readonly_snapshot_v2 import Snapshot


class Session:
    def __init__(self, plan, arm_id, root, graph, journal, snapshot_parent,
                 max_actions, *, snapshot_factory=Snapshot, broker_factory=Broker,
                 runner=subprocess.run, index_runner=subprocess.run):
        if type(max_actions) is not int or not 1 <= max_actions <= 32:
            raise ValueError("max_actions must be an integer from 1 through 32")
        self.plan = copy.deepcopy(plan)
        self.arm_id = arm_id
        self.source_root = root
        self.graph = graph
        self.journal = journal
        self.snapshot_parent = snapshot_parent
        self.max_actions = max_actions
        self.snapshot_factory = snapshot_factory
        self.broker_factory = broker_factory
        self.runner = runner
        self.index_runner = index_runner
        self.timings = {"snapshot_enter_wall_ms": None, "broker_init_wall_ms": None,
                        "broker_close_wall_ms": None, "snapshot_cleanup_wall_ms": None}
        self.snapshot = None
        self.broker = None
        self.receipt = None
        self.root_path = None
        self._graph_path = None
        self._entered = False
        self._closed = False

    @property
    def active(self):
        return self._entered and not self._closed

    @property
    def root(self):
        if not self.active or self.root_path is None:
            raise RuntimeError("Session is not open")
        return self.root_path

    @property
    def graph_path(self):
        if not self.active or self._graph_path is None:
            raise RuntimeError("Session is not open")
        return self._graph_path

    def __enter__(self):
        if self._entered or self._closed:
            raise RuntimeError("Session cannot be entered more than once")
        self._entered = True
        try:
            pin = artifact_pin(self.plan, self.arm_id, self.source_root, self.graph)
        except BaseException:
            self.close()
            raise
        snapshot = None
        started = time.perf_counter()
        try:
            snapshot = self.snapshot_factory(
                self.source_root, self.plan["source_manifest"], pin["path"], pin["sha256"],
                self.arm_id, self.snapshot_parent,
                **index_options(self.plan, self.arm_id), runner=self.index_runner)
            self.snapshot = snapshot
            snapshot.__enter__()
        except BaseException:
            self.timings["snapshot_enter_wall_ms"] = (time.perf_counter() - started) * 1000
            self.close()
            raise
        self.timings["snapshot_enter_wall_ms"] = (time.perf_counter() - started) * 1000
        try:
            self.receipt = snapshot.receipt
            self.root_path = snapshot.root
            self._graph_path = snapshot.graph_path
            staged = staged_plan(self.plan, self.arm_id, snapshot)
            started = time.perf_counter()
            try:
                self.broker = self.broker_factory(
                    staged, self.arm_id, self.root_path, self._graph_path, self.journal,
                    max_actions=self.max_actions, runner=self.runner)
            finally:
                self.timings["broker_init_wall_ms"] = (time.perf_counter() - started) * 1000
            return self
        except BaseException:
            self.close()
            raise

    def handle(self, request):
        if not self.active or self.broker is None:
            raise RuntimeError("Session is not open")
        return self.broker.handle(request)

    def close(self):
        if self._closed:
            return
        self._closed = True
        first_error = None
        if self.broker is not None:
            started = time.perf_counter()
            try:
                self.broker.close()
            except BaseException as exc:
                first_error = exc
            finally:
                self.timings["broker_close_wall_ms"] = (time.perf_counter() - started) * 1000
                self.broker = None
        if self.snapshot is not None:
            started = time.perf_counter()
            try:
                # Snapshot implementations clean up even when the body raised.
                self.snapshot.__exit__(None, None, None)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
            finally:
                self.timings["snapshot_cleanup_wall_ms"] = (time.perf_counter() - started) * 1000
                self.receipt = self.snapshot.receipt
        if first_error is not None:
            raise first_error

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

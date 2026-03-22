"""Tests for thread-safety of module-level state.

Verifies that context, tracer, tier_context, and llm_provider state
are isolated per-thread using threading.local(), so concurrent pipeline
runs don't corrupt each other's state.
"""

import threading

import pytest

import engine.context
import engine.tier_context as tier_context
import engine.tracer as tracer
from engine.llm_provider import _get_stage_token_overrides, set_stage_token_overrides
from engine.tracer import GENESIS_HASH, init_run


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path):
    """Point engine context at a temp dir and reset all module state."""
    engine.context.init(tmp_path)
    (tmp_path / "state").mkdir()
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0
    tier_context.reset()
    yield
    tracer._run_id = None
    tracer._prev_hash = GENESIS_HASH
    tracer._seq = 0
    tier_context.reset()


# ── Tracer thread isolation ──────────────────────────────────────────────────


class TestTracerThreadIsolation:
    def test_run_ids_isolated_across_threads(self, tmp_path):
        """Each thread gets its own run_id — no cross-contamination."""
        results = {}
        barrier = threading.Barrier(2)

        def worker(name):
            # Each thread initializes its own project context and run
            engine.context.init(tmp_path)
            init_run()
            barrier.wait()  # Sync so both threads are active simultaneously
            results[name] = tracer._run_id

        t1 = threading.Thread(target=worker, args=("thread_1",))
        t2 = threading.Thread(target=worker, args=("thread_2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Each thread should have its own unique run_id
        assert results["thread_1"] != results["thread_2"]
        assert results["thread_1"] is not None
        assert results["thread_2"] is not None

    def test_seq_counters_isolated(self, tmp_path):
        """Sequence counters don't leak between threads."""
        results = {}

        def worker(name, num_traces):
            engine.context.init(tmp_path)
            init_run()
            for _ in range(num_traces):
                tracer.trace(task=name, inputs=[], outputs=[])
            results[name] = tracer._seq

        t1 = threading.Thread(target=worker, args=("worker_a", 3))
        t2 = threading.Thread(target=worker, args=("worker_b", 5))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Each thread's seq reflects only its own traces
        assert results["worker_a"] == 3
        assert results["worker_b"] == 5

    def test_main_thread_unaffected_by_child(self, tmp_path):
        """A child thread's init_run() doesn't clobber the main thread's state."""
        tracer._run_id = None
        tracer._seq = 0

        def child():
            engine.context.init(tmp_path)
            init_run()
            tracer.trace(task="child_task", inputs=[], outputs=[])

        t = threading.Thread(target=child)
        t.start()
        t.join()

        # Main thread state should be untouched
        assert tracer._run_id is None
        assert tracer._seq == 0


# ── Context thread isolation ─────────────────────────────────────────────────


class TestContextThreadIsolation:
    def test_project_dirs_isolated(self, tmp_path):
        """Each thread can have its own project directory."""
        results = {}

        dir_a = tmp_path / "project_a"
        dir_b = tmp_path / "project_b"
        dir_a.mkdir()
        dir_b.mkdir()

        def worker(name, project_dir):
            engine.context.init(project_dir)
            results[name] = engine.context.get_project_dir()

        t1 = threading.Thread(target=worker, args=("a", dir_a))
        t2 = threading.Thread(target=worker, args=("b", dir_b))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results["a"] == dir_a
        assert results["b"] == dir_b


# ── Tier context thread isolation ────────────────────────────────────────────


class TestTierContextThreadIsolation:
    def test_tiers_isolated(self):
        """Each thread can have its own tier selection."""
        results = {}
        barrier = threading.Barrier(2)

        def worker(name, tier):
            tier_context.set_tier(tier)
            barrier.wait()
            results[name] = tier_context.get_tier()

        t1 = threading.Thread(target=worker, args=("t1", "mvp"))
        t2 = threading.Thread(target=worker, args=("t2", "premium"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results["t1"] == "mvp"
        assert results["t2"] == "premium"


# ── LLM provider token overrides thread isolation ────────────────────────────


class TestTokenOverridesThreadIsolation:
    def test_overrides_isolated(self):
        """Each thread gets its own stage token overrides."""
        results = {}
        barrier = threading.Barrier(2)

        def worker(name, overrides):
            set_stage_token_overrides(overrides)
            barrier.wait()
            results[name] = dict(_get_stage_token_overrides())

        t1 = threading.Thread(target=worker, args=("t1", {"design": 4096}))
        t2 = threading.Thread(target=worker, args=("t2", {"design": 8192, "implement": 16384}))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results["t1"] == {"design": 4096}
        assert results["t2"] == {"design": 8192, "implement": 16384}

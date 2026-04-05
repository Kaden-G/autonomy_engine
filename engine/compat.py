"""Compatibility layer for optional Prefect dependency.

With the LangGraph migration, Prefect is no longer a required dependency.
However, the existing task modules use @task decorators and decision_gates.py
uses pause_flow_run. This module provides no-op fallbacks when Prefect is
not installed, so the task functions work as plain Python functions under
LangGraph orchestration.

Design decision:
    Rather than removing all Prefect references from tasks/*.py (which would
    break the Prefect flow entry point), we make the decorators conditional.
    This lets both orchestrators coexist during migration:
    - LangGraph: tasks are plain functions (no-op decorator)
    - Prefect: tasks get the real @task decorator (retry, caching, etc.)

    After migration is complete and Prefect is fully retired, this module
    and the flows/ directory can be removed in a cleanup PR.
"""

try:
    from prefect import flow, task
    from prefect import pause_flow_run
    from prefect.input import RunInput

    PREFECT_AVAILABLE = True
except ImportError:
    PREFECT_AVAILABLE = False

    # No-op decorator that preserves the function as-is but adds a .fn
    # attribute pointing to the original function. Prefect's real @task
    # decorator exposes .fn for unwrapping in tests (e.g., bootstrap_project.fn()).
    # Our no-op must support the same pattern for backward compatibility.
    def task(fn=None, *, name=None, **kwargs):
        """No-op @task decorator when Prefect is not installed."""
        if fn is not None:
            fn.fn = fn  # Self-reference: .fn unwraps to the same function
            return fn
        # Called with arguments: @task(name="foo") → returns decorator
        def wrapper(f):
            f.fn = f
            return f
        return wrapper

    def flow(fn=None, *, name=None, **kwargs):
        """No-op @flow decorator when Prefect is not installed."""
        if fn is not None:
            fn.fn = fn
            return fn
        def wrapper(f):
            f.fn = f
            return f
        return wrapper

    def pause_flow_run(**kwargs):
        """Stub for pause_flow_run when Prefect is not installed.

        Under LangGraph, decision gates use interrupt() instead.
        This stub raises NotImplementedError to catch accidental usage.
        """
        raise NotImplementedError(
            "pause_flow_run requires Prefect. Under LangGraph orchestration, "
            "use interrupt() from langgraph.types instead."
        )

    class RunInput:
        """Stub for Prefect's RunInput when Prefect is not installed."""
        choice: str = ""
        rationale: str = ""

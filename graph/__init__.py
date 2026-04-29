"""LangGraph-based orchestration for the Autonomy Engine.

This package implements the pipeline as a LangGraph StateGraph, providing:
    - Typed pipeline state with formal transitions
    - Built-in checkpointing (resume from failure without re-running LLM calls)
    - Native human-in-the-loop via interrupt()
    - Conditional edges for retry loops and dynamic routing
    - Parallel node execution for independent checks

Architecture decision:
    LangGraph was chosen for orchestration because:
    1. Checkpointing saves LLM costs on pipeline failures ($1-8/run)
    2. interrupt() is a clean HITL primitive
    3. StateGraph makes the pipeline's control flow explicit and testable
    4. Graph-native retry loops (implement→test→re-implement) are first-class

    We deliberately did NOT adopt LangChain's LLM wrappers. Our existing
    engine/llm_provider.py is clean, supports Claude + OpenAI, handles
    retries and caching. LangChain's chain abstractions would add indirection
    without value here.

What stayed the same:
    - All engine/* modules (tracer, evidence, sandbox, design_contract, etc.)
    - State directory structure and file-based artifacts
    - HMAC audit trail
    - Dashboard (reads from same state/ directory)

Security note (MITRE ATLAS: AML.T0047 - ML Supply Chain Compromise):
    LangGraph is added as a new dependency. Its checkpointer serializes
    pipeline state to SQLite. The serialized state includes file paths and
    metadata but NOT LLM API keys or HMAC signing keys. The SQLite DB
    should be treated as sensitive (contains pipeline decisions and prompts)
    and excluded from version control.
"""

from graph.pipeline import build_graph, run_pipeline

__all__ = ["build_graph", "run_pipeline"]

"""No-op ``@task`` decorator — Prefect retired 2026-04-25.

Background:
    The Autonomy Engine migrated its orchestrator from Prefect to LangGraph
    (``graph/pipeline.py``) in v2.0. The Prefect entry point (``flows/``)
    was retired ahead of its 2026-05-21 sunset date as part of the deps
    CVE cleanup, since the Prefect transitive tree was the source of most
    of our pip-audit findings (see ``docs/prefect-sunset-audit.md``).

What's left:
    Every file in ``tasks/`` decorates its public function with ``@task``
    so the Prefect entry point could attach retry / caching / observability
    to it. Under LangGraph the decorator is a no-op that preserves the
    function and exposes a ``.fn`` attribute (some tests call
    ``bootstrap_project.fn()`` to bypass the decorator). Rather than
    edit every ``tasks/*.py`` to drop the import, this single-symbol
    shim keeps them working with zero change.

Future:
    A follow-up PR may drop the decorator entirely from ``tasks/*.py``,
    after which this module can be deleted.
"""


def task(fn=None, *, name=None, **kwargs):
    """No-op ``@task`` decorator.

    Supports both bare and parameterized invocation:
        @task
        def f(): ...

        @task(name="bootstrap")
        def f(): ...

    Adds a ``.fn`` self-reference so existing tests that call
    ``f.fn()`` to bypass the decorator continue to work.
    """
    if fn is not None:
        fn.fn = fn
        return fn

    def wrapper(f):
        f.fn = f
        return f

    return wrapper

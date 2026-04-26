# Contributing

## Contents

- [Development approach](#development-approach)
- [Setup](#setup)
- [Quality assurance](#quality-assurance)
- [Running the test suite](#running-the-test-suite)
- [Filing a change](#filing-a-change)

---

## Development approach

This project was built with Claude as a development partner — architecture decisions, security model, code review, and implementation were all done in collaboration with AI. That's a deliberate choice, not an asterisk.

The engineering value of this project lives in the decisions: why HMAC-SHA256 over plain hash chains, why contracts instead of freeform prompts, why LangGraph's StateGraph over a hand-rolled state machine (and why it replaced Prefect in v2.0), where to draw the threat model boundary and document what's explicitly out of scope. Those decisions are the work. The ability to execute on them efficiently using AI tooling is the skill, not the shortcut.

This is also a project *about* AI-supervised pipelines — building it with AI-assisted development is practicing what it preaches.

## Setup

```bash
pip install -r requirements.txt    # production: pinned versions
pip install -e ".[dev,dashboard]"   # editable with dev tools + dashboard
cp .env.example .env                # add your ANTHROPIC_API_KEY
git config core.hooksPath .githooks # pre-commit key-scan
```

The `.env` file is excluded from version control. The pre-commit hook rejects any commit containing API key patterns.

## Quality assurance

Three independent tools provide ongoing due-diligence on the engine's own codebase (not the AI-generated projects — the engine tests those during every pipeline run).

### Test coverage (pytest-cov)

The engine has 661 automated tests covering the core modules. Coverage on engine/ + graph/ + tasks/ is **67%** on the testable surface. Modules with 0% coverage (cost_estimator, usage_tracker, notifier) depend on the Prefect runtime or pipeline integration, which is only available when running the full pipeline — they are fully exercised during real pipeline runs but can't be unit-tested in isolation.

High-coverage modules (90%+): cache, contract_checker, design_contract, evidence, extraction, log_config, model_registry, prompt_guard, sandbox, spec_normalizer, state_loader, tier_context, tracer, graph.state, tasks.manifest_schema.

### Security scan (bandit)

Bandit (Python SAST — Static Application Security Testing) scans the engine for common security anti-patterns. Baseline results across the engine:

- **1 HIGH (known, intentional):** `subprocess call with shell=True` in `engine/evidence.py`. This is the test runner — it executes pre-approved commands from `config.yml` in a sandboxed workspace. The commands are never AI-generated; the AI only produces code, not shell commands. Annotated with `# nosec B602` and documented in [docs/threat-model.md](threat-model.md).
- **LOW / informational:** `subprocess` import/call notices in `engine/sandbox.py` and `engine/evidence.py`. Core to the engine's job (running tests in isolated environments) and use controlled, non-user-supplied arguments.

### Dependency audit (pip-audit)

pip-audit checks all installed packages against the Python Advisory Database for known vulnerabilities. Results for project dependencies fluctuate as upstream CVEs are published and patched — check the CI `security` job for the current status. Project dependencies are pinned in `requirements.txt` for reproducibility.

### Audit trail integrity (tamper tests)

The `trace-integrity` CI job runs 7 named integration tests that synthesize a trace, tamper with it, and assert the `python -m engine.verify_trace` CLI rejects with the correct exit code. See [docs/audit-trail.md](audit-trail.md) and [tests/test_trace_tampering_integration.py](../tests/test_trace_tampering_integration.py).

## Running the test suite

```bash
pytest -q                                        # full suite (~2.5 min)
pytest -x                                        # stop on first failure
pytest --cov=engine --cov=graph --cov=tasks      # with coverage
pytest tests/test_prompt_guard.py -v             # a single file
RUN_DEPRECATED_TESTS=1 pytest tests/             # include Prefect-flow tests (retire 2026-05-21)
```

Lint + format:

```bash
ruff check .
ruff format --check .
ruff check --fix .       # auto-fix
ruff format .            # auto-format
```

## Filing a change

1. Create a branch off `main` with a short descriptive name (`fix/…`, `feat/…`, `docs/…`, `chore/…`).
2. Make focused commits with clear messages (see recent history for style — usually a one-line subject + rationale body).
3. Open a PR against `main`. CI must be green on `lint`, all three `test` matrix jobs, `import-check`, `trace-integrity`, and `GitGuardian Security Checks`. The `security` job may be red on pre-existing upstream CVEs; if your PR introduces new findings, bump the deps as part of the same PR.
4. If your change touches a threat model or introduces a POAM, update [docs/threat-model.md](threat-model.md).

For substantial changes to the orchestrator, prompt-guard, or tracer, include a framework mapping in the PR description (OWASP / NIST / MITRE ATLAS where applicable).

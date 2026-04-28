# Autonomy Engine v2.0

![CI](https://github.com/Kaden-G/autonomy_engine/actions/workflows/ci.yml/badge.svg)
![Tests](https://img.shields.io/badge/tests-661%20passing-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-67%25%20engine%2Bgraph%2Btasks-blue)
![Security](https://img.shields.io/badge/bandit-1%20known%20%7C%200%20unexpected-yellow)
![Deps Pinned](https://img.shields.io/badge/deps-pinned%20%28lock%20file%29-blue)
![Docker](https://img.shields.io/badge/docker-compose%20up-blue)
![Orchestration](https://img.shields.io/badge/orchestration-LangGraph-purple)

An autonomous software build pipeline that turns a project description into working, tested code — with human approval gates, tamper-evident audit trails, and strict quality contracts that keep AI-generated output on-spec.

Built on [LangGraph](https://langchain-ai.github.io/langgraph/). Compatible with Claude and OpenAI.

---

## The problem this solves

When you ask an AI model to write an entire software project, three things consistently go wrong:

1. **Drift.** The AI forgets decisions it made earlier and contradicts itself across files.
2. **No receipts.** You can't prove what the AI was asked, what it produced, or whether anyone reviewed it.
3. **All-or-nothing.** The output is either accepted wholesale or thrown away — there's no structured quality gate.

The engine addresses all three by wrapping AI code generation in a pipeline with formal contracts, evidence-based testing, and a cryptographically signed audit trail.

## How it works (60-second version)

```
You describe what to build
        ↓
Engine creates a binding design contract (JSON blueprint)
        ↓
AI writes code in chunks, each checked against the contract
        ↓
Code is extracted into a standalone project folder
        ↓
Automated tests run: syntax, imports, linting, type safety, contract compliance
        ↓
A verification report gives a go/no-go recommendation
        ↓
Every step is recorded in a tamper-evident audit log
```

At any stage, the pipeline can pause and ask a human to approve before continuing. You stay in control; the AI stays in its lane.

## Quickstart

```bash
git clone https://github.com/Kaden-G/autonomy_engine.git
cd autonomy_engine
cp .env.example .env               # add your ANTHROPIC_API_KEY
docker compose up                  # → http://localhost:8501
```

Create a project, pick a tier, launch a build. More options (local install, CLI-only): [docs/usage.md](docs/usage.md).

## Documentation

| Topic | Doc |
|---|---|
| Pipeline stages, orchestration, contracts, tiers, gates, project layout | [docs/architecture.md](docs/architecture.md) |
| Intake, running, dashboard, supported project types, development setup | [docs/usage.md](docs/usage.md) |
| Env vars, `config.yml`, `models.yml`, `DECISION_GATES.yml`, efficiency, resilience | [docs/configuration.md](docs/configuration.md) |
| Security model, prompt-injection defense (OWASP LLM01), POAMs, framework mappings | [docs/threat-model.md](docs/threat-model.md) |
| HMAC chain, `verify_trace` CLI, CI enforcement, key management | [docs/audit-trail.md](docs/audit-trail.md) |
| Prefect → LangGraph migration, compat shim, retired-features / sunset plan | [docs/migration-langgraph.md](docs/migration-langgraph.md) |
| Setup, QA, test suite, filing a change | [docs/contributing.md](docs/contributing.md) |

## Security & responsible disclosure

The engine's threat model is documented at [docs/threat-model.md](docs/threat-model.md), including explicit non-goals. If you discover a security issue, please open a GitHub issue with the `security` label — or for sensitive reports, contact the maintainer directly (see commit `Author` metadata).

The project's HMAC audit trail is verifiable with `python -m engine.verify_trace --run-id <id>`. CI enforces tamper-detection on every PR via the `trace-integrity` job.

## License

MIT — see [LICENSE](LICENSE).

## Maintainer

[Kaden-G](https://github.com/Kaden-G). This project was built with Claude as a development partner; see [docs/contributing.md](docs/contributing.md) for the approach rationale.

# Configuration

## Contents

- [Environment variables](#environment-variables)
- [config.yml — runtime settings](#configyml--runtime-settings)
- [models.yml — model registry](#modelsyml--model-registry)
- [DECISION_GATES.yml — gate policies](#decision_gatesyml--gate-policies)
- [Efficiency features](#efficiency-features)
- [Resilience](#resilience)
- [Operations](#operations)

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `AE_ENV` | *(unset)* | Loads `config.<env>.yml` (e.g., `production` → `config.production.yml`) |
| `AE_CONFIG_PATH` | *(unset)* | Explicit config file path (overrides `AE_ENV`) |
| `AE_LOG_FORMAT` | `text` | Log output format: `text` (human-readable) or `json` (structured) |
| `AE_LOG_LEVEL` | `INFO` | Root log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `AE_TRACE_KEY_DIR` | `~/.autonomy_engine/keys` | HMAC key storage directory, or `keyring:<service>` — see [docs/audit-trail.md](audit-trail.md) |
| `AE_ACTOR` | *(unset — falls back to `$USER`)* | Override the actor string recorded on decision-gate entries |
| `ANTHROPIC_API_KEY` | *(required)* | Claude API key (loaded from `.env`) |
| `OPENAI_API_KEY` | *(optional)* | OpenAI API key (loaded from `.env`) |
| `AUTONOMY_ENGINE_PROJECT_DIR` | *(unset — uses CWD)* | Override the project dir the engine and dashboard read from |
| `RUN_DEPRECATED_TESTS` | *(unset — off)* | Set to `1` to include Prefect-flow tests that retire 2026-05-21 |

## config.yml — runtime settings

Controls how the engine runs (which AI model, token budgets, gate behavior, sandbox settings):

```yaml
llm:
  provider: "claude"       # or "openai"
  max_tokens: 16384
  claude:
    model: "claude-sonnet-4-20250514"
  openai:
    model: "gpt-4o"
  models:                  # per-stage model overrides (optional)
    design: "claude-sonnet-4-20250514"
    implement: "claude-sonnet-4-20250514"
    verify: "claude-sonnet-4-20250514"

notifications:
  enabled: false           # log-only; replace engine/notifier.py for Slack/email

sandbox:
  enabled: true            # run tests in isolated workspace
  install_deps: true

verify:
  mode: auto               # always_llm | auto (default) | never_llm
  llm_on_fail_summary: true
  llm_on_pass_summary: true

cache:
  llm_ttl_days: 30         # delete LLM cache entries older than 30 days (0 = disable)
  venv_ttl_days: 7          # delete sandbox venvs older than 7 days (0 = disable)

checks: []                 # approved test commands (see config.yml for examples)
```

## models.yml — model registry

Defines output-token limits and per-million-token pricing for all supported models. Both `llm_provider.py` and `cost_estimator.py` load from this file. Adding a new model requires only a YAML edit:

```yaml
models:
  claude-sonnet-4:
    provider: claude
    max_output_tokens: 64000
    pricing:
      input: 3.00
      output: 15.00
  gpt-4o:
    provider: openai
    max_output_tokens: 16384
    pricing:
      input: 2.50
      output: 10.00

defaults:
  max_output_tokens: 4096
  pricing:
    input: 0.0
    output: 0.0
```

Prefix matching is supported — `claude-sonnet-4` matches `claude-sonnet-4-20250514`. A project-local `models.yml` overrides the engine default, enabling per-project model customization.

## DECISION_GATES.yml — gate policies

Controls what happens at each decision point:

```yaml
gates:
  design:
    policy: "pause"        # stop for human approval
  test:
    policy: "skip"         # auto-continue
    default_option: "continue"
  verify:
    policy: "skip"         # auto-continue
    default_option: "accept"
```

Valid policies: `pause`, `auto`, `skip`. When a decision has been auto-taken or skipped, the trace entry records it the same way as a human-approved decision (with `actor="auto-policy"` in the auto case).

## Efficiency features

### Per-stage model selection

Each pipeline stage can use a different AI model. Use a cheaper/faster model for verification and a more capable one for implementation. Configure in `config.yml` under `llm.models`.

### AI response caching

Responses are cached based on the exact inputs (prompt, model, parameters). If you re-run the pipeline without changing inputs, cached responses are reused — no additional API cost. Cache entries are immutable (first response wins, never overwritten).

### Selective verify

The verification stage supports three modes to balance thoroughness against cost:

- **`always_llm`** — always use the AI for verification analysis (original v1.x behavior)
- **`auto`** — skip the AI when test results make the outcome obvious, i.e. all passed or all failed (default)
- **`never_llm`** — purely rule-based verification with structured issue breakdown (zero AI cost)

### Sandbox caching

Test environments (Python virtualenvs) are cached and reused across runs when dependencies haven't changed. A shared package cache further speeds up setup.

### Cache eviction

Both the LLM response cache and sandbox virtualenv cache are automatically cleaned at the start of each pipeline run. Entries older than the configured TTL are deleted — 30 days for LLM responses, 7 days for virtualenvs by default. TTLs are configurable in `config.yml` under the `cache` section. Set a TTL to `0` to disable eviction for that cache type.

### Benchmarking

```bash
python bench/benchmark_runs.py --runs 3 --project-dir ~/projects/myapp
python bench/compare_results.py bench/results_old.json bench/results_new.json
```

See `bench/README.md` for full documentation.

## Resilience

### Retry logic (exponential backoff)

All LLM API calls are wrapped with automatic retry on transient failures — rate limits (429), server errors (5xx), timeouts, and connection drops. The engine retries up to 3 times with exponential backoff (2s → 4s → 8s). Non-transient errors (authentication, validation) fail fast. Retry parameters are configurable per-provider in `config.yml` under `llm.retry.max_retries` and `llm.retry.backoff_base`.

### Thread safety

Module-level state (run IDs, hash chains, project context, tier selection, token overrides) uses `threading.local()` for isolation. This means multiple pipeline runs can execute concurrently in the same process without corrupting each other's audit trails or context. A module proxy pattern (`sys.modules` replacement) preserves backward compatibility with direct attribute access in tests.

### Robust contract extraction

Contract and manifest extraction uses parse-based scanning instead of regex. JSON blocks are found by scanning all fenced code blocks and running `json.loads` on each — no regex for the JSON itself. TypeScript interfaces and enums are extracted with a brace-counting state machine that correctly handles nested types like `{ database: { host: string } }`. Python class extraction captures the full body (fields and methods) using indentation tracking. The old regex patterns silently truncated nested structures; the new approach handles arbitrary nesting depth.

## Operations

### Environment-specific configuration

The engine supports per-environment config files for dev/staging/production deployments:

```bash
# Use an environment-specific config
AE_ENV=production python graph/pipeline.py
# → loads config.production.yml

# Or specify an explicit config path
AE_CONFIG_PATH=my-config.yml python graph/pipeline.py
```

Resolution order: `AE_CONFIG_PATH` (explicit path) → `AE_ENV` (loads `config.<env>.yml`) → `config.yml` (default). This is backward compatible — unset both variables and the engine behaves exactly as before.

### Structured logging

Two output modes controlled by `AE_LOG_FORMAT`:

```bash
# Human-readable (default)
AE_LOG_FORMAT=text python graph/pipeline.py

# JSON-lines for log aggregation (Datadog, CloudWatch, ELK)
AE_LOG_FORMAT=json AE_LOG_LEVEL=DEBUG python graph/pipeline.py
```

JSON mode emits one self-contained JSON object per line with `timestamp`, `level`, `logger`, `message`, and any structured `extra` fields. Log level is controlled by `AE_LOG_LEVEL` (default: `INFO`).

### Graceful shutdown

The pipeline installs SIGTERM and SIGINT handlers at startup. When a container orchestrator (ECS, Kubernetes) sends SIGTERM, the engine writes a `shutdown` trace entry to the audit log before exiting, keeping the HMAC chain valid. A second signal forces immediate exit. Exit codes follow Unix convention: `128 + signal number`.

### Dependency pinning

All dependencies in `pyproject.toml` have upper bounds (e.g., `anthropic>=0.40,<1`) to prevent breaking changes from surprise major-version releases. Exact versions are locked in `requirements.txt` (production) and `requirements-dev.txt` (development), generated by `pip-compile`. For reproducible deployments:

```bash
pip install -r requirements.txt
```

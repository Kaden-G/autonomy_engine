# Sandbox

The sandbox is where AI-generated code actually executes. Two backends, very different security postures. This doc explains what each one does, what it deliberately doesn't do, and how to demo the difference in 60 seconds.

## Contents

- [Why this exists](#why-this-exists)
- [Backend at a glance](#backend-at-a-glance)
- [Local backend — what it is and isn't](#local-backend--what-it-is-and-isnt)
- [Docker backend — the isolation flags, line by line](#docker-backend--the-isolation-flags-line-by-line)
- [Image lifecycle and caching](#image-lifecycle-and-caching)
- [Evidence — what auditors see](#evidence--what-auditors-see)
- [Demo: prove the isolation in 60 seconds](#demo-prove-the-isolation-in-60-seconds)
- [Phase 2 POAMs — what's deferred and why](#phase-2-poams--whats-deferred-and-why)
- [Operational notes](#operational-notes)

---

## Why this exists

The engine's job is to take an untrusted spec, generate code with an LLM, and run that code to produce evidence (lint output, test results, type-check verdicts). The "run that code" step is the highest-blast-radius operation in the pipeline. Everything upstream is data — extraction, contract checking, prompt-guarding. The sandbox is where data becomes execution.

That single boundary deserves its own doc. The threat model summarizes it; this doc is the deep dive.

## Backend at a glance

Selected via `sandbox.backend` in `config.yml`:

| Backend | Isolation | Startup overhead | When to use |
|---|---|---|---|
| `local` (default) | Host tempdir + venv. **Filesystem isolation only.** Network is open, runs as host user, no resource caps. | ~50ms | Trusted specs, fast dev loop, your own machine. |
| `docker` | Ephemeral container per check: `--network none`, `--read-only` root, `--tmpfs /tmp`, non-root uid=1000, `--cpus 2.0 --memory 2g`. Image cached by deps hash. | ~2–3s per check (cached image) | Less-trusted specs, hosted demo, anything you'd let a stranger feed input to. |

**The contract is honest about its limits.** If `sandbox.backend: docker` is set and the Docker daemon isn't reachable, `setup_docker_sandbox` raises `RuntimeError` rather than silently falling back to `local`. Falling back would defeat the security claim — the operator asked for container isolation and would believe they had it.

## Local backend — what it is and isn't

Defined in [`engine/sandbox.py`](../engine/sandbox.py).

**What it does:**

- Copies the extracted project to a host tempdir (`tempfile.mkdtemp`)
- Creates a Python virtualenv (or uses cached `node_modules`)
- Optionally installs dependencies from `requirements.txt` / `pyproject.toml` / `package.json`
- Runs each check via `subprocess.run(shell=True, cwd=workspace, env=sandbox_env)`
- Cleans up on exit via context manager

**What it actually isolates:**

- **Engine files.** Generated code can't `rm -rf` the engine — different directory.
- **Host site-packages.** The venv's `bin/` is prepended to PATH, so `pip install x` lands in the venv, not on your system Python.

**What it does NOT isolate** (this is the entire point of the docker backend):

- Network — generated code can reach `https://anywhere.example.com` and POST your home directory
- Filesystem outside the workspace — `~/.ssh/` is readable by the user the engine runs as
- CPU and memory — a runaway loop can pin a core and OOM the host
- Process — `pkill -9 dashboard` works fine
- UID — runs as the host user with all the host user's privileges

This is fine for the dev loop on a machine where you trust your own specs. It is not fine for accepting strangers' input. That's the explicit positioning.

## Docker backend — the isolation flags, line by line

Defined in [`engine/sandbox_docker.py`](../engine/sandbox_docker.py). Every check is its own ephemeral container; the workspace is bind-mounted in.

The argv assembled by `DockerSandbox.run()`:

```
docker run --rm
  --network none
  --read-only
  --tmpfs /tmp:rw,exec,size=512m
  --user 1000
  --cpus 2.0
  --memory 2g
  --memory-swap 2g
  --workdir /workspace
  -v <workspace>:/workspace:rw
  autonomy-sandbox:py3.11-<deps_hash>
  bash -c "<check command>"
```

Flag-by-flag:

| Flag | What it blocks | Verifying test |
|---|---|---|
| `--rm` | Container leak — every run is throwaway, no state survives | (implicit — `docker ps -a` stays clean) |
| `--network none` | Outbound network. No DNS, no socket connect. The container has no network namespace. | `test_network_is_isolated` |
| `--read-only` | Writes to `/etc`, `/usr`, `/`, anywhere outside the explicitly writable mounts | `test_readonly_root_prevents_writes_outside_workspace` |
| `--tmpfs /tmp:rw,exec,size=512m` | Provides the writable scratch space pip / pytest need, but it's ephemeral and capped at 512MB. Wiped at container exit. | `test_tmpfs_is_writable` |
| `--user 1000` | Container runs as a non-root UID baked into the image. Kernel exploits face an unprivileged target. | `test_runs_as_non_root` |
| `--cpus 2.0` | Hard CPU cap. A `while True: pass` from the LLM caps at 2 cores instead of pinning the host. | (implicit — observable in `docker stats`) |
| `--memory 2g` + `--memory-swap 2g` | Hard memory cap. OOM kills the container, not the host. `--memory-swap` set equal disables swap (no quiet thrash). | (implicit — OOM kill observable) |
| `-v <workspace>:/workspace:rw` | Workspace bind-mounted writable so `ruff --fix` and pytest artifacts surface to the host. This is a deliberate trade-off, see POAMs. | `test_workspace_is_bind_mounted` |

**Why bash -c?** Auto-detected checks rely on shell features (`&&`, `$(...)`, globs). The `bash -c` invocation is fixed engine code, not AI output — the AI never picks the command, it only writes code that the configured check then runs against.

## Image lifecycle and caching

Naive containerization rebuilds the image every run. That's a 30-second tax per check that destroys the dev loop and produces nothing. The engine is smarter about it.

**Image tag:** `autonomy-sandbox:py<version>-<sha256[:12]>`

The hash is computed over `requirements.txt` + `pyproject.toml` + the Python version. Identical deps → identical tag → cache hit. Change a single dependency → new hash → rebuild (once). This is verified by `test_image_is_cached_across_invocations` and `test_hash_changes_with_requirements`.

**Why the hash includes Python version:** A `requirements.txt` that resolves cleanly under 3.11 may not resolve under 3.12. Tagging by Python version too prevents serving a 3.11-resolved image to a 3.12-requested run.

**Image labels for cleanup:** Every sandbox image carries `LABEL autonomy-engine-sandbox=true`. The Makefile target `make sandbox-gc` prunes images older than 30 days that carry this label, so cleanup is surgical — it never touches the user's other Docker images.

**Common dev tools baked in:** `ruff`, `mypy`, `pytest` are installed at image build time, not at check time. This is what allows the runtime container to have `--network none` — by the time the check runs, everything it needs is already in the image.

## Evidence — what auditors see

Every check produces a structured evidence record. For Docker runs, the metadata block includes:

```json
{
  "backend": "docker",
  "image_tag": "autonomy-sandbox:py3.11-a1b2c3d4e5f6",
  "image_digest": "sha256:...",
  "py_version": "3.11",
  "isolation_flags": [
    "--network=none",
    "--read-only",
    "--tmpfs=/tmp",
    "--user=1000",
    "--cpus=2.0",
    "--memory=2g"
  ],
  "mount_mode": "bind-rw",
  "image_built_this_run": false,
  "image_build_time_s": 0.0
}
```

This is what makes the isolation claim auditable: a reviewer doesn't have to take the doc's word for it. They open the evidence record for any check, see exactly what flags were applied, see the image digest (so they can pull and inspect the layers themselves), and see whether the image was built fresh or served from cache.

The metadata shape is asserted by `test_metadata_reports_docker_backend` — the contract is enforced, not documented and forgotten.

## Demo: prove the isolation in 60 seconds

This is the script for the interview demo. All four facts are checkable in under a minute on a laptop with Docker.

```
1. Switch the config:
   sed -i '' 's/backend: local/backend: docker/' config.yml

2. Run the targeted test suite — every flag has a named test:
   pytest tests/test_docker_sandbox.py -v

3. Show network isolation directly:
   pytest tests/test_docker_sandbox.py::TestDockerSandboxIsolation::test_network_is_isolated -v

4. Show the metadata that lands in evidence:
   pytest tests/test_docker_sandbox.py::TestDockerSandboxIsolation::test_metadata_reports_docker_backend -v
```

The CI job `docker-sandbox-tests` in `.github/workflows/ci.yml` runs the full suite with `AE_REQUIRE_DOCKER=1` set — that flag turns any pytest skip into a hard failure. A broken or silently-disabled backend cannot reach `main`.

## Phase 2 POAMs — what's deferred and why

The Docker backend is a real boundary, not a complete one. Here's the honest gap list. None of these are surprises; each is a deliberate Phase 2 item with a known remediation path.

| Gap | Status | Remediation path |
|---|---|---|
| **No gVisor / Firecracker** — the kernel boundary is the standard Linux container one. A kernel-level container escape is not blocked. | Deferred | Switch the runtime to `runsc` via `--runtime=runsc`. Requires the gVisor daemon installed on the host. Wire through `cfg["docker"]["runtime"]`. |
| **No seccomp allowlist profile** — uses Docker's default seccomp filter, which is broad (drops ~44 syscalls; a full allowlist would drop hundreds). | Deferred | Author a profile based on the syscalls actually invoked by ruff/mypy/pytest, ship as `sandbox.seccomp.json`, attach via `--security-opt seccomp=...`. |
| **Workspace bind mount is rw** — the LLM's code can modify files in its own workspace. This is needed so `ruff --fix` and pytest's `__pycache__` work, but it does mean an attacker who got past prompt-guard could plant a payload back into the workspace. | Deferred | Mount workspace ro and copy evidence-side-effects out at teardown via a separate writable output volume. Adds I/O cost; deferred until threat justifies it. |
| **Node.js projects fall back to `local`** — the Docker image template is Python-only for Phase 1. Detected-as-Node projects log a warning and use the local backend. | Deferred | Add a Node base image (`node:20-slim`) keyed by `package-lock.json` hash. Same caching pattern as Python. |
| **Engine-side `subprocess.run` calls** — the engine itself shells out on the host for image builds (`docker build`), daemon probes (`docker version`), and venv setup. These execute engine code, not AI output, so the isolation claim for AI-generated code is unaffected. Documented here so reviewers can see the precise boundary. | Documented limitation | No remediation needed; the distinction is the right one. The narrower true statement is "AI-generated code never shells out on the host when `backend=docker`." |

The deferred items above are scoped Phase 2 work with named remediation paths, not unknowns.

## Operational notes

**Platform.** Docker Desktop on macOS and Windows runs containers in a Linux VM, so the `uid=1000` non-root user works correctly even when the host is not Linux. Native Linux with SELinux enforcing may need `:Z` on the bind mount; add it to the `-v` argument if users report `Permission denied` on workspace writes that should succeed.

**First-run cost.** The first invocation per dependency spec builds the image — typically 30–60 seconds depending on what's in `requirements.txt`. Every subsequent run with the same deps hits the cache and pays only the ~2–3s container startup. The dashboard surfaces `image_built_this_run: true` on the cold start so the cost is visible, not surprising.

**Cleanup.** Run `make sandbox-gc` to prune sandbox images older than 30 days. Filtered by `label=autonomy-engine-sandbox=true` so it only touches images this engine created.

**Failure mode if Docker dies mid-run.** A container that crashes (OOM, timeout) returns a non-zero exit code and stderr — the check is recorded as failed in the evidence record with `exit_code` and `stderr` populated. The pipeline does not retry the check; the verify stage receives the failure and routes accordingly.

**Framework mapping.** MITRE ATLAS T1609 (Container Administration Command) · NIST AI RMF GOVERN 1.4 (Risk management for autonomous systems) · OWASP LLM02 (Insecure Output Handling — untrusted code execution).

# Audit Trail

## Contents

- [HMAC chain model](#hmac-chain-model)
- [Verifying a run](#verifying-a-run)
- [CI enforcement](#ci-enforcement)
- [Key management (baseline — P0-3)](#key-management-baseline--p0-3)
- [Audit report bundles](#audit-report-bundles)
- [Threat coverage and POAMs](#threat-coverage-and-poams)

---

## HMAC chain model

**What it is:** Every pipeline run generates a unique cryptographic key. Each log entry is signed with that key using HMAC-SHA256 (a standard method for creating a tamper-evident signature). Entries are also chained — each one references the signature of the previous entry — so inserting, deleting, or reordering entries breaks the chain.

**What this protects against:** If someone edits the audit log after the fact (for example, changing a "test failed" result to "test passed"), the signatures won't match and verification will flag the tampering. Unlike a plain hash chain (which an attacker could recompute from scratch), the HMAC approach requires the secret key — so modifying and re-signing the log isn't possible without it.

**What this does NOT protect against:** An attacker with access to both the log file and the key file on disk can forge valid entries. In a production environment, the key should be stored in an external key management system (KMS or HSM). The current design is appropriate for development-time integrity verification, not adversarial forensics.

## Verifying a run

Verify a run's HMAC chain locally:

```bash
python -m engine.verify_trace --run-id <run-id>              # human output
python -m engine.verify_trace --run-id <run-id> --json       # CI-friendly
```

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | chain valid |
| 1 | chain invalid (tamper: HMAC mismatch, reorder, missing entry) |
| 2 | verification impossible (missing key file or missing trace.jsonl) |

The 1-vs-2 split is deliberate. CI treats 1 as "reject merge" and 2 as "flag for human — something structural is off." Conflating them would hide an attacker flipping a byte behind "runner was misconfigured."

Example tamper output:

```text
[INVALID] run=20260421-abc entries=12
  failure at seq 3: HMAC mismatch — entry has been modified
```

Truncation from the tail is **not** considered tamper — the chain up to the cut is still verifiable, so the CLI returns exit 0 and reports `"entries": N` where N is the number of intact entries. Callers who need the last-known-good sequence can compute it as `entries - 1`.

## CI enforcement

The `trace-integrity` job in `.github/workflows/ci.yml` runs [`tests/test_trace_tampering_integration.py`](../tests/test_trace_tampering_integration.py) on every PR — 7 named tests covering HMAC flipping, reorder, deletion, truncation-as-not-tamper, missing-key exit 2 distinct from tamper exit 1, and JSON-output parseability. If the CI checkout contains any `state/runs/*/trace.jsonl`, the job also verifies each of those real runs end-to-end.

On failure, the job uploads the offending `trace.jsonl` as a build artifact so the specific `seq` break is inspectable after the job ends.

**Framework mapping:** OWASP ASVS V7.1 (Log Tamper Protection) · NIST AI RMF MEASURE 2.7 (Traceability).

## Key management (baseline — P0-3)

The HMAC signing key lives at `~/.autonomy_engine/keys/<run_id>.key` (dir 0700, file 0600). Moving the key out of `state/runs/` closes the "attacker with write access to the trace dir gets both the log and the key" path — the exact scenario the HMAC design is supposed to prevent.

Override with `AE_TRACE_KEY_DIR`:

```bash
AE_TRACE_KEY_DIR=/absolute/path/to/keys python graph/pipeline.py
AE_TRACE_KEY_DIR=keyring:autonomy-engine python graph/pipeline.py  # OS keyring
```

The filesystem key file is created with `os.open(O_WRONLY|O_CREAT|O_EXCL, 0o600)` so the mode is set at creation time and is not loosened by the umask. Perms are re-verified after write; a loose mode logs a WARNING (defense in depth — some filesystems / samba mounts silently relax mode bits).

### Migration shim

A legacy run with the key at the old `state/runs/<id>/.trace_key` path is auto-migrated on first verification — the key moves to the new location and a `.trace_key_moved` breadcrumb is left in the run dir.

### POAM

**What this mitigates:** A local attacker with write-only access to `state/runs/` cannot forge entries — they don't have the key.

**What this does NOT mitigate:** A local attacker running as the *same user* as the engine can read the key from `~/.autonomy_engine/keys/` and forge. **Remediation path:** optional ed25519 asymmetric signatures (future work) where the signing key never touches the log dir and only the engine host has write access; verifiers use the public key.

**Framework mapping:** NIST SP 800-57 (Key Management — separation of keys from data they protect) · OWASP ASVS V6.2.1.

## Audit report bundles

After a run completes, export a self-contained audit bundle:

```bash
python -m engine.report --run-id <id> [--out path] [--project-dir dir]
```

This produces a compressed archive containing the full trace, config snapshot, evidence records, decisions, a rebuilt artifact manifest, and an integrity verification result. Useful for compliance reviews or sharing results with stakeholders who don't have access to the dashboard.

## Threat coverage and POAMs

See [docs/threat-model.md](threat-model.md) for the full threat model — this doc covers the audit-trail slice only.

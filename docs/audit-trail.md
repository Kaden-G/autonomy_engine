# Audit Trail

The engine produces a tamper-evident log of every pipeline action — each entry HMAC-signed, each entry chained to the previous one, the chain verifiable from a CLI. This doc covers the model, the verifier, the CI integration, the key-management posture, and the path to asymmetric signing.

## Contents

- [HMAC chain model](#hmac-chain-model)
- [What's in an entry](#whats-in-an-entry)
- [Verifying a run](#verifying-a-run)
- [Reading a tamper failure](#reading-a-tamper-failure)
- [CI enforcement](#ci-enforcement)
- [Key management — the baseline](#key-management--the-baseline)
- [POAM: ed25519 asymmetric signing](#poam-ed25519-asymmetric-signing)
- [Audit report bundles](#audit-report-bundles)
- [Threat coverage and POAMs](#threat-coverage-and-poams)

---

## HMAC chain model

**What it is.** Every pipeline run generates a unique 256-bit HMAC key. Each log entry is signed with that key using HMAC-SHA256. Entries also carry a `prev_hash` field referencing the signature of the previous entry — so inserting, deleting, or reordering entries breaks the chain at the point of modification and every entry after it.

**Why HMAC and not a plain hash chain.** A plain SHA-256 chain has the property that an attacker who modifies an entry can recompute every downstream hash and produce a valid-looking chain — there's no secret. HMAC requires the key, so an attacker without the key cannot produce a chain that verifies. The whole tamper-evidence claim turns on that distinction.

**What this protects.** Anyone who modifies the trace file after the fact — flipping a `verify` verdict from REJECTED to ACCEPTED, deleting the entry that recorded a failed test, swapping in a fabricated `design` decision — produces a trace that fails verification. The CLI flags the exact `seq` where the chain breaks. A reviewer at any decision gate can run the verifier and trust what they see, or know they can't.

**What this does NOT protect.** An attacker who has both the key file and the trace file can forge entries from scratch. The baseline mitigation for this (key separation) is below; the structural mitigation (asymmetric signing) is the POAM at the bottom of the doc.

## What's in an entry

Each line in `state/runs/<run_id>/trace.jsonl` is a single JSON object:

```json
{
  "seq": 7,
  "timestamp": "2026-04-25T14:23:11.482Z",
  "task": "verify",
  "inputs": {
    "state/inputs/REQUIREMENTS.md": "sha256:9f2e...",
    "state/tests/test_results.json": "sha256:c41a..."
  },
  "outputs": {
    "state/runs/<id>/decisions/verify.json": "sha256:8b03..."
  },
  "model": "claude-sonnet-4-20250514",
  "prompt_hash": "sha256:1d77...",
  "provider": "claude",
  "max_tokens": 16384,
  "prev_hash": "5e8c4a...",
  "extra": {
    "verdict": "ACCEPTED",
    "prompt_injection_detected": false
  },
  "entry_hash": "a2f9b1..."
}
```

A few things worth calling out:

- **Inputs and outputs are content-hashed**, not just listed by path. If someone swaps the file at the path between when it was logged and when you read the trace, the hash on disk won't match the hash in the entry, and the discrepancy is visible.
- **Prompts are hashed, not stored.** The full prompt text isn't in the trace — just its SHA-256. This keeps the log small and avoids leaking prompt content to anyone with read access to traces, while still letting you prove what prompt was sent (recompute the hash of a candidate prompt and compare).
- **`prev_hash` for the first entry is the genesis value** (`"0" * 64`). Every subsequent `prev_hash` is the previous entry's `entry_hash`. This is how the chain links.
- **`extra` is the structured-metadata escape hatch** — decision details, jailbreak-detector matches, token usage. The verifier treats it as part of the entry for HMAC purposes, so tampering with `extra.verdict` is detected the same way as tampering with `task`.

## Verifying a run

```bash
python -m engine.verify_trace --run-id <run-id>              # human output
python -m engine.verify_trace --run-id <run-id> --json       # CI-friendly
python -m engine.verify_trace --run-id <run-id> --state-dir /path/to/state
```

**Successful verification:**

```text
[VALID] run=20260425-143002-abc entries=14
```

**Exit codes:**

| Code | Meaning | CI action |
|---|---|---|
| `0` | Chain valid — every entry's HMAC matches and sequence is intact. | Allow merge. |
| `1` | Chain invalid — tamper detected, missing entry, or reordering. | **Reject merge.** |
| `2` | Verification impossible — missing key file or missing `trace.jsonl`. | Flag for human. Don't conflate with tamper. |

The `1`-vs-`2` split is deliberate. Treating "the chain failed" the same as "I couldn't find the key" would let an attacker hide a real tamper behind "oh, the runner was misconfigured." The CLI distinguishes them, and CI handles them differently. This is enforced by `_is_missing_key_error` and `_is_missing_trace_error` in [`engine/verify_trace.py`](../engine/verify_trace.py), and tested by `tests/test_trace_tampering_integration.py`.

**JSON output shape** (for CI consumption):

```json
{"valid": true, "entries": 14, "failure": null, "failure_seq": null}
```

```json
{"valid": false, "entries": 14, "failure": "HMAC mismatch — entry has been modified", "failure_seq": 7}
```

`failure_seq` is the first `seq` where the chain broke — the actionable detail an investigator wants. Everything before that seq verified cleanly.

## Reading a tamper failure

When the verifier fails, it tells you which entry broke and which property failed. Three flavors of break, each diagnostic:

**HMAC mismatch** — entry contents changed, signature no longer matches:

```text
[INVALID] run=20260425-abc entries=12
  failure at seq 7: HMAC mismatch — entry has been modified
    (expected a2f9b1c4e5d6..., got 4d8e2f1a3c5b...)
```

**`prev_hash` mismatch** — entry was inserted, deleted, or reordered:

```text
[INVALID] run=20260425-abc entries=12
  failure at seq 5: prev_hash mismatch
    (expected 8c1d4e7f2a9b..., got 1f3e6c8d9a2b...)
```

**`seq` discontinuity** — entry was deleted from the middle:

```text
[INVALID] run=20260425-abc entries=12
  failure at seq 3: seq mismatch (expected 3, got 4)
```

**Truncation from the tail is *not* counted as tamper.** If the trace was cut short — process killed mid-write, disk full at exit — the chain up to the cut is still verifiable, the CLI returns exit `0`, and reports `entries: N` where N is the surviving count. Callers who need the last-known-good seq can compute it as `entries - 1`. The reasoning: a partial trace is still a true trace; calling it tamper would cry wolf and erode the signal when a real tamper happened.

## CI enforcement

The `trace-integrity` job in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs [`tests/test_trace_tampering_integration.py`](../tests/test_trace_tampering_integration.py) on every PR. Seven named tests cover:

- Flipping a single byte in an `entry_hash` (HMAC mismatch)
- Reordering two entries (`prev_hash` mismatch on the first reordered entry)
- Deleting an entry from the middle (`seq` mismatch)
- Truncating from the tail (must NOT be flagged as tamper — exit 0)
- Missing key file (exit 2, distinct from tamper exit 1)
- Missing `trace.jsonl` (exit 2)
- JSON output is parseable

If the CI checkout contains any `state/runs/*/trace.jsonl`, the job also verifies each of those real runs end-to-end. On failure, the offending `trace.jsonl` is uploaded as a build artifact so the broken `seq` is inspectable after the job ends.

The point of putting this in CI is that the verifier is part of the threat model — if the verifier silently regresses, the whole tamper-evidence claim collapses. The CI job is what keeps the claim true over time.

**Framework mapping:** OWASP ASVS V7.1 (Log Tamper Protection) · NIST AI RMF MEASURE 2.7 (Traceability) · NIST SP 800-92 (Log Management).

## Key management — the baseline

The HMAC signing key for a run lives at:

```
~/.autonomy_engine/keys/<run_id>.key
```

with directory mode `0700` and file mode `0600`. Override with `AE_TRACE_KEY_DIR`:

```bash
# Filesystem path (absolute or relative)
AE_TRACE_KEY_DIR=/srv/autonomy_engine/keys python graph/pipeline.py

# OS keyring (uses the `keyring` library, install separately)
AE_TRACE_KEY_DIR=keyring:autonomy-engine python graph/pipeline.py
```

**Why the key lives outside `state/`.** Earlier versions kept the key at `state/runs/<id>/.trace_key` — next to the trace it signed. That co-located the secret with the data it was supposed to protect, so any attacker with write access to `state/` got both the log and the key. Moving the key out closes that path. An attacker now needs write access to `state/runs/` *and* read access to `~/.autonomy_engine/keys/` to forge a valid entry — two places, typically two different access scopes.

**The mode is set at creation time, not after.** The key is written via:

```python
fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
```

The `O_EXCL` refuses to overwrite an existing key for the same run_id (defense against a key-overwrite race). The `0o600` is set atomically with `O_CREAT` rather than via a later `chmod`, so there's no window where the file exists with looser perms. The `os.umask` doesn't affect this mode because `os.open` honors the explicit mode argument. After write, the perms are re-verified — if the filesystem (some Samba mounts, certain Windows paths) silently relaxed them, a WARNING is logged so an operator notices.

**Migration shim for legacy runs.** Runs created before the key relocation landed have their key at `state/runs/<id>/.trace_key`. On first verification, `_load_hmac_key` notices, moves the key to the new location, and leaves a `.trace_key_moved` breadcrumb in the run dir so an operator opening the directory sees where the key went. This keeps old runs verifiable without manual migration.

### Threat coverage at the baseline

| Threat | Status |
|---|---|
| Local attacker with write-only access to `state/runs/` | **Mitigated.** They don't have the key — can't forge. |
| Local attacker who can read `state/` but not modify it | **Mitigated.** Read access doesn't enable forgery. |
| Attacker who compromises the engine host as the same user | **Not mitigated.** They can read the key from `~/.autonomy_engine/keys/` and forge new entries that verify. This is the gap the ed25519 POAM closes. |
| Attacker who steals a backup that includes both `state/` and `~/.autonomy_engine/` | **Not mitigated** by HMAC alone. Backup encryption is the right control here, not the audit trail design. |

**Framework mapping:** NIST SP 800-57 Part 1 §6.1 (Key separation from data) · OWASP ASVS V6.2.1 (Cryptographic key storage).

## POAM: ed25519 asymmetric signing

**Status:** Deferred to Phase 2. The HMAC baseline is appropriate for development-time integrity verification; ed25519 is the next step for adversarial forensics and multi-host trust.

**The gap.** With HMAC, the signing key and the verifying key are the same key. Anyone who can verify can also forge. In a posture where the engine host is the only host that should be able to sign (but auditors, CI, and downstream consumers all need to verify), HMAC requires you to either:

(a) ship the key to every verifier — which makes everyone a potential forger, or
(b) keep the key on the engine host and have verifiers ask the engine to verify on their behalf — which makes the engine host a trust dependency for every verification.

Neither is acceptable for the threat model where you want the trace to stand on its own as evidence to a party who doesn't trust the engine host operator.

**The remediation.** Ed25519 asymmetric signatures separate the two keys:

- **Private signing key** lives only on the engine host (in `~/.autonomy_engine/keys/<run_id>.ed25519.priv`, mode `0600`, never copied off-host).
- **Public verification key** is published with the trace (`state/runs/<id>/trace.pub`) and can be verified by anyone who has it.

A compromised verifier can no longer forge. The engine host remains the trust anchor for signing — Phase 3 work on hardware-backed key storage (TPM, Secure Enclave, or KMS-managed keys with HSM backing) addresses that residual risk. The Phase 2 step alone shrinks the forgery blast radius from "anyone with a copy of the trace" to "the engine operator," which is a meaningfully smaller and more auditable set.

**Implementation sketch** (for the POAM tracker, not yet shipped):

1. Add `cryptography` (or `pynacl`) to the lock file.
2. Generate per-run ed25519 keypair at `init_run()` alongside the HMAC key.
3. Sign each entry with both — keep HMAC for backward compatibility during the migration window, add `entry_signature` field with the ed25519 signature.
4. Update `verify_trace_integrity` to prefer the asymmetric signature when present and fall back to HMAC for legacy runs.
5. Publish the public key in the audit bundle (`engine.report`) so an external verifier can check the trace without contacting the engine host.

**Why this isn't shipped yet.** The HMAC baseline closes the threats Phase 1 was scoped against (the trace dir attacker). The asymmetric step is for an adversarial-forensics posture — sharing traces with parties who shouldn't have to trust the engine operator. That posture isn't part of the current deployment, so the work is queued, not in flight.

**Framework mapping:** NIST SP 800-57 Part 1 §5.1.1.1 (Signature key types) · NIST FIPS 186-5 (EdDSA / ed25519 standard) · OWASP ASVS V6.2.4 (Asymmetric key storage).

## Audit report bundles

After a run completes, export a self-contained audit bundle:

```bash
python -m engine.report --run-id <id> [--out path] [--project-dir dir]
```

The bundle is a compressed archive containing:

- The full `trace.jsonl`
- The config snapshot used for the run (`config_snapshot.yml`)
- Every evidence record from the test stage
- Every decision record from gate interactions
- A rebuilt artifact manifest
- An integrity verification result (the bundle says, on its face, whether the chain verified at bundle time)

This is the artifact you'd hand to a compliance reviewer, a security team doing post-incident analysis, or a stakeholder who doesn't have access to the live dashboard. It's self-contained — no engine install required to inspect it.

## Threat coverage and POAMs

For the full threat-model context — prompt injection, sandbox isolation, content validation, API key handling — see [docs/threat-model.md](threat-model.md). This doc is the audit-trail slice; the threat model is the whole picture.

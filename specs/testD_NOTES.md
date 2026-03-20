# testD: keykeeper — Pipeline Stress Test Notes

## Why This Spec

This project is designed to exercise specific pipeline features that testA/B/C
didn't fully stress. Here's what each aspect is seeded to trigger:

### Contract System Stress
- **5+ shared canonical types**: User, VaultEntry, AuditEvent, Role (enum),
  TokenPayload — all must be consistent across auth.py, vault.py, audit.py,
  and middleware.py
- **Cross-component imports**: middleware imports from auth (JWT validation),
  vault imports from models (VaultEntry), audit imports from models (AuditEvent)
  — if the design contract's import map is wrong, the compliance checker will
  catch it
- **Type field sensitivity**: VaultEntry has an `encrypted_value` field that
  must NEVER be named `value` or `key` (which LLMs love to rename) — tests
  canonical schema enforcement

### Security Constraint Verification
- **"No plaintext in logs"** is a non-functional requirement the verify stage
  must assess against evidence — does the code actually avoid logging secrets?
- **Append-only audit log** — the verify stage should flag if any DELETE or
  UPDATE endpoints exist for audit entries
- **Rate limiting** — testable via the auto-detect check system
- **JWT middleware** — the verify stage should confirm auth is middleware-based,
  not per-handler

### Audit Trail Features
- The project itself is about audit logging, creating a fun meta-layer:
  the engine's audit trail traces the generation of an audit logging system
- Multiple decision gate opportunities: the design stage may flag the
  encryption approach (AES-256-GCM vs alternatives) as an architectural
  tradeoff
- The verify stage gets real security criteria to evaluate, not just
  "does it compile"

### Chunked Implementation Test
- 5 components (auth, vault, audit, middleware, models+config) means 5 chunks
- Each chunk depends on types from models.py — canonical schema must propagate
- The middleware chunk depends on auth — inter-chunk type contracts tested
- MVP tier should handle this (8 expected files, well within limits)

### Auto-Detect Checks
- Python project with requirements.txt → triggers pip install, py_compile,
  import validation, ruff lint, mypy typecheck
- FastAPI project → if tests/ dir exists, triggers pytest
- These checks will produce real evidence records for the dashboard

### What to Watch For
1. Does the design contract correctly identify the 5+ canonical types?
2. Does `encrypted_value` survive all chunks unchanged (not renamed)?
3. Does the audit model have no update/delete methods?
4. Does the verify stage catch any security gaps?
5. Does the contract compliance checker flag missing/extra files?
6. Do the pipeline stages show correct pass/fail in the dashboard?

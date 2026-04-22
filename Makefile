# Autonomy Engine — Makefile
#
# Targets:
#   share-zip    Build a shareable zip of the project, with secret-scan gating.
#   sandbox-gc   Prune Docker sandbox images older than 30 days (scoped by label).

SHELL := /bin/bash

.PHONY: share-zip sandbox-gc

# --- share-zip ---------------------------------------------------------------
#
# Produce a shareable zip, gated by a secret-scan that refuses to bundle
# files containing live-looking credential patterns.
#
# Exclusion categories (mirrored in .zipignore — single source of truth):
#
#   1. Secrets & per-run keys
#      .env, .env.*, .env.local, .trace_key, *.key, *.pem,
#      .streamlit/secrets.toml
#      → OWASP A02 (Cryptographic Failures) — secrets in artifacts
#      → CWE-312  (Cleartext storage of sensitive information)
#
#   2. Pipeline run state (state/)
#      Run logs, intermediate prompts, AI outputs, audit traces.
#      → CWE-532 (Insertion of sensitive information into log file)
#      → OWASP A09 (Security Logging and Monitoring Failures) — adjacent
#
#   3. Build / lint / test caches (__pycache__, .pytest_cache, .ruff_cache,
#      .mypy_cache); virtual envs (.venv/, venv/, env/, node_modules/);
#      VCS internals (.git/); prior zip artifacts (*.zip); OS metadata
#      (.DS_Store, Thumbs.db). Bloat / non-portable / not the recipient's
#      business — not a security category but excluded for hygiene.
#
# Secret scanner choice:
#   `detect-secrets` is NOT currently a project dependency (verified at
#   implementation time — it is not in requirements.lock or
#   requirements-dev.lock). Falling back to a grep over high-confidence
#   credential prefixes per the playbook spec. If `detect-secrets` is added
#   later, swap in `detect-secrets scan --baseline ...` here.
#
# Scan scope:
#   Two passes contribute to the failure list:
#     A. Every file STAGED for the zip (i.e., what the recipient will see).
#     B. Every high-risk-named file in cwd (.env, .env.*, *.key, *.pem,
#        .trace_key) — even if .zipignore excludes it from the zip.
#   Pass B is defense-in-depth: if the user has a real key sitting in a
#   .env that's "safely" excluded from the zip, we still want to surface it
#   so they know. Failing loudly here is cheap; a leaked key is not.
#
# Files matching *.example, *.template, *.sample are always skipped by the
# scanner — they are explicit placeholders and contain illustrative patterns
# (e.g. .env.example with `sk-ant-your-key-here`).

ZIP_NAME := autonomy_engine_$(shell date +%Y-%m-%d_%H%M).zip

share-zip:
	@set -e; \
	if [ ! -f .zipignore ]; then \
		echo "ERROR: .zipignore not found at repo root." >&2; \
		exit 2; \
	fi; \
	STAGE=$$(mktemp -d); \
	trap 'rm -rf "$$STAGE"' EXIT; \
	echo "→ Staging files (excluding patterns from .zipignore)..."; \
	rsync -a --exclude-from=.zipignore ./ "$$STAGE/payload/"; \
	echo "→ Running secret-scan (grep fallback; detect-secrets not installed)..."; \
	SECRET_RE='sk-ant-|sk-proj-|AKIA[0-9A-Z]{12,}|ghp_[A-Za-z0-9]{20,}|xoxb-[0-9]+-[0-9]+|-----BEGIN'; \
	HITS=""; \
	while IFS= read -r f; do \
		case "$$(basename "$$f")" in \
			*.example|*.template|*.sample|Makefile) continue ;; \
		esac; \
		if grep -lE "$$SECRET_RE" "$$f" >/dev/null 2>&1; then \
			HITS="$$HITS$$f\n"; \
		fi; \
	done < <(find "$$STAGE/payload" -type f); \
	while IFS= read -r f; do \
		[ -z "$$f" ] && continue; \
		case "$$(basename "$$f")" in \
			*.example|*.template|*.sample|Makefile) continue ;; \
		esac; \
		if grep -lE "$$SECRET_RE" "$$f" >/dev/null 2>&1; then \
			HITS="$$HITS$$f\n"; \
		fi; \
	done < <(find . -maxdepth 4 \
		\( -name '.env' -o -name '.env.*' -o -name '*.key' \
		   -o -name '*.pem' -o -name '.trace_key' \) \
		-type f 2>/dev/null); \
	if [ -n "$$HITS" ]; then \
		echo ""; \
		echo "❌ SECRET-SCAN FAILED — refusing to build zip." >&2; \
		echo "Files matching credential patterns:" >&2; \
		printf "$$HITS" | sort -u | sed 's/^/    /' >&2; \
		echo "" >&2; \
		echo "Action: scrub the secret, or add the file to .zipignore if it" >&2; \
		echo "        contains an illustrative placeholder, then re-run." >&2; \
		exit 1; \
	fi; \
	echo "→ Scan clean. Building $(ZIP_NAME)..."; \
	(cd "$$STAGE/payload" && zip -rq "$$OLDPWD/$(ZIP_NAME)" .); \
	SIZE=$$(ls -lh "$(ZIP_NAME)" | awk '{print $$5}'); \
	COUNT=$$(unzip -l "$(ZIP_NAME)" | tail -1 | awk '{print $$2}'); \
	echo ""; \
	echo "✅ Built $(ZIP_NAME)"; \
	echo "   size:  $$SIZE"; \
	echo "   files: $$COUNT"; \
	echo ""; \
	echo "📋 Contents:"; \
	unzip -l "$(ZIP_NAME)"; \
	echo ""; \
	echo "⚠️  Review the file list above before sharing."

# --- sandbox-gc --------------------------------------------------------------
#
# Prune Docker sandbox images older than 30 days (720h).  Scoped by the
# ``autonomy-engine-sandbox=true`` label so unrelated images are never
# touched.  Safe to run anytime — actively-used images (same deps hash)
# will be rebuilt on the next run from Docker's own layer cache.
#
# If Docker is not installed, prints a note and exits 0 so the target is
# still safe to wire into scheduled maintenance without breaking local
# dev loops.
sandbox-gc:
	@if ! command -v docker >/dev/null 2>&1; then \
		echo "Docker not installed — skipping sandbox-gc."; \
		exit 0; \
	fi; \
	echo "→ Pruning autonomy-engine sandbox images older than 30 days..."; \
	docker image prune -f \
		--filter "label=autonomy-engine-sandbox=true" \
		--filter "until=720h"; \
	echo "→ Remaining sandbox images:"; \
	docker image ls --filter "label=autonomy-engine-sandbox=true" \
		--format "table {{.Repository}}:{{.Tag}}\t{{.CreatedAt}}\t{{.Size}}"

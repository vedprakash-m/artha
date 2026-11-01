# Artha — Development Makefile
# Usage: make [target]

PYTHON ?= ~/.artha-venvs/.venv/bin/python
PYTEST ?= $(PYTHON) -m pytest
MAKE_DIR := $(dir $(firstword $(MAKEFILE_LIST)))
RUFF ?= ruff

# macOS: /usr/bin/{git,make} are shims for the selected developer directory and
# refuse to run until the Xcode licence is accepted (re-artha D-01). Without
# touching system settings, prefer the Command Line Tools when they exist.
ifeq ($(shell uname -s),Darwin)
ifneq ($(wildcard /Library/Developer/CommandLineTools/usr/bin/git),)
export DEVELOPER_DIR ?= /Library/Developer/CommandLineTools
endif
endif

.PHONY: test lint ruff import-check lint-state-writes pii-scan validate preflight generate clean check help start install-fonts install-playwright coverage diff-coverage mypy privacy-history hooks

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

start: ## First-time setup — clone → demo briefing in 60 seconds
	@bash setup.sh

test: ## Run full test suite in an isolated copy of the checkout (never against live data)
	$(PYTHON) tools/run_tests_isolated.py tests -q --tb=short --ignore=tests/work

lint: ## Syntax-check all Python files (fails on any error)
	@$(PYTHON) -m compileall -q -x '(__pycache__|\.archive|\.venv)' scripts tests tools artha.py
	@echo "✓ Syntax OK"

ruff: ## Run ruff linter (fails on violations or when ruff is missing)
	@command -v $(RUFF) >/dev/null 2>&1 || { echo "ERROR: ruff not installed — install it (pip install ruff)"; exit 1; }
	@$(RUFF) check scripts/ tests/ tools/ --config pyproject.toml
	@if [ -f scripts/catchup_core/ruff.toml ]; then \
		$(RUFF) check scripts/catchup_core --config scripts/catchup_core/ruff.toml; \
	fi
	@echo "✓ Ruff OK"

import-check: ## Catch 'from scripts.X' imports inside scripts/ (dual-import bug); parse errors fail
	@$(PYTHON) tools/import_check.py scripts

pii-scan: ## Privacy gate: tracked + untracked nonignored working files and unpushed commits
	@$(PYTHON) .githooks/privacy_scan.py --tracked
	@$(PYTHON) .githooks/privacy_scan.py --unpushed

privacy-history: ## One-time audit: every commit reachable from main, remotes and tags
	@$(PYTHON) .githooks/privacy_scan.py --history

hooks: ## Activate the repository privacy hooks (pre-commit, pre-push)
	git config core.hooksPath .githooks

validate: ## Validate example profile against JSON schema
	@$(PYTHON) -c "\
	import json, yaml, jsonschema; \
	profile = yaml.safe_load(open('config/user_profile.example.yaml')); \
	schema = json.load(open('config/user_profile.schema.json')); \
	jsonschema.validate(instance=profile, schema=schema); \
	print('✓ Example profile validates against schema')"

preflight: ## Run Artha preflight checks
	$(PYTHON) scripts/preflight.py

generate: ## Regenerate config/Artha.md from core + identity
	$(PYTHON) scripts/generate_identity.py

clean: ## Remove __pycache__, .pyc files, and test output artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -f test_out.txt test_results.txt test_results2.txt test_results3.txt \
	      tests_output.txt tests_output2.txt last_tests.txt
	@echo "✓ Clean"

lint-state-writes: ## AFW Wave 0: no NEW direct .write_text() outside state writers (ratcheted baseline)
	@$(PYTHON) tools/lint_state_writes.py .

# ── New-core quality gates (owner override 2026-10-25: >=85%, strict typing) ──
CORE_PKG ?= scripts/catchup_core
CORE_TESTS ?= tests/catchup_core
COV_MIN ?= 85
COV_BRANCH_MIN ?= 85
DIFF_BASE ?= origin/main
DIFF_RANGE_NOTATION ?= ...

coverage: ## Line+branch coverage floor on the new core package (fails below COV_MIN)
	@$(PYTEST) $(CORE_TESTS) -q -p no:cacheprovider --cov=$(CORE_PKG) --cov-branch \
		--cov-report=term-missing --cov-report=xml:coverage.xml --cov-fail-under=$(COV_MIN)
	@$(PYTHON) $(MAKE_DIR)tools/check_core_coverage.py coverage.xml --minimum=$(COV_MIN) --branch-minimum=$(COV_BRANCH_MIN)

HARNESS_MODULES = tool_guard session_stop stale_banner lib.action_policy lib.credentials lib.credential_migration lib.action_credentials lib.action_process lib.action_evidence catchup_core.recovery_registry catchup_core.recovery_quarantine catchup_core.quarantine_review catchup_core.recovery_access catchup_core.quarantine_fs_windows catchup_core.session_lock catchup_core.privacy_history catchup_core.work_privacy lib.work_publication catchup_core.storage_adoption
HARNESS_TESTS = tests/unit/test_tool_guard.py tests/unit/test_hook_portability.py tests/unit/test_session_stop.py tests/unit/test_stale_banner.py tests/unit/test_action_policy.py tests/unit/test_action_policy_freshness.py tests/unit/test_credentials.py tests/unit/test_credentials_providers.py tests/unit/test_credential_migration.py tests/unit/test_action_credentials.py tests/unit/test_action_process.py tests/unit/test_action_evidence_dispatch.py tests/catchup_core/test_recovery_registry.py tests/unit/test_recovery_registry_writers.py tests/catchup_core/test_recovery_quarantine.py tests/catchup_core/test_quarantine_review.py tests/catchup_core/test_recovery_access.py tests/catchup_core/test_quarantine_fs_windows.py tests/catchup_core/test_session_lock.py tests/catchup_core/test_preservation_bounded.py tests/catchup_core/test_privacy_anchor_capacity.py tests/catchup_core/test_work_privacy.py tests/unit/test_work_publication.py tests/catchup_core/test_state_checkpoint_cli.py tests/catchup_core/test_storage_adoption.py

# Windows-native code cannot run on POSIX hosts: Windows measures it at the same floor, POSIX leaves it out.
HARNESS_WINDOWS_MODULES = catchup_core.quarantine_fs_windows
HARNESS_WINDOWS_TESTS = tests/catchup_core/test_quarantine_fs_windows.py
ifeq ($(OS),Windows_NT)
HARNESS_RUN_MODULES = $(HARNESS_MODULES)
HARNESS_RUN_TESTS = $(HARNESS_TESTS)
HARNESS_COV_CONFIG = pyproject.toml
else
HARNESS_RUN_MODULES = $(filter-out $(HARNESS_WINDOWS_MODULES),$(HARNESS_MODULES))
HARNESS_RUN_TESTS = $(filter-out $(HARNESS_WINDOWS_TESTS),$(HARNESS_TESTS))
HARNESS_COV_CONFIG = tools/coverage-posix.toml
endif

coverage-harness: ## Combined line+branch coverage floor on the hook and policy modules
	@$(PYTHON) tools/run_tests_isolated.py $(HARNESS_RUN_TESTS) -q -p no:cacheprovider \
		$(foreach m,$(HARNESS_RUN_MODULES),--cov=$(m)) --cov-config=$(HARNESS_COV_CONFIG) --cov-branch --cov-fail-under=$(COV_MIN) \
		--cov-report=term-missing --cov-report=xml:"$(abspath coverage-harness.xml)"

bench: ## Synthetic workload benchmark of the Catch-up core (latency and scaling; no personal data)
	@$(PYTHON) tools/benchmark_core.py

.PHONY: diff-coverage-ready
diff-coverage-ready:
	@untracked=$$(git ls-files --others --exclude-standard -- "$(CORE_PKG)") || exit 1; \
	if [ -n "$$untracked" ]; then \
		printf '%s\n' 'ERROR: changed-line coverage excludes untracked core files:' "$$untracked" \
			'Run git add -- <files> for these core files, then retry diff-coverage.'; \
		exit 1; \
	fi

diff-coverage: diff-coverage-ready coverage ## Changed-line coverage; refuses untracked core files (DIFF_BASE defaults to origin/main)
	@$(PYTHON) "$(MAKE_DIR)tools/check_diff_coverage.py" coverage.xml --compare-branch="$(DIFF_BASE)" \
		--diff-range-notation="$(DIFF_RANGE_NOTATION)" \
		--include='scripts/catchup_core/*' --fail-under=$(COV_MIN)

mypy: ## Strict type check of the new core package
	@$(PYTHON) -m mypy --explicit-package-bases $(CORE_PKG)
	@$(PYTHON) -m mypy --strict --follow-imports=silent --explicit-package-bases scripts/lib/credentials.py scripts/lib/credential_migration.py scripts/lib/action_credentials.py

check: lint ruff import-check lint-state-writes test coverage diff-coverage coverage-harness mypy pii-scan validate ## Full CI check
	@echo "✓ All checks passed"

install-fonts: ## Download Space Grotesk + DM Sans woff2 fonts for CV PDF generation
	@bash scripts/install_fonts.sh

install-playwright: ## Install Playwright Chromium for CV PDF generation (career feature)
	@if pip show playwright >/dev/null 2>&1; then \
		playwright install chromium && echo "✓ Playwright Chromium installed"; \
	else \
		pip install playwright && playwright install chromium && echo "✓ Playwright + Chromium installed"; \
	fi

# ── Eval Layer Gates (specs/eval.md EV-16) ────────────────────────────────────

.PHONY: phase0-gate phase1-gate phase2-gate eval-all-gates eval-test

phase0-gate: ## EV-0d: Run eval summary (accuracy + quality metrics)
	$(PYTHON) scripts/eval_runner.py --summary

phase1-gate: ## EV-5: Score the most recent briefing
	$(PYTHON) scripts/eval_scorer.py --latest

phase2-gate: ## EV-6: Digest connector logs and report error budget
	$(PYTHON) scripts/log_digest.py --json

eval-test: ## EV-12: Run eval test suite only
	$(PYTEST) tests/eval/ --tb=short -q

eval-all-gates: phase0-gate phase1-gate phase2-gate eval-test ## Run all eval gates (phase0 + phase1 + phase2 + tests)
	@echo "✓ All eval gates passed."

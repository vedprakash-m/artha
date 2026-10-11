# Artha — Development Makefile
# Usage: make [target]

PYTHON ?= ~/.artha-venvs/.venv/bin/python
PYTEST ?= $(PYTHON) -m pytest
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

pii-scan: ## Privacy gate: every tracked file plus every commit not yet pushed (secrets, PII, owner identifiers, gitleaks)
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

# ── New-core quality gates (re-artha §12.8: >=90% line+branch, strict typing) ──
CORE_PKG ?= scripts/catchup_core
CORE_TESTS ?= tests/catchup_core
COV_MIN ?= 90

coverage: ## Line+branch coverage floor on the new core package (fails below COV_MIN)
	@$(PYTEST) $(CORE_TESTS) -q -p no:cacheprovider --cov=$(CORE_PKG) --cov-branch \
		--cov-report=term-missing --cov-report=xml:coverage.xml --cov-fail-under=$(COV_MIN)

diff-coverage: ## Changed-line coverage vs origin/main (requires coverage.xml from `make coverage`)
	@$(PYTHON) -m diff_cover.diff_cover_tool coverage.xml --compare-branch=origin/main --fail-under=$(COV_MIN)

mypy: ## Strict type check of the new core package
	@$(PYTHON) -m mypy --explicit-package-bases $(CORE_PKG)

check: lint ruff import-check lint-state-writes test coverage mypy pii-scan validate ## Full CI check
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

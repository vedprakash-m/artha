# Artha — Development Makefile
# Usage: make [target]

PYTHON ?= ~/.artha-venvs/.venv/bin/python
PYTEST ?= $(PYTHON) -m pytest

.PHONY: test lint ruff import-check pii-scan validate preflight generate clean check help start

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

start: ## First-time setup — clone → demo briefing in 60 seconds
	@bash setup.sh

test: ## Run full test suite
	$(PYTEST) tests/ --tb=short -q

lint: ## Syntax-check all Python files
	@find scripts/ -name "*.py" ! -path "*/__pycache__/*" ! -path "*/.archive/*" \
		-exec $(PYTHON) -m py_compile {} \;
	@$(PYTHON) -m py_compile artha.py
	@echo "✓ Syntax OK"

ruff: ## Run ruff linter (F811 redefinition, F401 unused import, etc.)
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check scripts/ tests/ --config pyproject.toml; \
		echo "✓ Ruff OK"; \
	else \
		echo "⚠ ruff not installed — skipping (pip install ruff)"; \
	fi

import-check: ## Catch 'from scripts.X' imports inside scripts/ (dual-import bug)
	@FAIL=0; \
	HITS=$$($(PYTHON) -c "\
	import ast, pathlib; \
	[print(f'{p}:{n.lineno}: from {n.module}') \
	 for p in sorted(pathlib.Path('scripts').rglob('*.py')) \
	 if '__pycache__' not in str(p) \
	 for n in ast.walk(ast.parse(p.read_text())) \
	 if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('scripts.')]" 2>/dev/null); \
	if [ -n "$$HITS" ]; then \
		echo "ERROR: 'from scripts.X' imports found inside scripts/:"; \
		echo "$$HITS"; \
		echo "Use 'from X import ...' instead (scripts/ is on sys.path)."; \
		exit 1; \
	fi; \
	echo "✓ No dual-import patterns"

pii-scan: ## Scan distributable files for PII
	@FAIL=0; \
	for f in config/Artha.core.md config/connectors.yaml config/actions.yaml \
	         config/user_profile.example.yaml config/user_profile.schema.json \
	         prompts/*.md docs/*.md CONTRIBUTING.md README.md; do \
		if [ -f "$$f" ]; then \
			if ! $(PYTHON) scripts/pii_guard.py scan < "$$f" > /dev/null 2>&1; then \
				echo "PII detected: $$f"; FAIL=1; \
			fi; \
		fi; \
	done; \
	[ "$$FAIL" -eq 0 ] && echo "✓ PII scan clean" || exit 1

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

clean: ## Remove __pycache__ and .pyc files
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "✓ Clean"

check: lint ruff import-check test pii-scan validate ## Full CI check
	@echo "✓ All checks passed"

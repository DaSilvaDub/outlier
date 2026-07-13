PYTHON ?= python

RUFF_FILES := $(sort \
	$(shell git -c core.safecrlf=false diff --name-only --diff-filter=ACMR HEAD -- "*.py") \
	$(shell git ls-files --others --exclude-standard -- "*.py"))

.PHONY: lint lint-check typecheck

lint:
ifeq ($(strip $(RUFF_FILES)),)
	@echo No changed Python files to lint.
else
	$(PYTHON) -m ruff check --fix $(RUFF_FILES)
endif

lint-check:
ifeq ($(strip $(RUFF_FILES)),)
	@echo No changed Python files to lint.
else
	$(PYTHON) -m ruff check $(RUFF_FILES)
endif

typecheck:
	$(PYTHON) -m mypy outlier_scrapers
	pyright outlier_scrapers


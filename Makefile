.PHONY: test verify

PYTHON ?= python3

test:
	$(PYTHON) -m pytest -q

verify:
	$(PYTHON) -m pytest -q
	$(PYTHON) -m compileall -q bot risk tests
	@if $(PYTHON) -m bot.cli --mode live >/dev/null 2>&1; then \
		echo "Live-mode hard stop failed"; \
		exit 1; \
	else \
		echo "Live-mode hard stop passed"; \
	fi

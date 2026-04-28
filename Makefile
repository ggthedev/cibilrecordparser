# CIBIL MHTML parser - common workflows.
#
#   make                 same as `make run`
#   make help            list all targets
#   make install         create .venv and install dependencies from requirements.txt
#   make run             parse every src/*.mhtml file (depends on install)
#   make file FILE=<p>   parse one explicit file
#   make query           REPL over the latest output JSON
#   make web             serve the browser UI in the foreground (Ctrl+C to stop)
#   make web start       serve the browser UI in the background (PID file)
#   make web stop        stop the background server
#   make web status      report background-server status
#   make web restart     stop + start in the background
#   make clean-cache     wipe cache/ (regeneratable intermediates)
#   make clean-output    wipe output/ (timestamped finals; destructive!)
#   make clean           clean-cache AND clean-output
#   make distclean       clean + remove .venv/

PYTHON  := .venv/bin/python
PIP     := .venv/bin/pip
SOURCES := $(wildcard src/*.mhtml)

# Browser-UI background-mode config. Override on the command line if needed,
# e.g. `make web start WEB_PORT=8080 WEB_HOST=127.0.0.1`.
WEB_HOST ?= 127.0.0.1
WEB_PORT ?= 5057
WEB_PID  := .web.pid
WEB_LOG  := .web.log

# When invoked as `make web start|stop|status|restart`, capture the action
# word so the `web` recipe can dispatch on it. Evaluated at parse time.
WEB_ACTION := $(firstword $(filter start stop status restart,$(MAKECMDGOALS)))

.DEFAULT_GOAL := run

.PHONY: help install run file query web clean-cache clean-output clean distclean

# Make the action keywords valid no-op goals, but ONLY when `web` is also a
# goal -- so `make start` alone still errors out (start is not a target).
ifneq (,$(filter web,$(MAKECMDGOALS)))
.PHONY: start stop status restart
start stop status restart:
	@:
endif

help:
	@echo "Targets:"
	@echo "  make install              create .venv and install deps"
	@echo "  make run                  parse all src/*.mhtml (default target)"
	@echo "  make file FILE=<path>     parse a single explicit file (any path)"
	@echo "  make query                interactive query REPL over the latest output JSON"
	@echo "  make web                  serve the browser UI in the foreground (Ctrl+C to stop)"
	@echo "  make web start            serve the browser UI in the background"
	@echo "  make web stop             stop the background server"
	@echo "  make web status           background-server status"
	@echo "  make web restart          stop + start in the background"
	@echo "  make clean-cache          wipe cache/ (regeneratable intermediates)"
	@echo "  make clean-output         wipe output/ (timestamped finals -- destructive!)"
	@echo "  make clean                clean-cache AND clean-output"
	@echo "  make distclean            clean AND remove .venv/"
	@echo ""
	@echo "Background server lives at http://$(WEB_HOST):$(WEB_PORT)  (logs: $(WEB_LOG))"
	@echo "Sources detected: $(SOURCES)"

# .venv is created on first install; idempotent.
.venv/bin/python:
	python3 -m venv .venv
	$(PIP) install --quiet --upgrade pip
	@echo "[install] .venv created ($$($(PYTHON) --version))"

# Always reconcile installed packages with requirements.txt so adding a
# dependency only requires editing requirements.txt + re-running `make install`
# (or any target that depends on it). pip is fast when nothing has changed.
install: .venv/bin/python
	@$(PIP) install --quiet -r requirements.txt
	@echo "[install] dependencies up-to-date"

run: install
	@if [ -z "$(SOURCES)" ]; then \
		echo "ERROR: no .mhtml files found in src/"; exit 1; \
	fi
	@set -e; for src in $(SOURCES); do \
		echo "==> parsing $$src"; \
		$(PYTHON) parse_cibil.py "$$src"; \
	done

file: install
	@if [ -z "$(FILE)" ]; then \
		echo "ERROR: FILE not specified."; \
		echo "Usage:  make file FILE=path/to/your-report.mhtml"; \
		exit 1; \
	fi
	@if [ ! -f "$(FILE)" ]; then \
		echo "ERROR: file not found: $(FILE)"; exit 1; \
	fi
	@echo "==> parsing $(FILE)"
	@$(PYTHON) parse_cibil.py "$(FILE)"

query: install
	@$(PYTHON) query_cibil.py

# `make web`                   foreground (existing behavior)
# `make web start|stop|status|restart`  background daemon control
web: install
ifeq ($(WEB_ACTION),start)
	@set -e; \
	if [ -f "$(WEB_PID)" ] && kill -0 $$(cat "$(WEB_PID)") 2>/dev/null; then \
		echo "[web] already running (pid $$(cat $(WEB_PID)))  http://$(WEB_HOST):$(WEB_PORT)"; \
		exit 0; \
	fi; \
	rm -f "$(WEB_PID)" "$(WEB_LOG)"; \
	nohup $(PYTHON) web_app.py --host "$(WEB_HOST)" --port "$(WEB_PORT)" </dev/null >"$(WEB_LOG)" 2>&1 & \
	echo $$! >"$(WEB_PID)"; \
	sleep 0.6; \
	if kill -0 $$(cat "$(WEB_PID)") 2>/dev/null; then \
		echo "[web] started  pid $$(cat $(WEB_PID))  http://$(WEB_HOST):$(WEB_PORT)"; \
		echo "      logs:    $(WEB_LOG)   (tail -f to follow)"; \
	else \
		echo "[web] FAILED to start; last lines of $(WEB_LOG):"; \
		echo '----------------------------------------'; \
		tail -n 20 "$(WEB_LOG)" 2>/dev/null || true; \
		echo '----------------------------------------'; \
		rm -f "$(WEB_PID)"; \
		exit 1; \
	fi
else ifeq ($(WEB_ACTION),stop)
	@if [ ! -f "$(WEB_PID)" ]; then \
		echo "[web] not running (no $(WEB_PID))"; \
		exit 0; \
	fi; \
	PID=$$(cat "$(WEB_PID)"); \
	if kill -0 $$PID 2>/dev/null; then \
		kill $$PID 2>/dev/null || true; \
		sleep 0.3; \
		if kill -0 $$PID 2>/dev/null; then \
			echo "[web] still alive after SIGTERM, sending SIGKILL"; \
			kill -9 $$PID 2>/dev/null || true; \
		fi; \
		echo "[web] stopped (pid $$PID)"; \
	else \
		echo "[web] stale PID $$PID (process not running); cleaning up"; \
	fi; \
	rm -f "$(WEB_PID)"
else ifeq ($(WEB_ACTION),status)
	@if [ -f "$(WEB_PID)" ] && kill -0 $$(cat "$(WEB_PID)") 2>/dev/null; then \
		echo "[web] running  pid $$(cat $(WEB_PID))  http://$(WEB_HOST):$(WEB_PORT)"; \
	elif [ -f "$(WEB_PID)" ]; then \
		echo "[web] stale PID file; server not running"; \
		rm -f "$(WEB_PID)"; \
	else \
		echo "[web] not running"; \
	fi
else ifeq ($(WEB_ACTION),restart)
	@$(MAKE) --no-print-directory web stop
	@$(MAKE) --no-print-directory web start
else
	@$(PYTHON) web_app.py --host "$(WEB_HOST)" --port "$(WEB_PORT)"
endif

clean-cache:
	@rm -rf cache/
	@echo "[clean] cache/ removed"

clean-output:
	@rm -rf output/
	@echo "[clean] output/ removed (timestamped finals are gone)"

clean: clean-cache clean-output

distclean: clean
	@rm -rf .venv/ __pycache__/
	@echo "[clean] .venv/ removed"

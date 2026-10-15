SHELL := /bin/sh

COMPOSE ?= docker compose
IMAGE ?= iris:local
# the unit tests import the bot, which reads these at import time; no real credentials needed
TEST_ENV = -e OWNER=1 -e APP_ID=1 -e APP_HASH=test -e BOT_TOKEN=1:test -e DB_DSN=sqlite:////tmp/iris-test.db

.PHONY: init check safety docs lint compose-config build unit test up down restart ps logs update shell requirements

init:
	@test -f .env || cp .env.example .env
	@mkdir -p data
	@test -f youtube-cookies.txt || touch youtube-cookies.txt
	@printf '%s\n' 'Fill in the first block of .env, then run: make test && make up'

safety:
	@python3 scripts/check_repository_safety.py

docs:
	@python3 scripts/validate_docs.py

lint:
	@python3 -m compileall -q src scripts tests
	@if command -v shellcheck >/dev/null 2>&1; then shellcheck docker-entrypoint.sh; else echo "shellcheck not installed, skipped"; fi

compose-config:
	@$(COMPOSE) config --quiet

check: safety docs lint compose-config

build:
	@$(COMPOSE) build

unit: build
	@docker run --rm --entrypoint python $(TEST_ENV) -v "$(CURDIR)/tests:/tests:ro" -w /app $(IMAGE) -m unittest discover -s /tests -v

test: check unit

up:
	@$(COMPOSE) up -d --build

down:
	@$(COMPOSE) down

restart:
	@$(COMPOSE) restart

ps:
	@$(COMPOSE) ps

logs:
	@$(COMPOSE) logs -f --tail 100

update:
	@git pull --ff-only
	@$(MAKE) up

shell:
	@$(COMPOSE) exec bot sh

requirements:
	@python3 scripts/sync_requirements.py

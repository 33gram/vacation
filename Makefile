COMPOSE = docker compose --env-file .env -f infra/docker-compose.yml

# T-M1-43: версия pip-tools закреплена здесь же — иначе обновление
# pip-compile само по себе могло бы поменять формат/порядок вывода и дать
# диф, не связанный ни с одной реальной зависимостью.
PIP_TOOLS_VERSION = 7.4.1

# PR #162 review: закреплено по digest, не по тегу `python:3.12-slim` —
# тот же довод, что у `bitnamilegacy/openldap` в `infra/docker-compose.yml`:
# тег может быть молча перезаписан другим содержимым, и тогда перегенерация
# `constraints.txt` через месяц дала бы другое разрешение зависимостей по
# причине, не связанной ни с одной из них самих, подрывая именно то
# свойство (воспроизводимость), ради которого заведён этот файл.
# Обновляется явно и отдельно от версий зависимостей: `docker pull
# --platform linux/amd64 python:3.12-slim`, затем `docker image inspect
# --platform linux/amd64 python:3.12-slim --format '{{index .RepoDigests 0}}'`.
LOCK_BASE_IMAGE = python@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d

.PHONY: up down logs test-backend test-frontend lint-backend lint-frontend openapi-export openapi-client check-conflict-markers check-task-checkbox lock-backend

up:
	@test -f .env || { echo "Нет .env — выполните: cp .env.example .env"; exit 1; }
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

# Требуют запущенного `make up` (выполняются внутри контейнера backend).
# Прогоняются на vacation_tracker_test (T-M1-31), а не на dev-базе
# работающего приложения — POSTGRES_DB переопределяется на время команды,
# DATABASE_URL обнуляется, чтобы .env-override (если задан) не перебивал
# переопределение (см. backend/app/core/config.py).
TEST_DB_OVERRIDE = -e DATABASE_URL= -e POSTGRES_DB=vacation_tracker_test

test-backend:
	@$(COMPOSE) exec -T postgres sh -c 'psql -U "$$POSTGRES_USER" -lqt' | cut -d '|' -f 1 | grep -qw vacation_tracker_test || { \
		echo "База vacation_tracker_test не найдена — /docker-entrypoint-initdb.d выполняется только при первой инициализации пустого тома Postgres, на существующем томе она сама не появится."; \
		echo "Восстановление — см. README.md, раздел «Тестирование»."; \
		exit 1; \
	}
	$(COMPOSE) exec $(TEST_DB_OVERRIDE) backend alembic upgrade head
	$(COMPOSE) exec $(TEST_DB_OVERRIDE) backend pytest

lint-backend:
	$(COMPOSE) exec backend ruff check .

test-frontend:
	cd frontend && npm run test -- --run

lint-frontend:
	cd frontend && npm run lint

openapi-export:
	$(COMPOSE) exec backend python scripts/export_openapi.py

openapi-client: openapi-export
	cd frontend && npm run generate:api

# T-M1-51: только git + python3, без docker — прогоняется локально перед
# пушем, а не только в CI.
check-conflict-markers:
	python3 scripts/check_conflict_markers.py

# T-M1-49: без аргументов и без docker — на ветке с открытым PR скрипт
# сам определяет репозиторий/номер/описание через `gh pr view` (в CI
# вместо этого читает PR_NUMBER/GITHUB_REPOSITORY/PR_BODY из контекста
# события; см. docstring scripts/check_task_checkbox.py).
check-task-checkbox:
	python3 scripts/check_task_checkbox.py

# T-M1-43: одноразовый контейнер `--platform linux/amd64`, НЕ обычный
# `backend`-сервис `$(COMPOSE)` — у части пакетов состав зависимостей
# платформенно-условный, а раннер CI и прод linux/amd64, тогда как
# `backend`-сервис на arm64-хосте (например, macOS) сам тоже aarch64.
# Результат не должен зависеть от архитектуры машины, которая выполняет
# эту команду — см. заголовок backend/constraints.txt.
lock-backend:
	docker run --rm --platform linux/amd64 \
		-v "$(CURDIR)/backend:/workspace" -w /workspace \
		$(LOCK_BASE_IMAGE) bash -c " \
			pip install --no-cache-dir --quiet pip-tools==$(PIP_TOOLS_VERSION) && \
			pip-compile --extra dev --strip-extras --no-header --quiet \
				--output-file=/tmp/constraints.body.txt pyproject.toml && \
			cat constraints.header.txt /tmp/constraints.body.txt > constraints.txt \
		"
	@echo "Перегенерировано: backend/constraints.txt. Обновление версий — отдельным коммитом (см. заголовок файла)."

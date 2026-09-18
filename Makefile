COMPOSE = docker compose --env-file .env -f infra/docker-compose.yml

.PHONY: up down logs test-backend test-frontend lint-backend lint-frontend openapi-export openapi-client

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

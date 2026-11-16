COMPOSE = docker compose --env-file .env -f infra/docker-compose.yml

# T-M1-43: версия pip-tools закреплена здесь же — иначе обновление
# pip-compile само по себе могло бы поменять формат/порядок вывода и дать
# диф, не связанный ни с одной реальной зависимостью.
PIP_TOOLS_VERSION = 7.4.1

# PR #162 review: закреплено по digest, не по тегу `python:3.12-slim` —
# тот же довод, что у `bitnamilegacy/openldap` в `infra/docker-compose.yml`:
# тег может быть молча перезаписан другим содержимым, и тогда перегенерация
# `backend/requirements.lock` (`T-M1-55`, прежде — `constraints.txt`,
# `T-M1-43`) через месяц дала бы другое разрешение зависимостей по
# причине, не связанной ни с одной из них самих, подрывая именно то
# свойство (воспроизводимость), ради которого заведён этот файл.
# Обновляется явно и отдельно от версий зависимостей: `docker pull
# --platform linux/amd64 python:3.12-slim`, затем `docker image inspect
# --platform linux/amd64 python:3.12-slim --format '{{index .RepoDigests 0}}'`.
LOCK_BASE_IMAGE = python@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d

# T-M1-57: адресный подъём версий — `make lock-backend LOCK_UPGRADE="-P
# amqp==5.4.1 -P pydantic==2.14.1"` — ровно та же цель, не отдельная
# команда, которую пришлось бы сочинять заново при следующем подъёме.
# Пусто по умолчанию: обычный `make lock-backend` не поднимает ничего
# сверх того, что уже требует `pyproject.toml` (см. комментарий к
# `lock-backend` самой цели). Содержимое ПРОВЕРЯЕТСЯ самой целью перед
# вызовом `pip-compile` (см. ниже) — допустимы только пары `-P имя==
# версия`, где ОБЕ половины ограничены символами настоящих имён/версий
# (не любой токен с `==` где-то внутри — PR #173 review нашёл, что это
# позволяло вырваться из кавычек, см. комментарий у `lock-backend`),
# в частности не `--upgrade`/`-U`: иначе эта переменная была бы дырой в
# защите от дрейфа, которую проверяет `backend/tests/test_lock_backend_
# recipe.py` — тот тест читает ТЕКСТ рецепта и не видит, что подставит
# переменная при вызове.
LOCK_UPGRADE ?=

.PHONY: up down logs test-backend test-frontend test-e2e lint-backend lint-frontend openapi-export openapi-client check-conflict-markers check-task-checkbox check-basis-version lock-backend migrate

up:
	@test -f .env || { echo "Нет .env — выполните: cp .env.example .env"; exit 1; }
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

# T-M2-42: миграции dev-базы - отдельное решение разработчика, `up` их НЕ
# применяет (как и `alembic upgrade head` стоит только в `test-backend` и
# `test-e2e`, для тестовой и e2e-баз). `run --rm`, а не `exec`: одноразовый
# контейнер не исполняет `lifespan`, поэтому работает и тогда, когда
# `backend` не стартует из-за отставшей схемы (`validate_business_rules`).
migrate:
	@test -f .env || { echo "Нет .env — выполните: cp .env.example .env"; exit 1; }
	$(COMPOSE) run --rm backend alembic upgrade head

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

# T-M1-24: отдельный оверрайд компоуза (infra/docker-compose.e2e.yml),
# не обычный `$(COMPOSE)` — почему именно этот оверрайд, см.
# `frontend/playwright.config.ts` (не дублируется здесь, PR #169 review,
# пункт 5). Готовность стека ждём опросом реального признака (как
# `backend-tests` в CI ждёт openldap), не `sleep`. После прогона
# (успешного или нет) возвращает `frontend` к обычному конфигу — иначе
# обычный `make up`/разработческий :5173 молча ломается следующим
# обращением к API. Предполагает, что зависимости уже установлены
# (`cd frontend && npm ci && npx playwright install --with-deps
# chromium`) — этот шаг делается один раз, не на каждый прогон.
COMPOSE_E2E = docker compose --env-file .env -f infra/docker-compose.yml -f infra/docker-compose.e2e.yml

test-e2e:
	@test -f .env || { echo "Нет .env — выполните: cp .env.example .env"; exit 1; }
	$(COMPOSE_E2E) up -d postgres redis openldap
	@echo "Ожидание готовности тестового LDAP..."
	@bash -c ' \
		source .env; \
		for i in $$(seq 1 30); do \
			$(COMPOSE_E2E) exec -T openldap ldapwhoami -x -H ldap://localhost:1389 \
				-D "cn=admin,dc=company,dc=local" -w "$$LDAP_ADMIN_PASSWORD" >/dev/null 2>&1 && exit 0; \
			sleep 1; \
		done; \
		echo "Тестовый LDAP не ответил за 30 секунд"; exit 1 \
	'
	@# T-M1-24, обнаружено прогоном: backend падает при старте на пустой
	@# БД (lifespan проверяет bootstrap-администратора запросом к
	@# `accounts`, которой до миграций не существует) - миграции
	@# обязаны применяться ОДНОРАЗОВЫМ контейнером (`run --rm`) ДО
	@# того, как сервис `backend` поднимется сам, а не после попытки
	@# его поднять. `run --rm` сам ждёт `postgres`/`redis` healthy
	@# (depends_on уже объявлен на сервисе `backend`), отдельный опрос
	@# не нужен.
	$(COMPOSE_E2E) run --rm backend alembic upgrade head
	$(COMPOSE_E2E) up -d
	@echo "Ожидание готовности приложения через nginx..."
	@bash -c ' \
		for i in $$(seq 1 60); do \
			curl -sf http://localhost/healthz >/dev/null 2>&1 && exit 0; \
			sleep 1; \
		done; \
		echo "Не дождались http://localhost/healthz за 60 секунд"; exit 1 \
	'
	@# T-M1-24: BOOTSTRAP_ADMIN_LOGIN/PASSWORD и LDAP_ADMIN_PASSWORD -
	@# секреты окружения (NFR-SEC-004), не хардкод (frontend/e2e/support/
	@# ldapScenario.ts) - playwright запускается на хосте, не в
	@# контейнере, поэтому читает `.env` явно, а не получает их через
	@# `env_file:` компоуза, как backend.
	bash -c 'set -a; source .env; set +a; cd frontend && npx playwright test'; status=$$?; $(COMPOSE) up -d frontend; exit $$status

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

# T-M1-44: без аргументов — на ветке с открытым PR `gh pr diff --name-only`
# сам определяет текущую ветку (см. docstring scripts/check_basis_version.py);
# в CI вместо этого передаётся явный PR_NUMBER из контекста события.
check-basis-version:
	python3 scripts/check_basis_version.py

# T-M1-43/T-M1-55: одноразовый контейнер `--platform linux/amd64`, НЕ
# обычный `backend`-сервис `$(COMPOSE)` — у части пакетов состав
# зависимостей платформенно-условный, а раннер CI и прод linux/amd64,
# тогда как `backend`-сервис на arm64-хосте (например, macOS) сам тоже
# aarch64. Результат не должен зависеть от архитектуры машины, которая
# выполняет эту команду. `--generate-hashes` добавлен в `T-M1-55` — риск
# для arm64 (хэши только amd64-колёс) проверен прогоном и не
# подтвердился: `pip-compile` хэширует все файлы релиза с PyPI, не
# только то, что скачал при разрешении — см. заголовок
# backend/requirements.lock.
#
# PR #171 review, четвёртый круг: временный выходной файл СЕЕТСЯ текущим
# `requirements.lock` перед запуском `pip-compile`, а не остаётся пустым/
# одноразовым. `pip-compile`, видя уже закреплённые версии в файле, на
# который указывает `--output-file`, предпочитает их и поднимает версию
# пакета только если диапазон `pyproject.toml` требует этого или пакет
# явно назван через `-P`/`--upgrade-package` — измерено тремя
# контролируемыми прогонами (посев существующим закреплённым файлом →
# диф пуст; посев + `-P pkg==X` → поднимается ровно названный пакет;
# `--output-file` без посева вовсе → воспроизводятся именно те девять
# версий, что дрейфовали до этого круга). Третий круг того же ревью
# временно возвращал `backend/constraints.txt` отдельным версионным
# полом для `-c` — измерением выяснилось, что это не нужно: посев тем же
# `requirements.lock` даёт тот же результат без второго файла. Удалён
# повторно, не возвращается. Осознанный подъём версий — через `-P` в
# этой же команде (`T-M1-57`), не правка отдельного файла: `make lock-
# backend LOCK_UPGRADE="-P pkg==X.Y.Z ..."` (см. `LOCK_UPGRADE` выше).
#
# T-M1-57: валидация `LOCK_UPGRADE` — первый шаг рецепта, ДО запуска
# контейнера (дешевле упасть здесь, чем после загрузки образа). Без неё
# `LOCK_UPGRADE="--upgrade"` (или `-U`, или что угодно ещё) прошло бы
# прямо в `pip-compile` и обесценило бы весь смысл посева, молча. Разбор
# POSIX-совместимый (`sh`, не `bash` — `/bin/sh` на раннере CI это
# `dash`, где нет массивов) - читает токены парами: `-P`, затем токен
# вида имя==версия, ГДЕ ОБЕ ПОЛОВИНЫ ОГРАНИЧЕНЫ СВОИМ НАБОРОМ СИМВОЛОВ
# (имя — буквы/цифры/`.`/`_`/`-`; версия — буквы/цифры/`.`/`+`/`!`/`-`,
# PEP 440 допускает `!` в эпохе и `+` в локальной версии); что угодно
# иное - отказ до docker run.
#
# Почему значение идёт через окружение, а не подстановкой, — коротко
# (история и прогоны — PR #173 review, круги 1–3, — в докстринге
# backend/tests/test_lock_backend_recipe.py, который этот механизм и
# закрепляет; здесь не повторяется):
# - ОКРУЖЕНИЕ (`export LOCK_UPGRADE_VALUE` у цели, `$$LOCK_UPGRADE_VALUE`
#   в рецепте): `$(LOCK_UPGRADE)`, подставленный Make'ом в ТЕКСТ команды,
#   shell разбирает как код — обратные кавычки/`$(...)` исполняются ещё
#   при разборе строки, до любой проверки `case`; раскрытие уже заданной
#   переменной shell результат повторно не разбирает.
# - ОДИНАРНЫЕ кавычки у `bash -c '...'`: в двойных хостовый shell раскрыл
#   бы `$LOCK_UPGRADE_VALUE`, и bash в контейнере разобрал бы результат
#   как новый код — та же дыра уровнем ниже. Держится и при ослабленном
#   наборе символов: `find_recipe_violations` проверяет текст рецепта.
# - набор допустимых символов (абзац выше) — первый слой, не единственный.
lock-backend: export LOCK_UPGRADE_VALUE := $(LOCK_UPGRADE)
lock-backend:
	@set -- $$LOCK_UPGRADE_VALUE; \
	ok=1; expect_value=0; \
	for tok in "$$@"; do \
		if [ "$$expect_value" = "1" ]; then \
			case "$$tok" in \
				?*==?*) \
					name="$${tok%%==*}"; \
					version="$${tok#*==}"; \
					case "$$name" in \
						*[!A-Za-z0-9._-]*) echo "LOCK_UPGRADE: имя пакета '$$name' содержит символ вне буквы/цифры/./_/- " >&2; ok=0; break ;; \
					esac; \
					case "$$version" in \
						*[!A-Za-z0-9.+!-]*) echo "LOCK_UPGRADE: версия '$$version' содержит символ вне буквы/цифры/./+/!/- " >&2; ok=0; break ;; \
					esac; \
					expect_value=0 ;; \
				*) echo "LOCK_UPGRADE: '$$tok' - ожидался токен вида имя==версия после -P" >&2; ok=0; break ;; \
			esac; \
		else \
			if [ "$$tok" != "-P" ]; then \
				echo "LOCK_UPGRADE: '$$tok' - допустимы только пары -P имя==версия" >&2; ok=0; break; \
			fi; \
			expect_value=1; \
		fi; \
	done; \
	if [ "$$ok" = "1" ] && [ "$$expect_value" = "1" ]; then \
		echo "LOCK_UPGRADE: '-P' без значения на конце" >&2; ok=0; \
	fi; \
	if [ "$$ok" != "1" ]; then exit 1; fi
	docker run --rm --platform linux/amd64 \
		-e LOCK_UPGRADE_VALUE \
		-v "$(CURDIR)/backend:/workspace" -w /workspace \
		$(LOCK_BASE_IMAGE) bash -c ' \
			pip install --no-cache-dir --quiet pip-tools==$(PIP_TOOLS_VERSION) && \
			if [ -f requirements.lock ]; then cp requirements.lock /tmp/requirements.lock.body.txt; fi && \
			set -- $$LOCK_UPGRADE_VALUE && \
			pip-compile --extra dev --strip-extras --generate-hashes --no-header --quiet \
				"$$@" \
				--output-file=/tmp/requirements.lock.body.txt pyproject.toml && \
			cat requirements.lock.header.txt /tmp/requirements.lock.body.txt > requirements.lock \
		'
	@echo "Перегенерировано: backend/requirements.lock. Обновление версий — отдельным коммитом (см. заголовок файла)."

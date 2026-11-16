# Система учёта отпусков

Внутренний HR-сервис для учёта ежегодных оплачиваемых отпусков, больничных, отгулов и командировок сотрудников компании (до 500 человек). Спроектирован по подходу **Spec-Driven Development**: требования → архитектура → задачи → код.

## Статус

🚧 MVP в разработке. Текущий прогресс — чек-боксы в [`docs/tasks.md`](docs/tasks.md), единственном источнике истины по составу и статусу задач (см. раздел «Задачи» ниже).

## Документация

- [`docs/requirements.md`](docs/requirements.md) — функциональные и нефункциональные требования; единственный источник истины по тому, что система должна делать. Версия и перечень открытых вопросов — в самом документе, здесь они не повторяются, чтобы не разойтись с ним
- [`docs/plan.md`](docs/plan.md) — техническая архитектура, стек, модель данных, API
- [`docs/ui.md`](docs/ui.md) — экраны и поведение интерфейса
- [`docs/tasks.md`](docs/tasks.md) — декомпозиция на задачи по этапам M0–M8

Доменный словарь — [`CONTEXT.md`](CONTEXT.md); правила работы агентов и разработчика — [`CLAUDE.md`](CLAUDE.md).

## Технологический стек

| Слой | Технология |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Celery + Redis |
| Frontend | React 18, TypeScript, Vite, Ant Design |
| База данных | PostgreSQL 16 |
| Аутентификация | LDAP/Active Directory (+ локальный резерв) |
| Инфраструктура | Docker Compose, Nginx, on-premise (серверы компании) |

Подробное обоснование — `docs/plan.md`, раздел 1.

## Структура репозитория

```
.
├── backend/            # FastAPI-приложение (см. docs/plan.md §3, §6)
├── frontend/            # React SPA
├── infra/                # docker-compose (+ оверрайд для e2e), nginx, инициализация postgres и тестового openldap
├── docs/                 # requirements.md, plan.md, ui.md, tasks.md
├── scripts/              # проверки репозитория для CI и локально (check_*.py), восстановление зеркала задач в Issues
├── .github/              # workflows, шаблон PR
├── Makefile              # команды разработки (см. «Разработка»)
├── .env.example          # шаблон окружения
└── CLAUDE.md, CONTEXT.md # правила работы и доменный словарь
```

Каталога `infra/backup` и `docker-compose.prod.yml`, которые перечислены в `docs/plan.md` §3, в репозитории пока нет.

## Разработка

Локальный запуск (все команды — из корня репозитория):

```bash
cp .env.example .env   # заполнить значениями
make migrate           # применить миграции к dev-базе (см. ниже, почему отдельным шагом)
make up                # поднять стек в фоне
```

`make up` — это `docker compose --env-file .env -f infra/docker-compose.yml up -d`. Путь к `--env-file` резолвится
относительно текущего каталога, а не относительно `-f`: без явного
`--env-file .env` Compose ищет `.env` рядом с `infra/docker-compose.yml`
(в `infra/`), а не в корне, и подстановка `${POSTGRES_*}`/`${LDAP_*}`
в `infra/docker-compose.yml` молча берёт значения по умолчанию вместо
заданных в корневом `.env`.

### Миграции dev-базы

`make up` миграций **не применяет**: `alembic upgrade head` выполняют только `make test-backend` и `make test-e2e`, и только в своих базах (тестовой и e2e), не в dev-базе. Если схема dev-базы отстаёт от кода (например, после `git pull` с новой миграцией), backend не покажет пустые справочники, а **откажется стартовать**: `lifespan` в `backend/app/main.py` вызывает `validate_business_rules`, и отсутствующий ключ реестра правил (например, `business_timezone`, его добавляет миграция `0011`) — громкий отказ старта. Признак: `http://localhost:8000/healthz` не отвечает, а контейнер `backend` при этом числится `Up` (процесс `uvicorn --reload` жив, приложение в нём не стартовало), причина — в `docker compose --env-file .env -f infra/docker-compose.yml logs backend`.

Порядок: посмотреть версию, применить, поднять.

```bash
docker compose --env-file .env -f infra/docker-compose.yml run --rm backend alembic current
make migrate           # то же, что `run --rm backend alembic upgrade head`
make up
```

Именно `run --rm`, а не `exec`: одноразовый контейнер не исполняет `lifespan`, поэтому работает и тогда, когда `backend` не стартует. `make migrate` не вызывается из `make up` — применение миграции остаётся решением разработчика, а не побочным эффектом запуска. Если `backend` уже запущен и упал на старте, после `make migrate` его нужно перезапустить (`docker compose --env-file .env -f infra/docker-compose.yml restart backend`): `make up` на уже работающем контейнере его не перезапускает.

### Адреса и вход

| Что | Адрес |
|---|---|
| Интерфейс | `http://localhost:5173` |
| API и его схема | `http://localhost:8000/docs` |
| Живость backend | `http://localhost:8000/healthz` |

Интерфейс открывается на **`:5173`**, а не на `:80`. `nginx` на `:80` проксирует `/` на frontend и `/api/` на backend, но frontend собран с `VITE_API_BASE_URL: http://localhost:8000` (`infra/docker-compose.yml`): браузер ходит к backend напрямую, а не через nginx. `CORS_ORIGINS` в `.env.example` разрешает единственный источник — `http://localhost:5173`, поэтому со страницы на `:80` запрос входа отклоняется браузером по CORS (в консоли: «No 'Access-Control-Allow-Origin' header is present»). Чтобы пользоваться `:80`, добавьте `http://localhost` в `CORS_ORIGINS` в `.env` и перезапустите `backend`.

Войти можно локальной учётной записью из `BOOTSTRAP_ADMIN_LOGIN`/`BOOTSTRAP_ADMIN_PASSWORD` в `.env` (`FR-AUTH-005`; значения здесь не приводятся). Запись создаётся при старте, только если в базе нет ни одной активной записи с ролью `system_admin`; при первом входе система требует сменить пароль. Уже существующая запись при старте не перезаписывается, поэтому на старой dev-базе после смены пароля значения из `.env` для входа не подходят (вход вернёт «Неверный логин или пароль»).

### Прочие команды

| Команда | Что делает |
|---|---|
| `make down`, `make logs` | остановить стек, читать его журнал |
| `make lint-backend`, `make lint-frontend` | линтеры (backend — в контейнере) |
| `make test-frontend`, `make test-e2e` | vitest на хосте; Playwright против полного стека (нужны зависимости frontend, см. комментарий в `Makefile`) |
| `make openapi-client` | перегенерировать TS-клиент по схеме backend (см. «CI») |
| `make check-conflict-markers`, `make check-task-checkbox`, `make check-basis-version` | те же проверки, что в CI, локально |

Команды фронтенда (`npm run lint`/`npm run test`/`npm run build`), запускаемые
НЕ через `docker compose` (тот использует `frontend/Dockerfile`, `node:22-slim`,
и версии Node на хосте не касается), а напрямую на хосте — требуют версию Node,
указанную в `.nvmrc` (мажор 22). `.github/workflows/ci.yml` читает тот же файл
(`node-version-file: .nvmrc`), так что версия задана в одном месте, а не
дублируется literal'ом, который пришлось бы поддерживать в двух местах вручную.
`nvm use` в корне репозитория подхватывает версию из `.nvmrc` автоматически;
скрипт `frontend/scripts/check-node-version.mjs` (`preinstall` в
`frontend/package.json`) отказывает в `npm install`/`npm ci` понятным
сообщением, если версия Node не подходит, — вместо трассировки об ESM-модуле из
транзитивной зависимости `jsdom` на непригодной версии.

## Тестирование

`make test-backend` прогоняет backend-тесты **не в dev-базе** работающего приложения, а в отдельной базе `vacation_tracker_test` (T-M1-31) — том же имени, что использует CI (`.github/workflows/ci.yml`), чтобы локальный прогон воспроизводил состояние CI, а не только приближался к нему. Без этого `make test-backend` подключался бы к той же базе и тому же `DATABASE_URL`, что и запущенное через `make up` приложение — а его `lifespan` при старте создаёт bootstrap-учётную запись администратора (`FR-AUTH-005`) обычным `commit`, минуя откат транзакций тестовой фикстуры.

База `vacation_tracker_test` создаётся автоматически скриптом `infra/postgres/init/01-create-test-db.sql` — но **только при первой инициализации пустого тома** Postgres (так работает `/docker-entrypoint-initdb.d` в официальном образе). На уже существующем томе (например, если он был создан до появления этой базы) скрипт не выполнится, и `make test-backend` завершится понятной ошибкой вместо невнятного отказа подключения. Восстановление — один из двух вариантов:

```bash
# Пересоздать том целиком (полный сброс dev-данных):
docker compose --env-file .env -f infra/docker-compose.yml down -v
make up

# Либо создать недостающую базу разово, не трогая остальные данные:
docker compose --env-file .env -f infra/docker-compose.yml exec postgres \
  sh -c 'createdb -U "$POSTGRES_USER" vacation_tracker_test'
```

Отдельная сессионная фикстура в `backend/tests/conftest.py` дополнительно проверяет, что имя базы, к которой подключаются тесты, оканчивается на `_test`, и сразу останавливает прогон понятной ошибкой, если это не так, — так что подключение к dev/prod-базе в обход `make test-backend` (например, прямым `docker compose exec backend pytest`) не пройдёт тихо.

С переменной окружения `PYTEST_FORBID_SKIPS=1` (выставлена в CI, `.github/workflows/ci.yml`) прогон заваливается с ненулевым кодом возврата ровно на том, что сам pytest считает пропуском, — включая пропуск на стадии сбора модуля (`pytest.skip(allow_module_level=True)`, `pytest.importorskip(...)`), но не `xfail`. Это ловит молчаливый пропуск по любой причине, а не только недоступность тестового LDAP, — но не ловит исключение файла из сбора (`collect_ignore`, имя вне маски `test_*.py`, флаги `--ignore`/`-k`/`-m`): pytest такие файлы пропусками не считает, и, в отличие от пропуска, это всегда видимая правка в диффе. Локально без переменной `PYTEST_FORBID_SKIPS` пропуски по-прежнему разрешены (`backend/tests/conftest.py`, докстринг модуля).

## Ветвление и Pull Request'ы

- В `main` изменения попадают только через Pull Request, прямые пуши не делаются. Это порядок работы, а не проверенная настройка GitHub: API защиты веток для этого приватного репозитория отвечает 403 («Upgrade to GitHub Pro»), поэтому принудительность на стороне GitHub не подтверждена.
- **Ревью выполняет агент, а не человек.** Проект ведёт один разработчик с AI-агентами, поэтому обязательное одобрение (approval) в настройках ветки **не включается** — автор PR не может одобрить собственный PR, и такое правило заблокировало бы работу. Вместо approval обязательны два условия: зелёный CI и отчёт скилла `code-review` в комментарии к PR (см. `CLAUDE.md`, раздел «Ревью»).
- Ветки задач: `task/M{этап}-{номер}-кратко`, например `task/M2-33-balance-forecast`.
- В заголовке коммитов и PR указывайте ID задачи (`T-Mx-yy`) — это даёт трассируемость от кода к требованию (`FR-XXX`/`NFR-XXX`) через `docs/tasks.md` и `docs/requirements.md`.
- Шаблон PR — `.github/PULL_REQUEST_TEMPLATE.md`.

## Задачи

Задачи (`T-Mx-yy`) ведутся в [`docs/tasks.md`](docs/tasks.md) — это единственный источник истины по их составу и статусу (чек-бокс `[x]` ставится только когда выполнены все критерии приёмки задачи, `CLAUDE.md`, правило 6). Отдельного зеркала в GitHub Issues/Milestones нет (`T-M1-50`: было, разошлось с `tasks.md` и упразднено) — связь задачи с работой прослеживается по ID в заголовке коммита (`git log --grep T-Mx-yy`) и в описании PR.

`scripts/import_tasks_to_github.py` остаётся в репозитории как средство восстановления зеркала, если решение пересмотрят, — не как часть обычного порядка работы; ограничения скрипта описаны в его докстринге.

## CI

`.github/workflows/ci.yml` — GitHub Actions, срабатывает на Pull Request в `main` и на push в `main`. Семь job'ов, на Pull Request каждому соответствует одна проверка:

| Проверка | Что делает |
|---|---|
| Backend (lint + pytest) | `ruff`, миграции тестовой базы, тестовый OpenLDAP, `pytest` с `PYTEST_FORBID_SKIPS=1` |
| Frontend (lint + vitest + build) | `npm run lint`, vitest, сборка |
| E2E (Playwright, LDAP login) | полный стек через docker compose, вход по LDAP до карточки сотрудника |
| Generated TS client matches backend schema | сгенерированный клиент совпадает со схемой backend |
| No merge conflict markers | в репозитории нет служебных маркеров слияния |
| Task checkbox is ticked when its commits land | отметка задачи в `docs/tasks.md` (только на Pull Request) |
| Основание cites the actual upstream version | версии в строках «Основание» (только на Pull Request) |

Поскольку развёртывание — on-premise (не облако), для реального деплоя на серверы компании потребуется **self-hosted GitHub Actions runner** внутри сети компании либо ручная выкладка — облачный runner GitHub до внутренней инфраструктуры не достучится (открытый вопрос `plan.md` §14, п. 2).

Job `openapi-client-check` (T-M1-28) сверяет сгенерированный TS-клиент (`frontend/src/api/generated/schema.d.ts`) со схемой backend; правка докстринга класса Pydantic-модели меняет OpenAPI-схему (Pydantic кладёт его в JSON-схему как `description`) и требует `make openapi-client` перед коммитом точно так же, как правка самого эндпоинта.

## Секреты и переменные окружения

- `.env` в репозиторий не коммитится (см. `.gitignore`); шаблон — `.env.example`.
- CI секретов GitHub сейчас не использует: тестовое окружение собирается из `.env.example` (`cp .env.example .env`). Появятся секреты — через **GitHub Actions Secrets** репозитория/организации, не в коде.
- Параметры подключения к LDAP/AD (`FR-ADMIN-005`) в `.env` **не хранятся** — они настраиваются через интерфейс администратора приложения и живут в таблице `ldap_settings` (см. `docs/plan.md` §5.2).

## Конфиденциальность

Внутренний проект компании — репозиторий должен оставаться **приватным**. Не публикуйте в коде, Issues или PR реальные персональные данные сотрудников, содержимое `.env`, пароли или иные секреты.

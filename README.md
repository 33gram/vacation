# Система учёта отпусков

Внутренний HR-сервис для учёта ежегодных оплачиваемых отпусков, больничных, отгулов и командировок сотрудников компании (до 500 человек). Спроектирован по подходу **Spec-Driven Development**: требования → архитектура → задачи → код.

## Статус

🚧 MVP в разработке. Текущий прогресс — см. чек-боксы в [`docs/tasks.md`](docs/tasks.md) или доску Issues/Projects (если настроена, см. раздел «Задачи и Milestones» ниже).

## Документация

- [`docs/requirements.md`](docs/requirements.md) — функциональные и нефункциональные требования (v1.2, все открытые вопросы закрыты)
- [`docs/plan.md`](docs/plan.md) — техническая архитектура, стек, модель данных, API
- [`docs/tasks.md`](docs/tasks.md) — декомпозиция на задачи по этапам M0–M8

> **Важно:** если в вашем репозитории эти документы называются иначе (например, `Требования_к_системе_учета_отпусков.md` вместо `requirements.md`), переименуйте их под указанные выше пути либо поправьте ссылки в этом README и в `scripts/import_tasks_to_github.py`.

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
├── infra/                # docker-compose, nginx, скрипты бэкапа
├── docs/                 # requirements.md, plan.md, tasks.md
├── scripts/              # вспомогательные скрипты (импорт задач в Issues и т.п.)
└── .github/              # workflows, шаблоны issue/PR
```

Backend и frontend разворачиваются задачами этапа **M0** (`docs/tasks.md`) — до их выполнения репозиторий содержит только документацию и конфигурацию.

## Разработка

Пока этап M0 не выполнен, окружение не запускается «из коробки» — актуальный порядок настройки см. в `docs/tasks.md`, раздел M0.

После выполнения M0, ожидаемый локальный запуск:

```bash
cp .env.example .env   # заполнить реальными значениями
docker compose --env-file .env -f infra/docker-compose.yml up
```

Обе команды — из корня репозитория. Путь к `--env-file` резолвится
относительно текущего каталога, а не относительно `-f`: без явного
`--env-file .env` Compose ищет `.env` рядом с `infra/docker-compose.yml`
(в `infra/`), а не в корне, и подстановка `${POSTGRES_*}`/`${LDAP_*}`
в `infra/docker-compose.yml` молча берёт значения по умолчанию вместо
заданных в корневом `.env`.

Команды фронтенда (`npm run lint`/`npm run test`/`npm run build`), запускаемые
НЕ через `docker compose` (тот использует `frontend/Dockerfile`, `node:22-slim`,
и версии Node на хосте не касается), а напрямую на хосте — требуют версию Node,
указанную в `.nvmrc` (мажор 22, тот же, что в CI, `.github/workflows/ci.yml`).
`nvm use` в корне репозитория подхватывает её автоматически; `frontend/.npmrc`
(`engine-strict=true`) отказывает в `npm install`/`npm ci` понятным сообщением,
если версия Node не подходит, — вместо трассировки об ESM-модуле из
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

- `main` — защищённая ветка: прямые пуши запрещены, слияние только через Pull Request.
- **Ревью выполняет агент, а не человек.** Проект ведёт один разработчик с AI-агентами, поэтому обязательное одобрение (approval) в настройках ветки **не включается** — автор PR не может одобрить собственный PR, и такое правило заблокировало бы работу. Вместо approval обязательны два условия: зелёный CI и отчёт скилла `code-review` в комментарии к PR (см. `CLAUDE.md`, раздел «Ревью»).
- Ветки задач: `task/T-M{этап}-{номер}-кратко`, например `task/T-M1-06-ldap-client`.
- В заголовке коммитов и PR указывайте ID задачи (`T-Mx-yy`) — это даёт трассируемость от кода к требованию (`FR-XXX`/`NFR-XXX`) через `docs/tasks.md` и `docs/requirements.md`.
- Шаблон PR — `.github/PULL_REQUEST_TEMPLATE.md`.

## Задачи и Milestones

Задачи ведутся как **GitHub Issues**, привязанные к **Milestones** `M0`–`M8` (соответствуют этапам `docs/plan.md`/`docs/tasks.md`). Метки (`labels`) — по функциональным модулям (`module:auth`, `module:requests` и т.д., см. `scripts/import_tasks_to_github.py`).

Чтобы массово создать Milestones и Issues из `docs/tasks.md`, один раз выполните (после `gh auth login`):

```bash
python scripts/import_tasks_to_github.py --repo <owner>/<name> --dry-run   # проверить, что будет создано
python scripts/import_tasks_to_github.py --repo <owner>/<name>             # создать по-настоящему
```

Прогресс по каждому этапу после этого виден во вкладке **Milestones** репозитория без необходимости открывать markdown-файлы.

## CI

`.github/workflows/ci.yml` — GitHub Actions, прогоняет линтер и тесты backend/frontend на каждый Pull Request в `main`. Workflow ожидает структуру backend (`pyproject.toml`, `ruff`, `pytest`, `alembic`) и frontend (`package.json` со скриптами `lint`/`test`/`build`), которая появится по итогам этапа M0 — до этого job'ы будут падать, это ожидаемо.

Поскольку развёртывание — on-premise (не облако), для реального деплоя на серверы компании потребуется **self-hosted GitHub Actions runner** внутри сети компании либо ручная выкладка — облачный runner GitHub до внутренней инфраструктуры не достучится (открытый вопрос `plan.md` §14, п. 2).

Отдельный job `openapi-client-check` (T-M1-28) сверяет сгенерированный TS-клиент (`frontend/src/api/generated/schema.d.ts`) со схемой backend; правка докстринга класса Pydantic-модели меняет OpenAPI-схему (Pydantic кладёт его в JSON-схему как `description`) и требует `make openapi-client` перед коммитом точно так же, как правка самого эндпоинта.

## Секреты и переменные окружения

- `.env` в репозиторий не коммитится (см. `.gitignore`); шаблон — `.env.example`.
- Секреты CI (пароли БД для тестового контура, ключи и т.п.) — через **GitHub Actions Secrets** репозитория/организации, не в коде.
- Параметры подключения к LDAP/AD (`FR-ADMIN-005`) в `.env` **не хранятся** — они настраиваются через интерфейс администратора приложения и живут в таблице `ldap_settings` (см. `docs/plan.md` §5.2).

## Конфиденциальность

Внутренний проект компании — репозиторий должен оставаться **приватным**. Не публикуйте в коде, Issues или PR реальные персональные данные сотрудников, содержимое `.env`, пароли или иные секреты.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY requirements.lock ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts

# T-M1-55: два шага, не один — `--require-hashes` отвергает редактируемую
# (и обычную) установку локального каталога независимо от хэшей (см.
# заголовок requirements.lock), поэтому зависимости и сам проект не
# могут делить одну команду install в режиме хэшей. Шаг зависимостей
# несёт `--require-hashes`, шаг проекта — `--no-deps` (зависимости уже
# поставлены первым шагом, повторное разрешение не нужно и не входит в
# режим хэшей). `-e .`/`-e ".[dev]"` сохранены: правки `backend/app`
# подхватываются без переустановки через volume-монтирование в
# `infra/docker-compose.yml`.
#
# PR #171 review: ВНИМАНИЕ — `INSTALL_DEV` фактически больше НЕ разделяет
# состав установки. Раньше (`-c constraints.txt`, до T-M1-55) `else`-ветка
# ставила только runtime-замыкание: `-c` сам ничего не запрашивает, только
# ограничивает версию уже запрошенного. Теперь первый шаг — ОДИН И ТОТ ЖЕ
# pip install -r requirements.lock --require-hashes в обеих ветках, а
# `-r` ставит ВСЁ перечисленное; замок сгенерирован с `--extra dev` и
# содержит dev-инструменты. Измерено прогоном (не из текста ревью, а
# самостоятельно): 62 дистрибутива в замке, из них 52 входят в
# runtime-замыкание (без `--extra dev`), 10 — только из dev: certifi,
# httpcore, httpx, iniconfig, pluggy, pygments, pytest, pytest-asyncio,
# ruff, sniffio. `pip list` в собранном с `INSTALL_DEV=false` образе
# подтверждает: pytest/ruff и остальные девять присутствуют. Второй шаг
# (`-e ".[dev]"` против `-e .`) на это не влияет под `--no-deps` — extra
# `[dev]` резолвится отдельно и `--no-deps` его просто не трогает;
# проверено прогоном на синтетическом пакете с намеренно неразрешимой
# dev-зависимостью: с `--no-deps` установка проходит (код 0) независимо
# от того, разрешима ли `[dev]`, без — падает (код 1, "No matching
# distribution found"). Живой поломки сегодня нет: единственный
# потребитель `ARG` — `infra/docker-compose.yml` (`INSTALL_DEV: "true"`),
# других в репозитории не найдено, и прод-профиля (`T-M7-10`) ещё не
# существует — но когда он появится, использовать этот `Dockerfile` как
# есть означало бы прод-образ с pytest/ruff внутри. `T-M7-10` дополнен
# критерием: отдельный хэшированный замок без `--extra dev`.
ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ]; then \
      pip install --no-cache-dir -r requirements.lock --require-hashes && pip install --no-cache-dir --no-deps -e ".[dev]"; \
    else \
      pip install --no-cache-dir -r requirements.lock --require-hashes && pip install --no-cache-dir --no-deps -e .; \
    fi

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

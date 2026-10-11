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
ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ]; then \
      pip install --no-cache-dir -r requirements.lock --require-hashes && pip install --no-cache-dir --no-deps -e ".[dev]"; \
    else \
      pip install --no-cache-dir -r requirements.lock --require-hashes && pip install --no-cache-dir --no-deps -e .; \
    fi

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

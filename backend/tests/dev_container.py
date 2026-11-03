"""Что видит тест внутри dev-контейнера `backend` - ОДНО место этого факта в коде.

Причины `skipif` сторожей, читающих файлы вне `backend/` (`docs/`, `frontend/`, `scripts/`,
`Makefile`, `.github/`), подставляют `DEV_CONTAINER_MOUNTS`, а не пересказывают перечень своими
словами: правка `infra/docker-compose.yml` (volumes сервиса `backend`) тогда меняет
одну строку здесь.
Граница (правило 8): строка описывает compose-файл и сама с ним не сверяется - сверки нет, и
её нарочно не заводили; при правке volumes compose эту строку нужно обновить руками.
"""

DEV_CONTAINER_MOUNTS = (
    "infra/docker-compose.yml монтирует в dev-контейнер backend только "
    "backend/app, "
    "backend/alembic, backend/tests и backend/openapi.json; остальное (docs/, frontend/, "
    "scripts/, Makefile, .github/) оказывается там, только если скопировано вручную (docker cp)"
)

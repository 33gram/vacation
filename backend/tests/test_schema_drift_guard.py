"""T-M1-62: расхождение модели SQLAlchemy и схемы, которую создают миграции.

Тесты идут на схеме из миграций (`alembic upgrade head` перед `pytest`, и в
`make test-backend`, и в CI), а не на `Base.metadata`, поэтому правка только
модели или только миграции не валила ничего. Здесь две сверки, ни одна без
другой: `alembic check` не сравнивает CHECK-ограничения (измерено: после
снятия `'hours'` из `ck_leave_types_unit` в модели он отвечает «No new upgrade
operations detected»), а сверка CHECK не видит колонок, типов и индексов.

ГРАНИЦА (измерено, перечень в `docs/tasks.md`, `T-M1-62`): обе сверки сравнивают
модель с БАЗОЙ, а не с текстом миграций, - миграция, записанная иначе, но
дающая ту же схему, проходит. Не ловятся: `server_default` (измерено: `false` →
`true` только в модели, `alembic check` чист), триггеры (в миграциях их нет,
сверки для них нет), данные (сидирование параметров), смысл «VARCHAR + CHECK
вместо enum». Ловятся (измерено): выражение функционального индекса
(`lower(email)` → `upper(email)` только в модели краснит `alembic check`).
"""

import subprocess
import sys
from pathlib import Path

from sqlalchemy import CheckConstraint, text
from sqlalchemy.engine import Connection

from app.db.base import Base
from app.db.session import get_engine

# Нижняя граница против ослепления: измерено 25 CHECK-ограничений модели на
# момент задачи. Новая таблица число поднимает; падение ниже порога означает,
# что обнаружение перестало видеть ограничения, а не что их стало меньше.
MIN_CHECK_CONSTRAINTS = 25

_PROBE_SCHEMA = "drift_probe"

# Читает определения CHECK обеих схем одним запросом и одним и тем же текстом
# `pg_get_constraintdef`; фильтр по схеме - структурный (системные домены
# `information_schema` тоже имеют `contype = 'c'`, но на наших таблицах их нет).
_CHECKS_SQL = text(
    """
    SELECT cls.relname, con.conname, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class cls ON cls.oid = con.conrelid
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    WHERE con.contype = 'c' AND ns.nspname = :schema
    """
)


def _create_model_in_probe_schema(sync_connection: Connection) -> None:
    Base.metadata.create_all(sync_connection)


async def _read_checks(connection, schema: str) -> dict[tuple[str, str], str]:
    rows = (await connection.execute(_CHECKS_SQL, {"schema": schema})).all()
    return {(table, name): definition for table, name, definition in rows}


async def test_check_constraints_of_the_model_match_the_database() -> None:
    """Выражения не сравниваются текстом модели: `pg_get_constraintdef`
    перепечатывает их (`unit IN ('a','b')` приходит как
    `(unit)::text = ANY ((ARRAY['a'::character varying, ...])::text[])`),
    и нормализация регулярными выражениями была бы вторым хрупким местом.
    Вместо этого модель разворачивается DDL в служебной схеме внутри
    транзакции, которая откатывается, и сравниваются два текста ОДНОЙ
    функции PostgreSQL: из миграций и из модели. Сравнение точное, как по
    именам, так и по выражениям, в обе стороны."""
    async with get_engine().connect() as connection:
        try:
            await connection.execute(text(f"CREATE SCHEMA {_PROBE_SCHEMA}"))
            probe = await connection.execution_options(
                schema_translate_map={None: _PROBE_SCHEMA}
            )
            await probe.run_sync(_create_model_in_probe_schema)
            from_model = await _read_checks(connection, _PROBE_SCHEMA)
            from_migrations = await _read_checks(connection, "public")
        finally:
            await connection.rollback()

    declared = sum(
        isinstance(constraint, CheckConstraint)
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
    )
    # обнаружение видит ровно то, что объявлено в модели (безымянное или
    # пропущенное ограничение расходится со счётом), и не меньше порога
    assert len(from_model) == declared
    assert len(from_model) >= MIN_CHECK_CONSTRAINTS

    only_model = sorted(set(from_model) - set(from_migrations))
    only_database = sorted(set(from_migrations) - set(from_model))
    changed = sorted(
        key
        for key in set(from_model) & set(from_migrations)
        if from_model[key] != from_migrations[key]
    )
    assert not (only_model or only_database or changed), (
        f"CHECK модели и схемы из миграций разошлись: "
        f"только в модели {only_model}; только в базе {only_database}; "
        f"выражение отличается "
        f"{[(key, from_model[key], from_migrations[key]) for key in changed]}"
    )


def test_alembic_check_finds_no_drift_in_columns_types_and_indexes() -> None:
    """Настоящая команда `alembic check` (та же, что у разработчика руками),
    а не `compare_metadata` в процессе: так сверка идёт с настройками
    `alembic/env.py`. Подпроцесс, а не `command.check`: `env.py` вызывает
    `asyncio.run`, что внутри процесса pytest сбросило бы цикл событий,
    общий для всех async-тестов сессии (`conftest.event_loop`)."""
    backend = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(backend / "alembic.ini"), "check"],
        cwd=backend,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "No new upgrade operations detected" in output, output

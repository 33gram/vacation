"""T-M1-62: расхождение модели SQLAlchemy и схемы, которую создают миграции.

Тесты идут на схеме из миграций (`alembic upgrade head` перед `pytest`, и в
`make test-backend`, и в CI), а не на `Base.metadata`, поэтому правка только
модели или только миграции не валила ничего. Здесь три сверки: настоящая
`alembic check` (колонки, типы, индексы), CHECK-ограничения и определения
индексов модели против схемы из миграций. `alembic check` CHECK не сравнивает
(измерено: после снятия `'hours'` из `ck_leave_types_unit` в модели он отвечает
«No new upgrade operations detected»).

Приём двух последних: модель разворачивается DDL в служебной схеме
(`drift_probe`) внутри откатываемой транзакции, и сравниваются два текста ОДНОЙ
функции PostgreSQL (`pg_get_constraintdef`, `pg_get_indexdef`) - из миграций и
из модели. Это не зависит от того, что умеет сравнивать Alembic.

ГРАНИЦА (измерено, перечень в `docs/tasks.md`, `T-M1-62`): все сверки сравнивают
модель с БАЗОЙ, а не с текстом миграций - миграция, записанная иначе, но
дающая ту же схему, проходит. Не ловится: значения по умолчанию на стороне базы
(`compare_server_default` в `alembic/env.py` выключен, а при включении и в
пробной схеме расходятся 8 колонок, у которых в модели `default=` на стороне
Python, а в миграции `server_default`, - вынесено в `T-M1-63`), триггеры (в
миграциях их нет, сверки для них нет), данные (сидирование параметров), смысл
«VARCHAR + CHECK вместо enum». Слепые зоны самого `alembic check` (измерено,
`alembic/ddl/impl.py`, `_column_args_match`: параметры типа сравниваются, только
когда их число совпадает): `varchar(N)` против `String()` без длины и `COLLATE` на
одной стороне, имя ограничения внешнего ключа; снятие первичного ключа ловит только
сверка индексов. `String(N)` в моделях сегодня нет, так что это граница, а не дефект.
"""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint, text
from sqlalchemy.engine import make_url

from app.db.base import Base
from app.db.session import get_engine

# Нижние границы против ослепления: на момент задачи измерено 25 CHECK-ограничений
# и 16 индексов в таблицах модели. Новая таблица числа поднимает; падение ниже
# порога означает, что обнаружение перестало видеть объекты, а не что их стало меньше.
MIN_CHECK_CONSTRAINTS = 25
MIN_INDEXES = 16

_PROBE_SCHEMA = "drift_probe"

# Определения читаются обеих схем одним запросом и одной функцией. Системные домены
# `information_schema` тоже имеют `contype = 'c'`, но отсекает их не фильтр по схеме,
# а `JOIN pg_class ON cls.oid = con.conrelid`: у ограничения домена `conrelid = 0`.
_CHECKS_SQL = text(
    """
    SELECT cls.relname, con.conname, pg_get_constraintdef(con.oid)
    FROM pg_constraint con
    JOIN pg_class cls ON cls.oid = con.conrelid
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    WHERE con.contype = 'c' AND ns.nspname = :schema
    """
)
_INDEXES_SQL = text(
    """
    SELECT tbl.relname, idx.relname, pg_get_indexdef(idx.oid)
    FROM pg_index ind
    JOIN pg_class idx ON idx.oid = ind.indexrelid
    JOIN pg_class tbl ON tbl.oid = ind.indrelid
    JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
    WHERE ns.nspname = :schema
    """
)


async def _read(connection, sql, schema: str) -> dict[tuple[str, str], str]:
    rows = (await connection.execute(sql, {"schema": schema})).all()
    return {(table, name): definition for table, name, definition in rows}


async def _read_model_and_migrations(sql) -> tuple[dict, dict]:
    """(из модели, из миграций). `checkfirst=False`: намерение - создать
    заведомо; проверка существования у SQLAlchemy смотрит на непереведённые
    имена (`schema_translate_map` на отражение не распространяется), и
    полагаться на это поведение незачем."""
    async with get_engine().connect() as connection:
        try:
            await connection.execute(text(f"CREATE SCHEMA {_PROBE_SCHEMA}"))
            probe = await connection.execution_options(
                schema_translate_map={None: _PROBE_SCHEMA}
            )
            await probe.run_sync(lambda sync: Base.metadata.create_all(sync, checkfirst=False))
            from_model = await _read(connection, sql, _PROBE_SCHEMA)
            from_migrations = await _read(connection, sql, "public")
        finally:
            await connection.rollback()
    return from_model, from_migrations


def _differences(from_model: dict, from_migrations: dict) -> str | None:
    only_model = sorted(set(from_model) - set(from_migrations))
    only_database = sorted(set(from_migrations) - set(from_model))
    changed = [
        (key, from_model[key], from_migrations[key])
        for key in sorted(set(from_model) & set(from_migrations))
        if from_model[key] != from_migrations[key]
    ]
    if not (only_model or only_database or changed):
        return None
    return (
        f"только в модели {only_model}; только в базе {only_database}; "
        f"определение отличается {changed}"
    )


async def test_check_constraints_of_the_model_match_the_database() -> None:
    """Выражения не сравниваются текстом модели: `pg_get_constraintdef`
    перепечатывает их (`unit IN ('a','b')` приходит как
    `(unit)::text = ANY ((ARRAY['a'::character varying, ...])::text[])`),
    и нормализация регулярными выражениями была бы вторым хрупким местом.
    Сравнение точное, по именам и по выражениям, в обе стороны."""
    from_model, from_migrations = await _read_model_and_migrations(_CHECKS_SQL)

    declared = sum(
        isinstance(constraint, CheckConstraint)
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
    )
    # Счёт ловит провал обнаружения (запрос перестал видеть ограничения модели).
    # Безымянное `CheckConstraint` его НЕ краснит - PostgreSQL даст ему имя сам,
    # запись попадёт в оба счёта; такое ограничение краснит разность имён ниже.
    assert len(from_model) == declared
    assert len(from_model) >= MIN_CHECK_CONSTRAINTS

    differences = _differences(from_model, from_migrations)
    assert differences is None, (
        f"CHECK модели и схемы из миграций разошлись: {differences}. Если расхождение "
        "только в форме перечня, текст мог быть перепарсен `ALTER … TYPE`."
    )


async def test_indexes_of_the_model_match_the_database() -> None:
    """Определения индексов, включая функциональные (`lower((email)::text)`
    в базе), сравниваются так же, как CHECK. Определение содержит имя схемы
    (`ON public.employees` против `ON drift_probe.employees`); подставляется
    ровно оно, и подстановка ничего другого не прячет: имя схемы стоит только
    после `ON`, а имена таблиц, колонок и выражения от неё не зависят.
    Таблица `alembic_version` не из модели (её ведёт сам Alembic), поэтому
    индекс из базы берётся только для таблиц `Base.metadata`.

    Второй тест нужен и сверх `alembic check`: `PostgresqlImpl.compare_indexes`
    сравнивает выражения, уникальность и `nulls_not_distinct`, а `WHERE`
    (частичный индекс) и `INCLUDE` не сравнивает - их ловит только этот тест."""
    from_model, from_migrations = await _read_model_and_migrations(_INDEXES_SQL)
    from_model = {
        key: definition.replace(f"{_PROBE_SCHEMA}.", "public.")
        for key, definition in from_model.items()
    }
    from_migrations = {
        key: definition
        for key, definition in from_migrations.items()
        if key[0] in Base.metadata.tables
    }

    # Как у CHECK: найденное равно объявленному (первичные ключи + UniqueConstraint
    # + Index), иначе обнаружение, ослепшее на часть индексов, прошло бы под полом.
    declared = sum(
        (1 if table.primary_key.columns else 0)
        + sum(isinstance(constraint, UniqueConstraint) for constraint in table.constraints)
        + len(table.indexes)
        for table in Base.metadata.tables.values()
    )
    assert len(from_model) == declared
    assert len(from_model) >= MIN_INDEXES
    differences = _differences(from_model, from_migrations)
    assert differences is None, f"индексы модели и схемы из миграций разошлись: {differences}"


def test_alembic_check_finds_no_drift_in_columns_types_and_indexes() -> None:
    """Настоящая команда `alembic check` (та же, что у разработчика руками),
    а не `compare_metadata` в процессе: так сверка идёт с настройками
    `alembic/env.py`. Подпроцесс, а не `command.check`: `env.py` вызывает
    `asyncio.run`, что внутри процесса pytest сбросило бы цикл событий,
    общий для всех async-тестов сессии (`conftest.event_loop`)."""
    backend = Path(__file__).resolve().parents[1]
    # База подпроцесса - та же, с которой работает тест, а не выбранная им самим:
    # измерено ревью, что при `.env` вне `backend` (cwd подпроцесса) он уходил в
    # dev-базу, а тест давал 3 passed; охранник `conftest` смотрит только на базу теста.
    database_url = get_engine().url.render_as_string(hide_password=False)
    assert (make_url(database_url).database or "").endswith("_test")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(backend / "alembic.ini"), "check"],
        cwd=backend,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "No new upgrade operations detected" in output, output

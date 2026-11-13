"""business_rules_config: third value type `timezone` and the key business_timezone (T-M2-34)

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-12

ЧТО ДОБАВЛЯЕТ. Бизнес-день («сегодня» Системы) задаёт ЧАСОВОЙ ПОЯС ОРГАНИЗАЦИИ
(`NFR-LOC-002`, `BR-12`, `requirements.md` v1.30, раздел 13 п. 36): параметр
`business_timezone`, по умолчанию `Europe/Moscow`. Значение - СТРОКА с именем зоны IANA,
а таблица знала два типа (`integer`, `month_day`), поэтому ревизия добавляет ТРЕТИЙ тип
`timezone` - единственный случай, требующий миграции (докстринг `0007`, модель):
  - CHECK `ck_business_rules_config_value_type` пересоздаётся со списком из трёх типов;
  - новый CHECK `ck_business_rules_config_timezone_format` проверяет ФОРМУ строки
    (ASCII, сегменты через `/`; `Europe/Moscow`, `UTC`, `Etc/GMT+3`). Годность имени
    регулярным выражением не проверяется (`Europe/Moskva` по форме верно): база не знает,
    какие зоны существуют, это проверяет сервис через `zoneinfo`. Кто что проверяет:
    база - форму и тип, сервис (`parse_stored_value`) - ту же форму И существование зоны;
    сервис строже базы, не мягче.
Отклонённая альтернатива (Ф-1) - держать пояс вне `business_rules_config` (переменная
окружения): правило 5 (значения раздела 5 живут в таблице и настраиваются через
интерфейс администратора, `FR-ADMIN-004`) тогда не применялось бы к значению, которое
меняет расчёт отпуска; пояс - решение организации, а не развёртывания.

СИДИРОВАНИЕ ЗДЕСЬ, В МИГРАЦИИ, `ON CONFLICT (key) DO NOTHING` - тем же способом и по той же
причине, что в `0007`/`0009`: умолчание живёт в сидировании, а не запасным значением в коде
(читатель при отсутствии ключа отказывает). Вставка вынесена в `seed_business_timezone`,
чтобы тест вызвал её дважды. Миграция не импортирует код приложения.

УЖЕ ЗАПИСАННЫЕ СОБЫТИЯ не переписываются и не пересчитываются: журнал добавляемый. Смена
пояса меняет, КАКОЙ ДЕНЬ считается сегодняшним, но не даты событий, уже записанных датой.

`downgrade` удаляет строки типа `timezone` (в том числе с правкой администратора: без этой ревизии
читателя у неё нет), затем возвращает прежние CHECK. Значения остальных ключей не затронуты.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMEZONE_FORM = r"^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$"

_BUSINESS_RULES = sa.table(
    "business_rules_config",
    sa.column("key", sa.String),
    sa.column("value", sa.String),
    sa.column("value_type", sa.String),
    sa.column("description", sa.String),
)

_PRESET: list[dict[str, str]] = [
    {
        # ВЫБРАНО (решение заказчика, `NFR-LOC-002`): `NFR-LOC-001` называет Europe/Moscow
        # поясом по умолчанию.
        "key": "business_timezone",
        "value": "Europe/Moscow",
        "value_type": "timezone",
        "description": (
            "NFR-LOC-002, BR-12: часовой пояс организации (имя зоны IANA, например "
            "Europe/Moscow), по которому определяется «сегодня» Системы: начисление "
            "отпуска, остаток на дату по умолчанию, граница даты корректировки, "
            "граница выбора даты. Переход на летнее время считается по правилам зоны; "
            "уже записанные события при смене пояса не пересчитываются."
        ),
    },
]


def seed_business_timezone(connection: sa.Connection) -> None:
    """Вставляет параметр пояса, не трогая существующий ключ (правка администратора
    не затирается). Вынесена из `upgrade` - чтобы тест вызывал ту же вставку повторно."""
    statement = postgresql.insert(_BUSINESS_RULES).on_conflict_do_nothing(index_elements=["key"])
    connection.execute(statement, _PRESET)


def upgrade() -> None:
    op.drop_constraint("ck_business_rules_config_value_type", "business_rules_config")
    op.create_check_constraint(
        "ck_business_rules_config_value_type",
        "business_rules_config",
        "value_type IN ('integer', 'month_day', 'timezone')",
    )
    op.create_check_constraint(
        "ck_business_rules_config_timezone_format",
        "business_rules_config",
        f"value_type <> 'timezone' OR value ~ '{_TIMEZONE_FORM}'",
    )
    seed_business_timezone(op.get_bind())


def downgrade() -> None:
    # По ТИПУ, а не по ключу: прежний CHECK не допускает ни одной строки типа `timezone`.
    op.execute(sa.delete(_BUSINESS_RULES).where(_BUSINESS_RULES.c.value_type == "timezone"))
    op.drop_constraint("ck_business_rules_config_timezone_format", "business_rules_config")
    op.drop_constraint("ck_business_rules_config_value_type", "business_rules_config")
    op.create_check_constraint(
        "ck_business_rules_config_value_type",
        "business_rules_config",
        "value_type IN ('integer', 'month_day')",
    )

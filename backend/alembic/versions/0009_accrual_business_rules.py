"""seed the accrual parameters of FR-BAL-001 into business_rules_config (T-M2-05)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-22

ЧТО ДОБАВЛЯЕТ. Два параметра расчёта накопления отпуска (`FR-BAL-001`,
`requirements.md` v1.24, раздел 13 п. 30). Таблицу создала `0007`; здесь
только строки — добавление параметра схемы не меняет (`plan.md` §4).
`0007` сознательно отложила ключ про отпуск без содержания «до T-M2-05»
(её докстринг, перечень «не заведено сознательно»); это он и есть, плюс
порог округления, которого в тот момент в требованиях ещё не было.

ЗНАЧЕНИЯ И ПРОИСХОЖДЕНИЕ (пометки как в `0007`):
  - `month_rounding_threshold_days` = 15, `integer` — ВЫБРАНО (решение
    заказчика, `FR-BAL-001/1`). Остаток неполного месяца в столько
    календарных дней и более округляется до полного месяца. Источник не
    называет числа: п. 35 Правил об очередных и дополнительных отпусках
    № 169 говорит о «половине месяца»; 15 — выбор, а не вывод из нормы.
  - `unpaid_leave_days_counted_in_service` = 14, `integer` — ВЫВЕДЕНО из
    ст. 121 ТК РФ: в стаж включается время отпусков без сохранения
    заработной платы по просьбе работника, «не превышающее 14 календарных
    дней в течение рабочего года» (`FR-BAL-001/2`).

НОРМА ОТПУСКА и СРОК ПЕРВОГО ОТПУСКА берутся из существующих ключей
`annual_leave_days` (`BR-1`) и `first_leave_eligibility_months` (`BR-2`);
второй нормы по категориям не заводится: удлинённый и дополнительный
отпуск по категориям выводятся за границы MVP (`A-3.1`, раздел «вне
границ»).

СИДИРОВАНИЕ ЗДЕСЬ, В МИГРАЦИИ, `ON CONFLICT (key) DO NOTHING` — тем же
способом и по той же причине, что в `0007`. Вставка вынесена в
`seed_accrual_rules`, чтобы тест вызвал её дважды (идемпотентность
оператора; повторный `alembic upgrade` её не проверяет — ревизия
применяется один раз). Миграция не импортирует код приложения; реестр
диапазонов — `app/modules/business_rules/service.py`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BUSINESS_RULES = sa.table(
    "business_rules_config",
    sa.column("key", sa.String),
    sa.column("value", sa.String),
    sa.column("value_type", sa.String),
    sa.column("description", sa.String),
)

_PRESET: list[dict[str, str]] = [
    {
        # ВЫБРАНО (решение заказчика): п. 35 Правил № 169 даёт «половину
        # месяца» без числа.
        "key": "month_rounding_threshold_days",
        "value": "15",
        "value_type": "integer",
        "description": (
            "FR-BAL-001/1: остаток неполного рабочего месяца в столько календарных дней "
            "и более округляется до полного месяца при расчёте накопления, меньше — "
            "отбрасывается; значение выбрано заказчиком (п. 35 Правил № 169 говорит о "
            "«половине месяца» без числа)."
        ),
    },
    {
        # ВЫВЕДЕНО: ст. 121 ТК РФ, «не превышающее 14 календарных дней в
        # течение рабочего года».
        "key": "unpaid_leave_days_counted_in_service",
        "value": "14",
        "value_type": "integer",
        "description": (
            "FR-BAL-001/2: сколько календарных дней отпусков без сохранения заработной "
            "платы за рабочий год включается в стаж, дающий право на отпуск; превышение "
            "исключается из стажа и сдвигает конец рабочего года (ст. 121 ТК РФ)."
        ),
    },
]


def seed_accrual_rules(connection: sa.Connection) -> None:
    """Вставляет параметры расчёта накопления, не трогая существующие
    ключи (правка администратора не затирается). Вынесена из `upgrade` —
    чтобы тест вызывал ровно ту же вставку повторно."""
    statement = postgresql.insert(_BUSINESS_RULES).on_conflict_do_nothing(index_elements=["key"])
    connection.execute(statement, _PRESET)


def upgrade() -> None:
    seed_accrual_rules(op.get_bind())


def downgrade() -> None:
    # Удаляются строки обоих ключей, в том числе с правкой администратора:
    # без этой ревизии читателя у них нет.
    op.execute(
        sa.delete(_BUSINESS_RULES).where(
            _BUSINESS_RULES.c.key.in_([row["key"] for row in _PRESET])
        )
    )

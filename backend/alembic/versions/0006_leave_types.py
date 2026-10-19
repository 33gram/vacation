"""create leave_types table and seed the preset set (T-M2-01, T-M2-04)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19

СИДИРОВАНИЕ ЗДЕСЬ, В МИГРАЦИИ, А НЕ ПРИ СТАРТЕ ПРИЛОЖЕНИЯ (T-M2-04) —
выбор, а не умолчание. `FR-ADMIN-001` разрешает администратору править
виды, а не откатывать их: миграция исполняется ровно один раз на базу
(Alembic помнит ревизию), поэтому повторный запуск `upgrade head` не
может ни продублировать записи, ни перетереть правку. Сидирование при
старте (как `auth/bootstrap.py`) пришлось бы защищать от этого отдельно
и оно воскрешало бы вид, который администратор сознательно удалил.
Плата: набор растёт только новой миграцией, а `downgrade` вместе с
таблицей теряет и правки администратора — как и любой `downgrade`
таблицы с данными. Вставка при этом всё равно `ON CONFLICT (code) DO
NOTHING`: миграция не должна падать и не должна затирать запись, если
вид с таким кодом уже заведён вручную до неё.

КОДЫ — часть контракта (на них будут ссылаться `leave_requests`, тесты и
фронтенд): латиница, snake_case, имя по смыслу вида, а не по номеру в
списке раздела 6 (порядок списка может измениться). После заведения код
не меняется — меняется `name`.

АТРИБУТЫ. «Раздел 6» ниже — это только перечень вопросов (оплата,
единица, ...), значений он не задаёт, кроме названных явно. Каждое
значение помечено: ВЫВЕДЕНО (следует из текста требований, ссылка) или
ВЫБРАНО (требования молчат, значение — решение этой задачи, администратор
может его изменить).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEAVE_TYPES = sa.table(
    "leave_types",
    sa.column("code", sa.String),
    sa.column("name", sa.String),
    sa.column("is_paid", sa.String),
    sa.column("unit", sa.String),
    sa.column("reduces_main_balance", sa.Boolean),
    sa.column("requires_document", sa.Boolean),
    sa.column("requires_approval", sa.Boolean),
    sa.column("annual_limit", sa.Numeric),
    sa.column("carry_over_policy", sa.String),
    sa.column("exclude_weekends_holidays", sa.Boolean),
    sa.column("is_active", sa.Boolean),
)

# Общее для всех видов:
#  - annual_limit = NULL: нормы закона (28 дней, BR-1) — настраиваемые
#    параметры в `business_rules_config` (T-M2-03), а не колонка вида
#    (правило 5); лимитов других видов требования не называют.
#  - requires_approval = True: ВЫВЕДЕНО из FR-REQ-001 (заявка получает
#    статус «На согласовании» и идёт по маршруту), FR-DAYOFF-002 (то же
#    для командировки), FR-SICK-003 (больничный «подтверждён HR»);
#    для остальных видов — ВЫБРАНО по аналогии (заявки одного движка).
#  - carry_over_policy = 'none' везде, кроме основного отпуска: ВЫБРАНО,
#    поведение переноса не определено ни одним FR и не читается (T-M3-30).
#  - exclude_weekends_holidays = False у видов в календарных днях: ВЫБРАНО.
#    Ограничение булевой колонки: для основного отпуска ст. 120 ТК РФ
#    исключает праздники, но НЕ выходные, — один флаг этого не выражает;
#    расчёт количества дней (T-M3-05, FR-REQ-001 «где применимо») решает
#    это своей логикой, колонка здесь значения не навязывает.
_PRESET: list[dict[str, object]] = [
    {
        # BR-1, FR-REQ-001: основной оплачиваемый отпуск, календарные дни —
        # ВЫВЕДЕНО. reduces_main_balance=True — ВЫВЕДЕНО (это и есть основной
        # отпуск). carry_over='unlimited' — ВЫБРАНО: неиспользованный остаток
        # основного отпуска не сгорает на конец года (BR-6: срок 12 месяцев,
        # предупреждение FR-REQ-004, не обнуление).
        "code": "annual_paid",
        "name": "Основной оплачиваемый отпуск",
        "is_paid": "full",
        "unit": "calendar_days",
        "reduces_main_balance": True,
        "requires_document": False,
        "requires_approval": True,
        "annual_limit": None,
        "carry_over_policy": "unlimited",
        "exclude_weekends_holidays": False,
        "is_active": True,
    },
    {
        # A-3.1: в справочнике, но ВЫКЛЮЧЕН по умолчанию — ВЫВЕДЕНО.
        # reduces_main_balance=False — ВЫБРАНО: дополнительный отпуск —
        # отдельное право, не часть основных 28 дней.
        "code": "additional_paid",
        "name": "Дополнительный оплачиваемый отпуск",
        "is_paid": "full",
        "unit": "calendar_days",
        "reduces_main_balance": False,
        "requires_document": False,
        "requires_approval": True,
        "annual_limit": None,
        "carry_over_policy": "none",
        "exclude_weekends_holidays": False,
        "is_active": False,
    },
    {
        # BR-9: без сохранения зарплаты, по заявлению — ВЫВЕДЕНО
        # (is_paid='none'). Документ не требуется — ВЫБРАНО (BR-9 говорит
        # «по заявлению/основанию», но реквизитов документа не вводит).
        "code": "unpaid",
        "name": "Отпуск без сохранения заработной платы",
        "is_paid": "none",
        "unit": "calendar_days",
        "reduces_main_balance": False,
        "requires_document": False,
        "requires_approval": True,
        "annual_limit": None,
        "carry_over_policy": "none",
        "exclude_weekends_holidays": False,
        "is_active": True,
    },
    {
        # ВЫБРАНО целиком: требования не говорят ничего, кроме названия.
        # is_paid='full' — случай ст. 173 ТК РФ (сохраняется средний
        # заработок); ст. 174 (без сохранения) этим видом не выражается.
        # requires_document=True — учебный отпуск оформляется по справке-
        # вызову (ст. 177 ТК РФ).
        "code": "study",
        "name": "Учебный отпуск",
        "is_paid": "full",
        "unit": "calendar_days",
        "reduces_main_balance": False,
        "requires_document": True,
        "requires_approval": True,
        "annual_limit": None,
        "carry_over_policy": "none",
        "exclude_weekends_holidays": False,
        "is_active": True,
    },
    {
        # is_paid='partial' — ВЫБРАНО: оплата идёт пособием (не зарплатой
        # работодателя), и для ухода за ребёнком только в части срока.
        # requires_document=True — ВЫБРАНО по аналогии с больничным: по
        # беременности и родам оформляется листок нетрудоспособности (BR-10).
        "code": "maternity_childcare",
        "name": "Отпуск по беременности и родам / по уходу за ребёнком",
        "is_paid": "partial",
        "unit": "calendar_days",
        "reduces_main_balance": False,
        "requires_document": True,
        "requires_approval": True,
        "annual_limit": None,
        "carry_over_policy": "none",
        "exclude_weekends_holidays": False,
        "is_active": True,
    },
    {
        # FR-SICK-001: не уменьшает остаток основного отпуска, нужен номер
        # ЭЛН и даты — ВЫВЕДЕНО (reduces_main_balance, requires_document);
        # FR-REQ-005 называет больничный примером вида с документом.
        # is_paid='partial' — ВЫБРАНО: больничное — пособие (часть за счёт
        # работодателя, часть СФР, BR-10), не полная зарплата.
        "code": "sick",
        "name": "Больничный (временная нетрудоспособность)",
        "is_paid": "partial",
        "unit": "calendar_days",
        "reduces_main_balance": False,
        "requires_document": True,
        "requires_approval": True,
        "annual_limit": None,
        "carry_over_policy": "none",
        "exclude_weekends_holidays": False,
        "is_active": True,
    },
    {
        # unit='working_days' — ВЫБРАНО: FR-DAYOFF-001 ведёт компенсацию в
        # днях (стандартный график) ИЛИ в часах (сменный) — единица зависит
        # от сотрудника, а колонка `unit` — от вида; этот разрыв заведён
        # задачей T-M3-31. Раз единица — рабочие дни, выходные/праздники
        # внутри периода в количество не входят (exclude=True) — ВЫВЕДЕНО из
        # выбора единицы. is_paid='full' — ВЫБРАНО: отгул — день отдыха
        # с сохранением оплаты (определение отгула, раздел 2).
        "code": "time_off",
        "name": "Отгул",
        "is_paid": "full",
        "unit": "working_days",
        "reduces_main_balance": False,
        "requires_document": False,
        "requires_approval": True,
        "annual_limit": None,
        "carry_over_policy": "none",
        "exclude_weekends_holidays": True,
        "is_active": True,
    },
    {
        # Раздел 6, п. 8: «не расходует остаток отпуска, но блокирует
        # сотрудника в календаре» — ВЫВЕДЕНО (reduces_main_balance=False);
        # FR-DAYOFF-002: даты, место, цель вместо документа — ВЫВЕДЕНО
        # (requires_document=False). is_paid='full' — ВЫБРАНО (средний
        # заработок за время командировки, ст. 167 ТК РФ).
        "code": "business_trip",
        "name": "Командировка",
        "is_paid": "full",
        "unit": "calendar_days",
        "reduces_main_balance": False,
        "requires_document": False,
        "requires_approval": True,
        "annual_limit": None,
        "carry_over_policy": "none",
        "exclude_weekends_holidays": False,
        "is_active": True,
    },
]


def seed_preset(connection: sa.Connection) -> None:
    """Вставляет предустановленный набор, не трогая уже существующие коды.
    Вынесена из `upgrade` — чтобы тест вызывал ровно ту же вставку
    повторно (backend/tests/modules/leave_types/test_seed.py)."""
    statement = postgresql.insert(_LEAVE_TYPES).on_conflict_do_nothing(index_elements=["code"])
    connection.execute(statement, _PRESET)


def upgrade() -> None:
    op.create_table(
        "leave_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False, unique=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_paid", sa.String(), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("reduces_main_balance", sa.Boolean(), nullable=False),
        sa.Column("requires_document", sa.Boolean(), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("annual_limit", sa.Numeric(7, 2), nullable=True),
        # Набор значений сознательно минимальный (решение владельца
        # продукта): поведение переноса не определено ни одним FR и никем не
        # читается (T-M3-30). Случай «не более N дней» потребует ОТДЕЛЬНОЙ
        # колонки с лимитом переноса и миграции, а не нового значения здесь.
        sa.Column("carry_over_policy", sa.String(), nullable=False),
        sa.Column("exclude_weekends_holidays", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "is_paid IN ('full', 'none', 'partial')",
            name="ck_leave_types_is_paid",
        ),
        sa.CheckConstraint(
            "unit IN ('calendar_days', 'working_days', 'hours')",
            name="ck_leave_types_unit",
        ),
        sa.CheckConstraint(
            "carry_over_policy IN ('none', 'unlimited')",
            name="ck_leave_types_carry_over_policy",
        ),
        sa.CheckConstraint(
            "annual_limit IS NULL OR annual_limit >= 0",
            name="ck_leave_types_annual_limit_non_negative",
        ),
    )
    seed_preset(op.get_bind())


def downgrade() -> None:
    op.drop_table("leave_types")

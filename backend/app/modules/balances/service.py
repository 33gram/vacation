"""Остаток отпуска: начисление, ручная корректировка и чтение (T-M2-05,
T-M2-08, `FR-BAL-001`, `FR-BAL-002`, `FR-BAL-004`).

Остаток — СУММА `quantity` событий журнала `leave_balance_entries`
(`plan.md` §4); агрегирующей таблицы нет.

ЕДИНСТВЕННОЕ МЕСТО ВЫЧИСЛЕНИЯ ОСТАТКА — `get_balance` ниже. Начисление
(`T-M2-05`), перерасчёт (`T-M2-07`), эндпоинты (`T-M2-11`), отчёты
(`T-M6-03`) и списание по заявке (`T-M3-34`) получают остаток только
через неё и свои события пишут только через функции этого модуля, а не
собственной суммой по таблице и не прямой вставкой. Это свойство проверяет
`tests/modules/balances/test_balance_journal_guard.py` (на таблицу
ссылается только этот модуль и модель); границы проверки названы там.

ЖУРНАЛ ДОБАВЛЯЕМЫЙ: в модуле нет путей `UPDATE`/`DELETE`; исправление —
новое событие. Запрет на уровне прав БД — `NFR-AUDIT-001` (M6), здесь его
нет.

ТРАНЗАКЦИЯ. Функции записи только `flush`-ят, `commit` делает вызывающий:
начальные остатки при импорте (`T-M1-36`) пишутся вместе с карточкой
сотрудника одной транзакцией, а не по одному коммиту на событие.

НАЧИСЛЕНИЕ (T-M2-05, `T-M2-21`). `accrue_leave` пишет по событию `accrual` на
КАЖДЫЙ ещё не записанный шаг накопления рабочего года (`accrual.accrual_steps`),
датированному днём шага. Количество шага — РАЗНОСТЬ между тем, сколько должно
быть начислено к шагу (нарастающий итог шагов), и тем, что уже начислено за этот
рабочий год (сумма событий `accrual` и `recalculation`). Шаг, у которого
разность не больше нуля, НЕ пишется (`CHECK` требует `accrual` > 0, а
уменьшение накопленного — это `recalculation`, `T-M2-07`). Поэтому повторный
вызов на ту же дату ничего не пишет, а вызов на более позднюю — только новые шаги.
Сумма уже начисленного — это не остаток (остаток включает списания и корректировки)
и ему не конкурент: единственное место вычисления остатка по-прежнему `get_balance`.

ГРАНИЦА `FR-BAL-004` (правило 8). Критерий требует причину И запись в
журнале аудита. Причину закрывает журнал событий (она обязательна на
уровне БД и сервиса); `audit_log` появится в M6 (`T-M6-02`), и вторая
половина критерия этим модулем НЕ закрыта.
"""

from __future__ import annotations

import datetime
import zoneinfo
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.leave_balance_entry import (
    ACCRUAL,
    INITIAL_IMPORT,
    MANUAL_ADJUSTMENT,
    RECALCULATION,
    LeaveBalanceEntry,
)
from app.models.leave_type import LeaveType
from app.modules.balances.accrual import (
    MONTHS_IN_YEAR,
    accrual_steps,
    compute_accrual,
    is_working_year_start,
    working_year_containing,
    working_year_start_on,
    working_year_starts,
)
from app.modules.business_rules.business_day import business_date_of
from app.modules.business_rules.service import (
    ANNUAL_LEAVE_DAYS,
    MONTH_ROUNDING_THRESHOLD_DAYS,
    get_ints,
)

# Начисление работает с ОСНОВНЫМ оплачиваемым отпуском: норма `annual_leave_days`
# (`BR-1`) - его норма. Когда норма станет зависеть от вида (удлинённый и
# дополнительный отпуск - за границами MVP, `A-3.1`), менять надо это место и
# выбор нормы в `accrue_leave`.
MAIN_LEAVE_TYPE_CODE = "annual_paid"
# Предел горизонта прогноза (`FR-BAL-002/2`, `T-M2-33`): дальше пяти лет от бизнес-дня запрос
# отклоняется. Технический предел, а не норма раздела 5: защищает от абсурдной даты (запрос
# на сотни лет вперёд), а не от стоимости - она определяется стажем сотрудника, не горизонтом.
FORECAST_HORIZON_YEARS = 5

# Типы событий, которые `accrue_leave` считает "уже начисленным" (T-M2-07,
# решения ВЫВЕДЕНЫ):
#   - `accrual` - само начисление;
#   - `recalculation` - пересчёт правит именно начисленное право; если не считать
#     его, следующее начисление записало бы ту же разность второй раз;
#   - `initial_import` НЕ входит: он несёт начальный ОСТАТОК, в котором уже учтено
#     прошлое использование, и считать его начисленным значило бы заглушить
#     законное начисление; вместо этого год с начальным остатком (и все годы до
#     него) для начисления ЗАКРЫТ целиком - см. `AccrualClosedError`.
# Расширять набор - осознанно: каждый тип сюда ломает или чинит двойной счёт.
ALREADY_ACCRUED_ENTRY_TYPES = (ACCRUAL, RECALCULATION)


class BalanceEntryError(Exception):
    """Событие нельзя записать: причина, автор, количество или период
    не годятся. Сообщение — для вызывающего (эндпоинт `T-M2-11` переводит
    его в ответ 4xx)."""


class BalanceConflictError(BalanceEntryError):
    """Операция конфликтует с УЖЕ ЗАПИСАННЫМИ событиями журнала (например, дату приёма
    нельзя сменить из-за событий исчезающего года). Отдельный класс, чтобы эндпоинт
    ответил 409, а не 400 (правило кодов отказа, `CLAUDE.md`)."""


class LeaveTypeNotSeededError(RuntimeError):
    """Основной вид отпуска (`MAIN_LEAVE_TYPE_CODE`) отсутствует в справочнике:
    сломана КОНФИГУРАЦИЯ, а не запрос. Намеренно не `BalanceEntryError`: эндпоинт
    переводит тот в 400 («запрос неверен»), а это должно быть отказом сервера."""


@dataclass(frozen=True)
class AccrualAttribution:
    """Автор и причина события, которое начисление порождает не само по себе,
    а по поручению другой операции (пересчёт, `T-M2-07`, `FR-BAL-003`). Без неё
    начисление системное: автора нет, причина стандартная."""

    actor_account_id: int
    reason_prefix: str


class AccrualClosedError(BalanceEntryError):
    """Начисление по рабочему году закрыто начальным остатком: в этом или
    более позднем году есть событие `initial_import`. Ничего не записано.

    ПРЕДПОЛОЖЕНИЕ (не факт): начальный остаток уже включает прошлое
    использование (прибавлять к нему вычисленное право - двойной счёт) и
    покрывает все годы ДО года импорта. Что означает начальный остаток, решает
    `T-M1-36`, и она ещё не решила. ИЗВЕСТНАЯ ДЫРА этого предположения: год
    импорта закрыт ЦЕЛИКОМ, поэтому его часть после даты импорта не
    начисляется никогда (следующий год стартует с нуля); кандидаты на
    решение - в тексте `T-M1-36`."""


async def get_balance(
    session: AsyncSession,
    employee_id: int,
    leave_type_id: int,
    working_year_start: datetime.date,
    *,
    as_of: datetime.date | None = None,
) -> Decimal:
    """Остаток сотрудника по виду за рабочий год, начинающийся в
    `working_year_start`, — сумма `quantity` событий (0, если событий нет).

    `as_of` (`BR-8`, остаток на любую дату) — сумма событий с `entry_date` не
    позже этой даты. ГРАНИЦА: функция отражает ЗАПИСАННЫЕ события и не
    придумывает начисление, которого в журнале нет; остаток на дату в будущем
    без начисления сам не растёт. Кому нужна свежая цифра на дату, сначала
    вызывает `accrue_leave` на эту дату."""
    query = select(func.coalesce(func.sum(LeaveBalanceEntry.quantity), 0)).where(
        LeaveBalanceEntry.employee_id == employee_id,
        LeaveBalanceEntry.leave_type_id == leave_type_id,
        LeaveBalanceEntry.working_year_start == working_year_start,
    )
    if as_of is not None:
        query = query.where(LeaveBalanceEntry.entry_date <= as_of)
    result = await session.execute(query)
    return Decimal(result.scalar_one())


async def get_balances_by_working_year(
    session: AsyncSession,
    employee_id: int,
    leave_type_id: int,
    *,
    as_of: datetime.date | None = None,
) -> dict[datetime.date, Decimal]:
    """Остаток по каждому рабочему году, в котором есть события (начало года
    -> остаток). Каждое число даёт `get_balance`, своей суммы здесь нет."""
    query = (
        select(LeaveBalanceEntry.working_year_start)
        .where(
            LeaveBalanceEntry.employee_id == employee_id,
            LeaveBalanceEntry.leave_type_id == leave_type_id,
        )
        .distinct()
        .order_by(LeaveBalanceEntry.working_year_start)
    )
    if as_of is not None:
        query = query.where(LeaveBalanceEntry.entry_date <= as_of)
    starts = list((await session.execute(query)).scalars())
    return {
        start: await get_balance(session, employee_id, leave_type_id, start, as_of=as_of)
        for start in starts
    }


@dataclass(frozen=True)
class AccrualYearSummary:
    """Начисления рабочего года, свёрнутые (`T-M2-30`)."""

    working_year_start: datetime.date
    working_year_end: datetime.date
    accrued: Decimal
    last_accrual_date: datetime.date


async def get_accrual_years(
    session: AsyncSession,
    employee_id: int,
    leave_type_id: int,
    *,
    as_of: datetime.date | None = None,
) -> list[AccrualYearSummary]:
    """Начисления по рабочим годам (`T-M2-30`): сумма событий `accrual` года, конец года
    (самый поздний, как в `recalculate_balance`: исключаемые дни сдвигают его) и дата
    последнего начисления; по возрастанию начала года. Отсечение `as_of` и суммирование
    `quantity` - те же, что у `get_balance`; `recalculation` в сумму НЕ входит (он остаётся
    отдельной строкой истории), поэтому число - «сколько начислено», а не остаток. Год без
    событий `accrual` не возвращается."""
    query = (
        select(
            LeaveBalanceEntry.working_year_start,
            func.max(LeaveBalanceEntry.working_year_end),
            func.sum(LeaveBalanceEntry.quantity),
            func.max(LeaveBalanceEntry.entry_date),
        )
        .where(
            LeaveBalanceEntry.employee_id == employee_id,
            LeaveBalanceEntry.leave_type_id == leave_type_id,
            LeaveBalanceEntry.entry_type == ACCRUAL,
        )
        .group_by(LeaveBalanceEntry.working_year_start)
        .order_by(LeaveBalanceEntry.working_year_start)
    )
    if as_of is not None:
        query = query.where(LeaveBalanceEntry.entry_date <= as_of)
    rows = (await session.execute(query)).all()
    return [
        AccrualYearSummary(
            working_year_start=start,
            working_year_end=end,
            accrued=Decimal(total),
            last_accrual_date=last,
        )
        for start, end, total, last in rows
    ]


async def get_total_balance(
    session: AsyncSession,
    employee_id: int,
    leave_type_id: int,
    *,
    as_of: datetime.date | None = None,
) -> Decimal:
    """Остаток за ВСЕ рабочие годы (`BR-8`, ст. 127 ТК РФ: компенсация за все
    неиспользованные отпуска) — сумма остатков по годам, как они даны
    `get_balance`. Год с отрицательным остатком (аванс) уменьшает сумму; если
    нужна иная политика, она принадлежит вызывающему (`T-M1-37`), а не
    этой функции."""
    by_year = await get_balances_by_working_year(session, employee_id, leave_type_id, as_of=as_of)
    return sum(by_year.values(), Decimal(0))


async def list_entries(
    session: AsyncSession,
    employee_id: int,
    leave_type_id: int | None = None,
    *,
    as_of: datetime.date | None = None,
    include_accruals: bool = True,
) -> list[LeaveBalanceEntry]:
    """История событий сотрудника (`FR-BAL-002`): по дате события, при
    равных датах — в порядке записи. У события есть тип, количество со
    знаком, причина и автор (`actor_account_id`; имя автора по аккаунту
    подставляет слой эндпоинта). `as_of` - только события с `entry_date` не позже
    этой даты (то же отсечение, что у `get_balance(as_of=)`, делается в SQL).
    `include_accruals=False` (`T-M2-30`) - без событий `accrual`: их показывает свёртка
    по годам `get_accrual_years`."""
    query = select(LeaveBalanceEntry).where(LeaveBalanceEntry.employee_id == employee_id)
    if leave_type_id is not None:
        query = query.where(LeaveBalanceEntry.leave_type_id == leave_type_id)
    if as_of is not None:
        query = query.where(LeaveBalanceEntry.entry_date <= as_of)
    if not include_accruals:
        query = query.where(LeaveBalanceEntry.entry_type != ACCRUAL)
    result = await session.execute(
        query.order_by(LeaveBalanceEntry.entry_date, LeaveBalanceEntry.id)
    )
    return list(result.scalars())


async def list_leave_type_ids_with_events(session: AsyncSession, employee_id: int) -> set[int]:
    """Виды отпуска, по которым у сотрудника есть хотя бы одно событие журнала
    (для набора видов в ответе `GET .../balances`, `T-M2-11`)."""
    result = await session.execute(
        select(LeaveBalanceEntry.leave_type_id)
        .where(LeaveBalanceEntry.employee_id == employee_id)
        .distinct()
    )
    return set(result.scalars())


async def leave_type_has_events(session: AsyncSession, leave_type_id: int) -> bool:
    """Есть ли по виду отпуска хотя бы одно событие журнала у любого сотрудника
    (справочник `T-M2-10`: признак «ведётся остаток» нельзя снять у такого вида).
    Проверка моментальная: событие, записанное одновременно с правкой справочника,
    в неё может не попасть."""
    return bool(
        await session.scalar(
            select(exists().where(LeaveBalanceEntry.leave_type_id == leave_type_id))
        )
    )


async def adjust_balance(
    session: AsyncSession,
    *,
    employee_id: int,
    leave_type_id: int,
    working_year_start: datetime.date,
    working_year_end: datetime.date,
    quantity: Decimal,
    reason: str,
    actor_account_id: int | None,
    entry_date: datetime.date,
) -> Decimal:
    """Ручная корректировка остатка (`FR-BAL-004`): записывает ОДНО событие
    `manual_adjustment`, прежних не трогает, возвращает новый остаток.

    Причина обязательна, пустая строка и одни пробелы причиной не являются;
    записывается обрезанной. Автор обязателен. `quantity` — со знаком
    (минус уменьшает остаток), ноль — не корректировка."""
    stripped_reason = reason.strip()
    if not stripped_reason:
        raise BalanceEntryError("Укажите причину корректировки.")
    # Корректировка по АКТИВНОМУ неосновному виду разрешена (осознанное действие HR,
    # не начисление); по неактивному - отказ. ВЫБРАНО.
    leave_type_active = await session.scalar(
        select(LeaveType.is_active).where(LeaveType.id == leave_type_id)
    )
    if leave_type_active is None:
        raise BalanceEntryError("Вид отпуска не найден.")
    if not leave_type_active:
        raise BalanceEntryError("Вид отпуска неактивен: корректировка по нему невозможна.")
    if actor_account_id is None:
        raise BalanceEntryError("Не указан автор корректировки.")
    if quantity == 0:
        raise BalanceEntryError("Корректировка на ноль ничего не меняет.")
    if working_year_end <= working_year_start:
        raise BalanceEntryError("Конец рабочего года должен быть позже его начала.")
    session.add(
        LeaveBalanceEntry(
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            working_year_start=working_year_start,
            working_year_end=working_year_end,
            entry_type=MANUAL_ADJUSTMENT,
            quantity=quantity,
            entry_date=entry_date,
            reason=stripped_reason,
            actor_account_id=actor_account_id,
        )
    )
    await session.flush()
    return await get_balance(session, employee_id, leave_type_id, working_year_start)


async def _lock_employee(session: AsyncSession, employee_id: int) -> Employee | None:
    """Строка сотрудника ПОД ЗАМКОМ и ПЕРЕЧИТАННАЯ (`T-M2-11`, замечание ревью).

    `session.get(..., with_for_update=True)` для уже загруженного объекта (слой API
    читает сотрудника раньше) шлёт `SELECT ... FOR UPDATE`, но атрибуты объекта НЕ
    обновляет: расчёт шёл бы по устаревшей `hire_date`, если параллельная
    транзакция её сменила (измерено на sqlalchemy 2.0.54, закреплено тестом).
    Поэтому `populate_existing=True`: одно обращение, перечитывает объект и
    возвращает `None`, если строки нет (`refresh` в этом случае бросил бы
    исключение). Перед этим `flush`: `populate_existing` затёр бы несохранённые
    правки объекта, а пересчёт при смене даты приёма приходит с ещё не
    сброшенной в БД новой датой. Единственный замок модуля; порядка замков нет."""
    await session.flush()
    return await session.get(
        Employee, employee_id, with_for_update=True, populate_existing=True
    )


async def _require_main_leave_type(session: AsyncSession, leave_type_id: int) -> None:
    """Вид существует и он основной. Проверяется ДО блокировки строки
    сотрудника: неверный вызов не должен держать её до конца транзакции
    вызывающего."""
    leave_type_code = await session.scalar(
        select(LeaveType.code).where(LeaveType.id == leave_type_id)
    )
    if leave_type_code is None:
        raise BalanceEntryError("Вид отпуска не найден.")
    if leave_type_code != MAIN_LEAVE_TYPE_CODE:
        raise BalanceEntryError(
            "Начисление выполняется только по основному оплачиваемому отпуску."
        )


async def _latest_import_year(
    session: AsyncSession, employee_id: int, leave_type_id: int
) -> datetime.date | None:
    """Начало самого позднего рабочего года с событием `initial_import`."""
    return await session.scalar(
        select(func.max(LeaveBalanceEntry.working_year_start)).where(
            LeaveBalanceEntry.employee_id == employee_id,
            LeaveBalanceEntry.leave_type_id == leave_type_id,
            LeaveBalanceEntry.entry_type == INITIAL_IMPORT,
        )
    )


def _closed_by_import_message(year_start: datetime.date, import_year: datetime.date) -> str:
    return (
        f"Начисление за рабочий год с {year_start:%d.%m.%Y} закрыто: "
        f"начальный остаток занесён за рабочий год с {import_year:%d.%m.%Y}."
    )


async def accrue_leave(
    session: AsyncSession,
    *,
    employee_id: int,
    leave_type_id: int,
    as_of: datetime.date,
    excluded_days: int = 0,
    working_year_start: datetime.date | None = None,
    attribution: AccrualAttribution | None = None,
    write: bool = True,
) -> Decimal:
    """Начисляет отпуск за рабочий год на дату `as_of` (`FR-BAL-001`):
    пишет по ОДНОМУ событию `accrual` на каждый ещё не записанный шаг накопления
    (`accrual_steps`, `T-M2-21`: до двенадцати на рабочий год), датированному днём шага -
    днём, когда право возникло, а не днём вызова, - и возвращает записанное количество
    (сумму шагов); ноль — если писать нечего (событий нет, исключение не
    поднимается). Шаг пишется, когда его нарастающий итог больше уже начисленного, на
    разность: сумма записанного та же, что у одного события на `as_of`.

    `write=False` (`T-M2-33`, прогноз): считает ТО ЖЕ самое - те же шаги, ту же разность, те же
    отказы - и возвращает, сколько было бы записано, но в журнал НЕ пишет и `flush` не делает;
    замок строки сотрудника берётся как обычно. Формулы ШАГОВ одни: прогноз идёт тем же путём, а
    не вторым расчётом, и по каждому году называет то, что записал бы настоящий догон. СБОРКА
    суммы по видам и годам (`forecast_by_year` -> `forecast_accrual`, `balance`,
    `total_balance`) - в слое API (`router.py`), своя, и покрыта тестами эндпоинта
    (`test_balance_forecast.py`), а не этим путём. Начисление только у основного вида
    обеспечивает `_require_main_leave_type` в этой же функции: проверка вида в роутере - выбор,
    у каких видов показывать прогноз, а не второе решение о том, кому начислять.

    Работает только с основным видом (`MAIN_LEAVE_TYPE_CODE`): норма
    `annual_leave_days` - норма основного отпуска, и начислять её на другой
    вид нельзя; иной вид - `BalanceEntryError`, событий нет.

    «Уже начислено» - сумма событий типов `ALREADY_ACCRUED_ENTRY_TYPES`
    (`accrual` и `recalculation`), см. оговорку у константы. По году, который
    закрыт начальным остатком (`initial_import` в этом или более позднем году),
    начисление не выполняется: `AccrualClosedError`, событий нет.

    Норма и порог округления — из `business_rules_config`
    (`annual_leave_days`, `month_rounding_threshold_days`), дата приёма — из
    карточки сотрудника. `excluded_days` и `working_year_start` — как в
    `accrual.compute_accrual` (отпуска без содержания — данные модуля заявок,
    M3; здесь число). Автор события — `None`: начисляет система; если начисление
    порождено пересчётом (`FR-BAL-003` требует автора и причину), вызывающий
    передаёт `attribution`: событие остаётся `accrual`, но несёт автора пересчёта
    и его причину перед стандартным текстом. Тип не меняется: это заработанное
    право, его уже считает `ALREADY_ACCRUED_ENTRY_TYPES`; модель автора у
    `accrual` допускает (`ACTOR_REQUIRED_ENTRY_TYPES` его не содержит, `CHECK`,
    запрещающего автора, нет).

    Транзакцией владеет вызывающий: только `flush`. Строка сотрудника
    блокируется (`FOR UPDATE`) до конца транзакции, чтобы две параллельные
    транзакции не записали одну и ту же разность дважды; это закрыто
    рассуждением, а не проверено тестом на гонку — проверены
    последовательные повторные вызовы. Замок ОДИН (строка сотрудника); порядка
    замков нет, порядок обращений (вид отпуска читается обычным `select`, затем
    блокируется строка) на корректность не влияет. Две границы замка: (1) он
    защищает от ДРУГИХ `accrue_leave`, `accrue_all_working_years` и
    `recalculate_balance`, но не от `adjust_balance`, который его не берёт;
    сегодня этого достаточно, потому что корректировка не входит в «уже
    начислено»; (2) рассуждение опирается на уровень изоляции READ COMMITTED
    (после ожидания замка читается уже зафиксированное другой транзакцией); при
    более строгом уровне повторное чтение вело бы себя
    иначе."""
    if attribution is not None and not attribution.reason_prefix.strip():
        raise BalanceEntryError("Для начисления по пересчёту нужна причина.")
    await _require_main_leave_type(session, leave_type_id)
    employee = await _lock_employee(session, employee_id)
    if employee is None:
        raise BalanceEntryError("Сотрудник не найден.")
    rules = await get_ints(session, ANNUAL_LEAVE_DAYS, MONTH_ROUNDING_THRESHOLD_DAYS)
    try:
        accrual = compute_accrual(
            hire_date=employee.hire_date,
            as_of=as_of,
            annual_norm=rules[ANNUAL_LEAVE_DAYS],
            rounding_threshold_days=rules[MONTH_ROUNDING_THRESHOLD_DAYS],
            excluded_days=excluded_days,
            working_year_start=working_year_start,
        )
    except ValueError as error:
        raise BalanceEntryError(str(error)) from error

    closing_import_year = await _latest_import_year(session, employee_id, leave_type_id)
    if closing_import_year is not None and closing_import_year >= accrual.working_year_start:
        raise AccrualClosedError(
            _closed_by_import_message(accrual.working_year_start, closing_import_year)
        )

    already = await session.execute(
        select(func.coalesce(func.sum(LeaveBalanceEntry.quantity), 0)).where(
            LeaveBalanceEntry.employee_id == employee_id,
            LeaveBalanceEntry.leave_type_id == leave_type_id,
            LeaveBalanceEntry.working_year_start == accrual.working_year_start,
            LeaveBalanceEntry.entry_type.in_(ALREADY_ACCRUED_ENTRY_TYPES),
        )
    )
    # По событию на шаг (T-M2-21): каждый шаг, чьё накопленное больше уже записанного,
    # пишет разность и датируется днём шага, а не днём вызова; шаги, покрытые прежними
    # событиями, пропускаются. Идемпотентность - тот же принцип «положено минус уже
    # начислено», применённый к нарастающему итогу шагов.
    written_total = Decimal(already.scalar_one())
    steps = accrual_steps(
        hire_date=employee.hire_date,
        working_year_start=accrual.working_year_start,
        as_of=as_of,
        annual_norm=rules[ANNUAL_LEAVE_DAYS],
        rounding_threshold_days=rules[MONTH_ROUNDING_THRESHOLD_DAYS],
        excluded_days=excluded_days,
    )
    prefix = f"{attribution.reason_prefix.strip()}: " if attribution else ""
    difference = Decimal(0)
    for step in steps:
        if step.accrued <= written_total:
            continue
        quantity = step.accrued - written_total
        if write:
            session.add(
                LeaveBalanceEntry(
                    employee_id=employee_id,
                    leave_type_id=leave_type_id,
                    working_year_start=accrual.working_year_start,
                    working_year_end=accrual.working_year_end,
                    entry_type=ACCRUAL,
                    quantity=quantity,
                    entry_date=step.date,
                    reason=(
                        prefix + f"Начисление за {step.counted_months}-й месяц рабочего года "
                        f"({step.date.strftime('%d.%m.%Y')}): "
                        f"{step.counted_months} из {MONTHS_IN_YEAR} месяцев"
                    ),
                    actor_account_id=attribution.actor_account_id if attribution else None,
                )
            )
        written_total = step.accrued
        difference += quantity
    if difference > 0 and write:
        await session.flush()
    return difference


@dataclass(frozen=True)
class CatchUpResult:
    accrued: dict[datetime.date, Decimal]  # начало года -> записанное (только > 0)
    skipped: dict[datetime.date, str]  # начало года -> почему не начислялось


async def accrue_all_working_years(
    session: AsyncSession,
    *,
    employee_id: int,
    leave_type_id: int,
    as_of: datetime.date,
    attribution: AccrualAttribution | None = None,
    zone: zoneinfo.ZoneInfo | None = None,
    write: bool = True,
) -> CatchUpResult:
    """Догоняет начисление по ВСЕМ рабочим годам от даты приёма до `as_of`,
    вызывая `accrue_leave` по каждому (своей арифметики и своей записи в журнал
    нет). Завершённые годы начисляются на их последний день (значит, полной
    нормой), текущий - на `as_of`. Повторный вызов ничего не пишет.

    Годы, которые НЕ начисляются, пропускаются и названы в `skipped` с причиной
    (это не ошибка читающего кода): (1) закрытые начальным остатком
    (`AccrualClosedError`); (2) БАРЬЕР против выдуманного остатка (решение
    заказчика, `T-M2-19`): годы, ЗАКОНЧИВШИЕСЯ до появления карточки сотрудника в
    Системе (`employees.created_at`; дата - ДЕНЬ ОРГАНИЗАЦИИ по параметру пояса,
    `business_date_of`, `T-M2-40`). Смысл: Система ведёт остаток с
    момента, как узнала о сотруднике, прошлое приходит начальным остатком
    (`initial_import`, `T-M1-36`), а не вычисляется. Граница: год, закончившийся
    ПОСЛЕ появления карточки (в том числе карточка заведена внутри первого
    рабочего года, через неделю после приёма), начисляется целиком; год
    заканчивается в последний день, и карточка, появившаяся в этот день, год
    сохраняет. Если год попадает под оба правила, причина называет оба.
    Остаётся ДЫРА (один текущий год): текущий год сотрудника без импорта
    начисляется с начала года, хотя до появления карточки история использования
    Системе неизвестна. `excluded_days` = 0 для всех лет - оговорка до появления
    учёта отпусков без содержания (M3, `T-M3-37`). Сотрудник принят позже
    `as_of` - пусто. Уволенный: догон идёт не дальше `termination_date` (включительно),
    уволенный без даты - `BalanceEntryError`. `attribution` пробрасывается в `accrue_leave`. Замок -
    тот же единственный, что в `accrue_leave`. `write=False` (`T-M2-33`) пробрасывается туда же:
    `accrued` называет, сколько БЫЛО БЫ записано по каждому году, журнал не меняется."""
    await _require_main_leave_type(session, leave_type_id)
    employee = await _lock_employee(session, employee_id)
    if employee is None:
        raise BalanceEntryError("Сотрудник не найден.")
    if employee.termination_date is None and employee.employment_status == "terminated":
        # Инвариант карточки (`deactivate_employee` требует дату увольнения): громкий
        # отказ вместо догадки, до какой даты начислять.
        raise BalanceEntryError(
            "У уволенного сотрудника не указана дата увольнения: начислять не до какой даты."
        )
    if employee.termination_date is not None:
        # Начисление не идёт дальше даты увольнения (последний день работы -
        # включительно, ст. 84.1 ТК РФ): право за время после увольнения не
        # возникает, а `T-M1-37` фиксирует остаток именно на эту дату.
        as_of = min(as_of, employee.termination_date)
    # `zone` - пояс, уже прочитанный вызывающим (один запрос параметра на операцию, PR #198);
    # без него `business_date_of` читает параметр сам.
    card_date = await business_date_of(session, employee.created_at, zone=zone)
    import_year = await _latest_import_year(session, employee_id, leave_type_id)
    starts = working_year_starts(employee.hire_date, as_of)
    accrued: dict[datetime.date, Decimal] = {}
    skipped: dict[datetime.date, str] = {}
    for index, start in enumerate(starts):
        is_last = index == len(starts) - 1
        next_start = working_year_start_on(employee.hire_date, index + 1)
        year_as_of = as_of if is_last else next_start - datetime.timedelta(days=1)
        reasons: list[str] = []
        if next_start <= card_date:
            reasons.append(
                f"Рабочий год {start:%d.%m.%Y}-{next_start - datetime.timedelta(days=1):%d.%m.%Y} "
                f"закончился до появления карточки сотрудника в Системе ({card_date:%d.%m.%Y}): "
                "прошлое приходит начальным остатком (initial_import, T-M1-36), а не вычисляется."
            )
        if import_year is not None and import_year >= start:
            reasons.append(_closed_by_import_message(start, import_year))
        if reasons:
            skipped[start] = " ".join(reasons)
            continue
        try:
            written = await accrue_leave(
                session,
                employee_id=employee_id,
                leave_type_id=leave_type_id,
                as_of=year_as_of,
                working_year_start=start,
                attribution=attribution,
                write=write,
            )
        except AccrualClosedError as error:  # защитно: между проверкой и записью
            skipped[start] = str(error)
            continue
        if written > 0:
            accrued[start] = written
    return CatchUpResult(accrued=accrued, skipped=skipped)


@dataclass(frozen=True)
class RecalculationResult:
    closed_years: dict[datetime.date, Decimal]  # начало года -> записанная разность (< 0)
    catch_up: CatchUpResult


def _nothing_recalculated() -> RecalculationResult:
    return RecalculationResult(closed_years={}, catch_up=CatchUpResult({}, {}))


async def recalculate_balance(
    session: AsyncSession,
    *,
    employee_id: int,
    actor_account_id: int | None,
    as_of: datetime.date,
    changes: Mapping[str, tuple[object, object]],
    zone: zoneinfo.ZoneInfo | None = None,
) -> RecalculationResult:
    """Пересчёт остатка при смене даты приёма (`FR-BAL-003`, `T-M2-07`).

    Рабочий год - ФУНКЦИЯ даты приёма (годовщины), поэтому смена даты - это
    переатрибуция (ВЫБРАНО: оставить прежние границы значило бы навсегда
    оставить сотруднику неверный отпускной год). Журнал добавляемый, поэтому:
      1. по каждому рабочему году, которого при новой дате больше нет, пишется по
         `recalculation` на КАЖДЫЙ прежний шаг начисления: та же дата, та же
         величина со знаком минус (`T-M2-32`); прежние строки не правятся;
      2. затем `accrue_all_working_years` начисляет по новым годам.
    Причина события формируется системой и называет прежнюю и новую дату.

    ГРОМКИЙ ОТКАЗ (`BalanceEntryError`, ничего не записано): в исчезающем году
    есть событие, внесённое человеком против конкретного года
    (`manual_adjustment`, `initial_import`) или иное, кроме `accrual`/
    `recalculation` - переатрибуция перестаёт быть арифметикой, угадывать нельзя.
    Дата приёма исправляется после разбора этих событий. Событий `usage` пока нет
    (модуль заявок - M3); как относить их к годам при смене даты, не решено
    (`T-M3-36`).

    Сотрудник без событий основного вида - пусто: начислять по нему будет
    ленивое начисление при чтении (`T-M2-19`). Дата приёма не менялась - пусто.
    `changes` - изменённые поля карточки: имя -> (было, стало). Пересчёт идёт
    только по `hire_date`; любое другое имя (например, `leave_category`) принимается,
    но событий не пишет: норма от него сегодня не зависит (`A-3.1`), и это
    намеренно, а не обработано.
    Годы, оставшиеся прежними, пересчёту не подвергаются (начало года то же, норма та
    же; границы месяцев внутри могут отличаться на день при 28/29 февраля, разницы в
    начислении на проверенных датах нет); уменьшение в сохранившемся году эта
    функция не производит.

    `BR-8` (`T-M2-32`). Закрытие датировано днями ПРЕЖНИХ шагов, поэтому остаток
    исчезнувшего года равен нулю на каждую дату, а сумма по всем годам на любую дату
    равна расчёту по новой дате приёма (перебор каждого дня,
    `test_recalculation_close_dates.py`: сдвиги +1 день, +10 дней, +1 месяц, +3 месяца,
    -6 месяцев, целое число лет, уволенный, повторный пересчёт, возврат даты). ЦЕНА:
    остаток прежнего года на ПРОШЛУЮ дату теперь нулевой - журнал «как было видно
    тогда» для сумм по датам не сохраняется. Закрытий столько же, сколько прежних
    шагов (до двенадцати на год).
    ИДЕМПОТЕНТНОСТЬ: год, у которого сумма `ALREADY_ACCRUED_ENTRY_TYPES` равна нулю,
    не закрывается; закрыто то, что ещё не закрыто (нетто по дате шага, не больше
    начисленного итога года), поэтому пересчёт после возврата даты верен. ЗАКРЫВАЕТСЯ
    только то, что считается начисленным (`accrual`, `recalculation`); остальное в
    исчезающем году - отказ выше (`manual_adjustment`, `initial_import`, `usage`).
    УВОЛЕННЫЙ: даты закрытия - даты прежних шагов, а они не позже `termination_date`
    (начисление стоит на ней), поэтому позже увольнения закрытий нет.
    ОСТАЁТСЯ (ИЗМЕРЕНО, закреплено `test_known_boundary_a_year_closed_in_one_piece...` в
    `test_recalculation_close_dates.py`; первым это показало ревью PR #194): год,
    закрытый ДО `T-M2-32` одним куском на день
    пересчёта, не переписывается (журнал добавляемый), и пересчёт его не трогает: сумма
    года равна нулю, закрывать нечего. Сценарий «старое закрытие -> возврат даты ->
    повторный сдвиг» (приём 10.03.2026, начислено на 24.10.2026, пересчёт в тот же день,
    новая дата 20.03.2026): после возврата год начисляется заново поверх старых шагов,
    остаток года ненулевой на 215 датах (до 32,66, больше нормы); после повторного
    сдвига остаток исчезнувшего года ненулевой (4,67, 9,33, 13,99) на 92 датах, сумма по
    годам расходится с расчётом на 92 днях (с 24.07.2026). Числа ревью (4,67 на 184
    датах, 214 дней) этим сценарием не воспроизведены. 214 - число из отметки `T-M2-21`
    (24.03-23.10), то есть обычный сценарий до `T-M2-32`, а не старое закрытие. 184 как
    число дат в документах не встречается (только номер PR #184) и остаётся необъяснённым;
    ДОГАДКА: это 92 + 92, сложенные ненулевые даты и расхождение суммы. Закреплены
    измеренные числа.
    РЕШЕНИЕ: отдельной задачи нет - эксплуатации нет (развёртывание M7), такие годы есть
    только в базах разработки, журнал добавляемый; та же развилка, что решена в `T-M2-21`.

    Автор обязателен; `recalculation` пишется только функцией
    `_write_recalculation` с проверкой причины (`strip()`) и автора. Проверка
    автора стоит и здесь, и там НАМЕРЕННО: здесь она отказывает до блокировки и
    чтения журнала, там - последний рубеж для любого другого вызывающего
    `_write_recalculation`; второй слой не лишний. Закрывается
    именно НАЧИСЛЕННОЕ (сумма событий `ALREADY_ACCRUED_ENTRY_TYPES`), а не остаток
    `get_balance`: остаток включал бы списания и корректировки, которых в
    исчезающем году быть не должно (иначе отказ выше), и закрытие по остатку
    стёрло бы чужие события. Транзакцией владеет вызывающий: только `flush`.
    Замок - единственный, строка сотрудника, как в `accrue_leave`
    (`accrue_all_working_years` берёт её повторно в той же транзакции).

    ГОНКА ПО `old_hire_date` (`T-M2-07`) закрыта вызывающим: `update_employee` берёт
    строку сотрудника под замок (`refresh(..., with_for_update=True)`) ПОСЛЕ
    цепочки подчинения и ДО чтения «было», поэтому две параллельные правки даты
    приёма сериализуются и вторая пересчитывает от зафиксированной первой; порядок
    «цепочка -> строка» тот же, что у `deactivate_employee`, `import_employees` и
    `update_department`. Что НЕ доказано: отсутствие взаимной блокировки - вывод из
    чтения кода и правил блокировок PostgreSQL, не воспроизведённый прогоном; сама
    сериализация проверена тестом двух параллельных сессий
    (`tests/test_concurrent_org_changes.py`). Эта функция сама замок на входе не
    берёт до чтения карточки: прямой вызывающий без замка получит устаревшее
    «было» в причине. Границы месяцев в сохранившемся году (28/29 февраля) могут
    отличаться на день; разницы в начислении на проверенных датах нет."""
    if "hire_date" not in changes:
        # Других полей, влияющих на норму, нет: ни замка, ни чтения журнала.
        return _nothing_recalculated()
    if actor_account_id is None:
        raise BalanceEntryError("Не указан автор пересчёта.")
    leave_type_id = await session.scalar(
        select(LeaveType.id).where(LeaveType.code == MAIN_LEAVE_TYPE_CODE)
    )
    if leave_type_id is None:
        raise LeaveTypeNotSeededError(
            f"В справочнике нет основного вида отпуска {MAIN_LEAVE_TYPE_CODE!r}."
        )
    employee = await _lock_employee(session, employee_id)
    if employee is None:
        raise BalanceEntryError("Сотрудник не найден.")
    old_hire_date, new_hire_date = changes["hire_date"]
    if not isinstance(old_hire_date, datetime.date) or not isinstance(new_hire_date, datetime.date):
        raise BalanceEntryError("Дата приёма в изменениях должна быть датой.")
    if employee.hire_date != new_hire_date:
        raise BalanceEntryError("Дата приёма в карточке не совпадает с новой датой пересчёта.")
    if old_hire_date == new_hire_date:
        return _nothing_recalculated()

    events = list(
        (
            await session.execute(
                select(LeaveBalanceEntry)
                .where(
                    LeaveBalanceEntry.employee_id == employee_id,
                    LeaveBalanceEntry.leave_type_id == leave_type_id,
                )
                .order_by(LeaveBalanceEntry.entry_date, LeaveBalanceEntry.id)
            )
        ).scalars()
    )
    if not events:
        return _nothing_recalculated()

    vanishing = sorted(
        {
            event.working_year_start
            for event in events
            if not is_working_year_start(new_hire_date, event.working_year_start)
        }
    )
    blocking = [
        event
        for event in events
        if event.working_year_start in vanishing
        # Совпадение с набором намеренное: тип, считающийся начисленным, - ровно
        # тот, с которым умеет работать закрывающая арифметика ниже, поэтому
        # наборы обязаны совпадать.
        and event.entry_type not in ALREADY_ACCRUED_ENTRY_TYPES
    ]
    if blocking:
        listed = "; ".join(
            f"{event.entry_type} {event.quantity} от {event.entry_date:%d.%m.%Y} "
            f"(рабочий год с {event.working_year_start:%d.%m.%Y})"
            for event in blocking
        )
        raise BalanceConflictError(
            "Дату приёма нельзя изменить автоматически: в рабочих годах, которых при новой "
            f"дате не существует, есть события, внесённые против конкретного года: {listed}. "
            "Разберитесь с ними и повторите."
        )

    closed: dict[datetime.date, Decimal] = {}
    for start in vanishing:
        year_events = [event for event in events if event.working_year_start == start]
        accrued = sum(
            (e.quantity for e in year_events if e.entry_type in ALREADY_ACCRUED_ENTRY_TYPES),
            Decimal(0),
        )
        if accrued == 0:
            # Идемпотентность и возврат даты: закрытый год (начисления и их зеркала
            # дают ноль) второй раз не закрывается.
            continue
        # События одного года могут нести разный конец (сдвиг исключаемыми
        # днями, FR-BAL-001/2): берём самый поздний - итоговую границу года.
        end = max(event.working_year_end for event in year_events)
        # ЗЕРКАЛО НА КАЖДЫЙ ШАГ (T-M2-32): закрытие датировано днём прежнего шага и равно
        # ему со знаком минус, поэтому остаток исчезнувшего года равен нулю на каждую
        # дату, а не только с дня пересчёта. Дата берётся из журнала, а не считается
        # заново. Нетто по дате (начисления плюс уже записанные закрытия) делает пересчёт
        # после возврата даты верным: закрыты только ещё не закрытые шаги.
        net_by_date: dict[datetime.date, Decimal] = {}
        for event in year_events:
            if event.entry_type in ALREADY_ACCRUED_ENTRY_TYPES:
                net_by_date[event.entry_date] = (
                    net_by_date.get(event.entry_date, Decimal(0)) + event.quantity
                )
        remaining = accrued
        for step_date in sorted(net_by_date):
            quantity = min(net_by_date[step_date], remaining)
            if quantity <= 0:
                continue
            await _write_recalculation(
                session,
                employee_id=employee_id,
                leave_type_id=leave_type_id,
                working_year_start=start,
                working_year_end=end,
                quantity=-quantity,
                entry_date=step_date,
                reason=(
                    f"Пересчёт при изменении даты приёма с {old_hire_date:%d.%m.%Y} на "
                    f"{new_hire_date:%d.%m.%Y}: закрыт рабочий год {start:%d.%m.%Y}-"
                    f"{end - datetime.timedelta(days=1):%d.%m.%Y} (шаг {step_date:%d.%m.%Y}: "
                    f"{quantity} дн.)"
                ),
                actor_account_id=actor_account_id,
            )
            remaining -= quantity
        closed[start] = -accrued
    # Начисление по новым годам несёт автора пересчёта и его причину:
    # FR-BAL-003 требует их у КАЖДОЙ записи, порождённой пересчётом, в том числе
    # когда ни один год не исчез (сдвиг на целое число лет) и закрывающих строк нет.
    catch_up = await accrue_all_working_years(
        session,
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        as_of=as_of,
        zone=zone,
        attribution=AccrualAttribution(
            actor_account_id=actor_account_id,
            reason_prefix=(
                f"Пересчёт при изменении даты приёма с {old_hire_date:%d.%m.%Y} "
                f"на {new_hire_date:%d.%m.%Y}"
            ),
        ),
    )
    return RecalculationResult(closed_years=closed, catch_up=catch_up)


async def _write_recalculation(
    session: AsyncSession,
    *,
    employee_id: int,
    leave_type_id: int,
    working_year_start: datetime.date,
    working_year_end: datetime.date,
    quantity: Decimal,
    entry_date: datetime.date,
    reason: str,
    actor_account_id: int | None,
) -> None:
    """Единственное место записи `recalculation`: причина (`strip()`: пустая
    строка и пробелы причиной не являются) и автор обязательны ещё в сервисе, а
    не только `CHECK` базы; разность ненулевая."""
    stripped_reason = reason.strip()
    if not stripped_reason:
        raise BalanceEntryError("Не указана причина пересчёта.")
    if actor_account_id is None:
        raise BalanceEntryError("Не указан автор пересчёта.")
    if quantity == 0:
        raise BalanceEntryError("Пересчёт на ноль ничего не меняет.")
    session.add(
        LeaveBalanceEntry(
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            working_year_start=working_year_start,
            working_year_end=working_year_end,
            entry_type=RECALCULATION,
            quantity=quantity,
            entry_date=entry_date,
            reason=stripped_reason,
            actor_account_id=actor_account_id,
        )
    )
    await session.flush()


async def accrue_before_read(
    session: AsyncSession,
    *,
    employee_id: int,
    as_of: datetime.date,
    zone: zoneinfo.ZoneInfo | None = None,
    write: bool = True,
) -> CatchUpResult:
    """Ленивое начисление при чтении (`T-M2-19`, решение заказчика): догоняет
    начисление по основному виду на дату `as_of` через `accrue_all_working_years`
    (своего цикла по годам и своей арифметики нет); остаток после этого читает
    вызывающий через `get_balance`. Звать ПЕРЕД каждым чтением, по которому
    принимается решение, а не только перед показом.

    ЧТЕНИЕ СТАНОВИТСЯ ПИШУЩЕЙ ОПЕРАЦИЕЙ. Эндпоинт идёт в транзакции и сам делает
    `commit`; одновременные чтения по одному сотруднику сериализуются на замке
    строки сотрудника (`FOR UPDATE` в `accrue_all_working_years`). Пишущий `GET`
    выглядит странно, но безопасен и чинить его не надо: повторный вызов на ту же
    дату ничего не пишет (свойство `accrue_leave`, проверено тестом), поэтому
    повтор запроса безопасен.

    Годы, закрытые импортом или барьером, - не ошибка: они приходят в
    `skipped`. Отказ самого начисления (не закрытый год: `BalanceEntryError`,
    `LeaveTypeNotSeededError`) НЕ перехватывается - устаревшее число молча не
    отдаётся. `excluded_days` = 0 - оговорка до периодов без содержания (M3). `write=False` - расчёт
    без записи (`forecast_accrual`)."""
    leave_type_id = await session.scalar(
        select(LeaveType.id).where(LeaveType.code == MAIN_LEAVE_TYPE_CODE)
    )
    if leave_type_id is None:
        raise LeaveTypeNotSeededError(
            f"В справочнике нет основного вида отпуска {MAIN_LEAVE_TYPE_CODE!r}."
        )
    return await accrue_all_working_years(
        session,
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        as_of=as_of,
        zone=zone,
        write=write,
    )


async def forecast_accrual(
    session: AsyncSession,
    *,
    employee_id: int,
    as_of: datetime.date,
    zone: zoneinfo.ZoneInfo | None = None,
) -> CatchUpResult:
    """ПРОГНОЗ (`T-M2-33`, `FR-BAL-002/2`): сколько начисления по основному виду сверх уже
    записанного добавилось бы к дате `as_of` позже бизнес-дня, по каждому рабочему году
    (`CatchUpResult.accrued`: начало года -> прогнозное количество, только больше нуля). Это
    ЛЕНИВОЕ НАЧИСЛЕНИЕ БЕЗ ЗАПИСИ (`accrue_before_read(write=False)`): те же шаги, барьер
    появления карточки, закрытие начальным остатком, остановка на дате увольнения,
    `excluded_days` = 0 (`T-M3-37`). Прогноз остатка = записанное + это; журнал не меняется.

    ГРАНИЦЫ: учитывает ТОЛЬКО начисление (заявок и графика в Системе нет, M3-M4; подпись на
    экране говорит это прямо); перенос остатка между годами не участвует (`carry_over_policy`,
    `T-M3-30`); вызывать для даты позже бизнес-дня, для прошлой даты записанное не меняется и
    прогнозом не называется. Дату дальше предела горизонта (`FORECAST_HORIZON_YEARS`) отсекает
    вызывающий: функция сама её не проверяет. Стоимость определяется СТАЖЕМ сотрудника (числом
    рабочих лет от приёма), а не расстоянием до даты (измерено ревью PR #201: стаж 26 лет, +1 день -
    318 операторов, +5 лет - 343); прогноз вдвое дороже обычного чтения (два прохода по годам).
    Замок строки сотрудника берётся, как у записи."""
    return await accrue_before_read(
        session, employee_id=employee_id, as_of=as_of, zone=zone, write=False
    )


async def get_working_year(
    session: AsyncSession, employee_id: int, on_date: datetime.date
) -> tuple[datetime.date, datetime.date]:
    """Рабочий год сотрудника, в который попадает `on_date`: (начало, конец
    исключительно). ЕДИНСТВЕННОЕ место, которое отвечает на этот вопрос для слоя
    API (эндпоинт корректировки не вычисляет границы сам, клиент их не присылает).
    Исключаемые дни не учитываются (`excluded_days` = 0, оговорка `T-M2-19`).
    Сотрудника нет или дата раньше приёма - `BalanceEntryError`."""
    hire_date = await session.scalar(select(Employee.hire_date).where(Employee.id == employee_id))
    if hire_date is None:
        raise BalanceEntryError("Сотрудник не найден.")
    try:
        return working_year_containing(hire_date, on_date)
    except ValueError as error:
        raise BalanceEntryError(
            f"Дата {on_date:%d.%m.%Y} раньше даты приёма сотрудника ({hire_date:%d.%m.%Y})."
        ) from error

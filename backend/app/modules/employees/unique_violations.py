"""Единая классификация нарушений уникальности `employees.personnel_
number`/`ix_employees_email_lower` по имени ограничения из диагностики
asyncpg — общая для всех путей записи в `employees`/`accounts`
(`employee_service.create_employee`/`update_employee`, импорт,
`import_service.py`), а не по одной копии на каждый.

Источник истины — ограничение БД, а не опережающая проверка: под
конкурентной нагрузкой TOCTOU-окно между проверкой и записью остаётся
всегда, поэтому каждому месту, которое пишет в эти таблицы, СЛЕДУЕТ
самому ловить `IntegrityError` на `COMMIT`/`flush` и переводить её этим
модулем — так клиент получает лучшее сообщение (с указанием конкретного
поля и значения из тела ЕГО запроса).

Но это правило поведения, а не гарантия: этот модуль ничего не
перехватывает сам и не может заставить будущий путь записи вспомнить о
себе — именно так `import_service` забыл обработать исчерпание
повторов (PR #139 review, коммит 1). Гарантию, что забытый путь всё
равно не отдаст 500 с персональными данными в трассировке, даёт
обработчик `IntegrityError` в `main.py` — сетка уровня приложения, а не
этот модуль.

Поле определяется по ИМЕНИ ОГРАНИЧЕНИЯ, а не разбором текста ошибки —
имя не зависит от локали/версии сервера, текст сообщения зависит.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

UNIQUE_CONSTRAINT_FIELDS: dict[str, str] = {
    "employees_personnel_number_key": "personnel_number",
    "ix_employees_email_lower": "email",
}


def unique_violation_field(exc: IntegrityError) -> str | None:
    """Имя поля (`personnel_number`/`email`), которое нарушил `exc`, или
    `None`, если ограничение не входит в карту (например, случайное
    нарушение внешнего ключа) — такую ошибку нельзя классифицировать
    как гонку на известном поле, вызывающий код обязан поднять её как
    есть, а не маскировать под штатный отказ."""
    constraint_name = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
    return UNIQUE_CONSTRAINT_FIELDS.get(constraint_name)


def translate_unique_violation(
    exc: IntegrityError, *, personnel_number: str | None, email: str | None
) -> Exception:
    """`ValueError` с тем же текстом и тем же полем, что и опережающая
    проверка (`_require_personnel_number_free`/`_require_email_free`,
    `employee_service.py`) — клиент не должен различать «поймали до
    записи» и «поймали на гонке», поэтому оба пути дают один и тот же
    тип исключения и один и тот же текст. Ограничение, не входящее в
    карту, не подменяется — исходная ошибка поднимается как есть, чтобы
    не маскировать неожиданный сбой под штатный отказ 400."""
    field = unique_violation_field(exc)
    if field == "personnel_number" and personnel_number is not None:
        return ValueError(f"personnel_number: '{personnel_number}' is already in use")
    if field == "email" and email is not None:
        return ValueError(f"email: '{email}' is already in use")
    return exc

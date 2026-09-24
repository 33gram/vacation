"""PR #149 review: `employee_service._require_personnel_number_free`/
`_require_email_free` (опережающая проверка) и
`unique_violations.translate_unique_violation` (сетка под
`IntegrityError` на гонке) обязаны говорить пользователю ОДНО И ТО ЖЕ
для одного и того же условия — иначе то, поймали ли конфликт до записи
или на гонке (вопрос тайминга, не наблюдаемый пользователем), меняло бы
текст ответа. Тексты вынесены в `unique_violations.
personnel_number_conflict_message`/`email_conflict_message`, и оба пути
вызывают именно их, а не собственный литерал."""

from __future__ import annotations

import pytest

from app.modules.employees import employee_service
from app.modules.employees.unique_violations import (
    email_conflict_message,
    personnel_number_conflict_message,
    translate_unique_violation,
)
from tests.fake_integrity_error import fake_integrity_error
from tests.modules.auth.factories import make_employee


async def test_precheck_and_integrity_race_agree_on_personnel_number_text(db_session) -> None:
    existing = await make_employee(db_session, email="unique-pn@company.local")

    with pytest.raises(ValueError) as precheck_exc:
        await employee_service._require_personnel_number_free(
            db_session, existing.personnel_number
        )

    race_exc = translate_unique_violation(
        fake_integrity_error("employees_personnel_number_key"),
        personnel_number=existing.personnel_number,
        email=None,
    )

    assert str(precheck_exc.value) == str(race_exc)
    assert str(precheck_exc.value) == personnel_number_conflict_message(existing.personnel_number)


async def test_precheck_and_integrity_race_agree_on_email_text(db_session) -> None:
    existing = await make_employee(db_session, email="unique-email@company.local")

    with pytest.raises(ValueError) as precheck_exc:
        await employee_service._require_email_free(db_session, existing.email)

    race_exc = translate_unique_violation(
        fake_integrity_error("ix_employees_email_lower"),
        personnel_number=None,
        email=existing.email,
    )

    assert str(precheck_exc.value) == str(race_exc)
    assert str(precheck_exc.value) == email_conflict_message(existing.email)

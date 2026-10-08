#!/usr/bin/env python3
r"""
Проверка: если PR меняет содержимое `docs/plan.md`, `docs/tasks.md` или
`docs/ui.md`, его строка «Основание» не называет версию вышестоящего
документа старее (или новее — см. ниже) фактической версии этого
документа в ТОМ ЖЕ итоговом состоянии PR.

T-M1-44: строка «Основание» — заявление о прослеживаемости, а не
оформление; устаревший номер делает невозможным понять, учтён ли в
артефакте последний пересмотр документа, на который он опирается.
Обнаружено при ревью ветки `docs/pre-frontend-decisions` (PR #142): три
документа из трёх одновременно называли устаревшие версии оснований, при
том что сама ветка правила версию каждого из них.

Масштаб — только файлы, которые PR действительно менял, не весь граф
цитирования разом (решено в тексте задачи, не пересматривается здесь):
падать на любом расхождении независимо от предмета PR — отклонённая
альтернатива, самоссылающаяся. Коммит, поднимающий версию вышестоящего
документа (например, requirements.md), мгновенно делает устаревшими
цитаты во ВСЕХ документах, которые его цитируют, даже если этот PR их не
трогал — первая редакция задачи (`tasks.md` v1.27) это не учитывала и
сама устарела бы по собственному критерию при первой же несвязанной
правке `requirements.md`.

Граф обязательных цитат — фиксированный, а не обнаруживаемый по тексту:
`docs/plan.md` цитирует `docs/requirements.md`; `docs/tasks.md` и
`docs/ui.md` цитируют оба — `docs/requirements.md` и `docs/plan.md`;
`docs/tasks.md` цитирует дополнительно `docs/ui.md`. Это соответствует
таблице `**Основание:**` в заголовках самих документов на сегодня, не
придумано заново.

Вся логика — в `find_violations(changed_paths, documents)`, чистой
функции без обращения к git/gh/окружению (documents — отображение
repo-relative путь -> текст файла В ИТОГОВОМ СОСТОЯНИИ PR). Фактическая
версия цитируемого документа берётся из ЕГО `**Версия документа:**` в
том же `documents`, а не из базы PR — PR может поднять обе версии одним
коммитом, и сравнение с базой ругалось бы на согласованную правку.

ТРИ ЛОВУШКИ (см. ARGUMENTS задачи), каждая закрыта сознательно и
покрыта тест-кейсом в `backend/tests/test_check_basis_version.py`:

(а) Форматы цитат РАЗНЫЕ. `plan.md`: "`docs/requirements.md`, версия
    1.18". `tasks.md`/`ui.md`: "`requirements.md` v1.18". Оба написания
    поддержаны одним регэкспом (`_citation_pattern`), с необязательным
    префиксом `docs/`. Если имя документа упомянуто в блоке «Основание»
    (простой substring), но ни один формат не совпал — это отдельная
    ошибка (`UnrecognizedCitationFormat`), не молчание: цитата ЕСТЬ, а
    прочитать её не удалось.

    PR #167 review: если имя не упомянуто вовсе, а пара (цитирующий,
    цитируемый) при этом состоит в `_REQUIRED_CITATIONS`, — это ТРЕТЬЕ
    нарушение, `kind="missing"`, а не тихий пропуск. Первая версия этой
    проверки возвращала `None` и молчала здесь, и это открывало дыру по
    собственному названию: таблица `_REQUIRED_CITATIONS` объявляет, что
    документ ОБЯЗАН цитировать — а проверка, молчащая, когда обязательная
    цитата попросту удалена из строки, проходима удалением предмета
    проверки, что не защищает прослеживаемость, а защищает только тех,
    кто и так её соблюдает (воспроизведено прогоном на `docs/tasks.md`
    с вырезанным фрагментом цитаты `plan.md` — см. тело коммита). Пара,
    для которой цитата сознательно необязательна (документ перестал
    зависеть от другого), объявляется явно в `_ALLOWED_WITHOUT_CITATION`
    ниже — записью с обоснованием, видимой в дифе, а не молчанием по
    умолчанию; на сегодня таких пар нет.

(б) Версии — пары чисел (major, minor), не строки: "1.9" < "1.10"
    лексикографически дал бы обратный ответ. Это ровно случай `caa7d38`.

(в) `plan.md`'s «Основание» несёт СОБСТВЕННЫЙ журнал изменений («в
    v1.6 добавлены…», и так далее), в котором тот же `requirements.md`
    упоминается повторно с ДРУГИМИ, уже неактуальными номерами (включая
    номера, описывающие прошлую ошибку этой же проверки — «называла
    `requirements.md` версии 1.17»). Наивное «взять последнее
    упоминание» споткнулось бы ровно на этой строке. `_citation_pattern`
    привязан к ИМЕНИ документа и берёт ПЕРВОЕ совпадение в блоке
    «Основание» — для `plan.md` это декларативная цитата в начале
    строки, до собственного журнала; для `tasks.md`/`ui.md`, где
    журнала внутри «Основания» нет, первое совпадение и единственное.

Цитата НОВЕЕ фактической версии — тоже нарушение (`kind="ahead_of_
actual"`, отдельно от `kind="stale"`), решено сознательно, а не как
побочный эффект `!=`: текст задачи говорит о версиях «старее», но
цитата на несуществующую будущую версию — такой же дефект
прослеживаемости (опечатка, неудачный rebase), и молчать о нём по
умолчанию значило бы не заметить его тоже.

Честная граница (правило 8, CLAUDE.md): проверка требует, чтобы цитата
на каждую пару из `_REQUIRED_CITATIONS` ПРИСУТСТВОВАЛА (если пара не
объявлена исключением в `_ALLOWED_WITHOUT_CITATION`) и называла номер,
совпадающий с `**Версия документа:**` цитируемого файла, — но не
содержательное соответствие документов друг другу. Цитата может
называть верный номер, когда содержимое уже разошлось обновлением, не
меняющим номер версии (например, правкой без собственного коммита
версии) — такой дрейф эта проверка не видит и не претендует увидеть.
Граф `_REQUIRED_CITATIONS` сам по себе — фиксированный список,
сверенный с заголовками документов вручную при постановке задачи, а не
выводимый из их реального содержания: если документ начнёт зависеть от
нового документа, которого нет в этой таблице, проверка этого не
заметит, пока таблицу не обновят отдельной правкой.

Обвязка (`main`) получает список изменённых файлов через `gh pr diff
--name-only` (а не `gh api repos/{...}/pulls/{n}/files --paginate`
напрямую и не `git diff --name-only <base>...HEAD`): `gh pr diff` —
тонкая обёртка именно над этим REST-эндпоинтом (тот же набор прав
токена: `contents: read` на чекаут, `pull-requests: read` на сам
вызов — см. `.github/workflows/ci.yml`, job `task-checkbox-check`,
комментарий о 403 без этих прав, уже проверено прогоном в PR #156), но
возвращает готовый построчный список путей без пагинации вручную и без
разворачивания JSON (`check_task_checkbox.py`'s `_pr_commit_titles`
разворачивает `--slurp`-обёртку страниц именно поэтому, здесь это не
нужно). `git diff --name-only` не выбран: он требует `fetch-depth: 0`
специально для этого job'а (не нужен иначе), тогда как `gh pr diff`
не требует вовсе — чекаут остаётся дефолтным (достаточен: на событие
`pull_request` `actions/checkout` по умолчанию берёт merge-ref,
которое и есть «итоговое состояние PR», а историю этот скрипт не
читает — только файлы на диске и список изменённых путей).

Текст самих документов (`documents`) читается прямо из рабочего
дерева (`Path("docs/...").read_text()`) — это и есть «итоговое
состояние PR» на чекауте события `pull_request` (merge-ref), а не
нужно отдельного запроса к GitHub.

Job исполняется только на `pull_request` (`if: github.event_name ==
'pull_request'`), не на `push` — у `push` нет номера PR, а гадать его
по SHA было бы ровно тем риском ложного срабатывания, которого просит
избежать текст задачи (по аналогии с `check_task_checkbox.py`).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_DOCS = ("docs/requirements.md", "docs/plan.md", "docs/tasks.md", "docs/ui.md")

# Граф обязательных цитат — фиксированный (см. докстринг модуля), не
# обнаруживается по тексту документов.
_REQUIRED_CITATIONS: dict[str, tuple[str, ...]] = {
    "docs/plan.md": ("docs/requirements.md",),
    "docs/tasks.md": ("docs/requirements.md", "docs/plan.md", "docs/ui.md"),
    "docs/ui.md": ("docs/requirements.md", "docs/plan.md"),
}

# PR #167 review: пара (цитирующий, цитируемый) из _REQUIRED_CITATIONS,
# для которой отсутствие цитаты - не нарушение, а сознательное решение
# (документ перестал зависеть от другого). Обоснование у каждой записи
# обязательно - запись без него не исключение, а необнаруженный пробел;
# правка видна в дифе, не скрыта в логике сравнения. По образцу
# _KNOWN_GAPS/_PUBLIC_ENDPOINTS/_ALLOWED_WITHOUT_OWN_CALL в этом же
# наборе проверок. Сегодня пуст: все три документа цитируют всё, что
# обязаны (проверено прогоном на текущем дереве - см. тело коммита).
_ALLOWED_WITHOUT_CITATION: dict[tuple[str, str], str] = {}

_BASIS_BLOCK = re.compile(r"\*\*Основание:\*\*(.*?)(?=\n\*\*|\Z)", re.DOTALL)
_ACTUAL_VERSION = re.compile(r"^\*\*Версия документа:\*\*\s*(\d+)\.(\d+)", re.MULTILINE)


class UnrecognizedCitationFormat(ValueError):
    """Имя документа упомянуто в блоке «Основание», но ни один из двух
    известных форматов цитаты не совпал — цитата ЕСТЬ, прочитать её не
    удалось (см. ловушку (а) в докстринге модуля). Это не «нарушений
    нет»: падать с внятным текстом, а не молчать."""


@dataclass(frozen=True)
class Violation:
    target_path: str
    upstream_path: str
    kind: str  # "stale" | "ahead_of_actual" | "missing"
    message: str


def _basis_block(document_text: str, path: str) -> str | None:
    match = _BASIS_BLOCK.search(document_text)
    if match is None:
        return None
    return match.group(1)


def _actual_version(document_text: str, path: str) -> tuple[int, int]:
    match = _ACTUAL_VERSION.search(document_text)
    if match is None:
        raise UnrecognizedCitationFormat(
            f"{path}: не найдена строка \"**Версия документа:** X.Y\" в начале "
            f"строки — не могу определить фактическую версию этого документа"
        )
    return int(match.group(1)), int(match.group(2))


def _citation_pattern(upstream_filename: str) -> re.Pattern[str]:
    name = re.escape(upstream_filename)
    # Оба формата поддержаны одним регэкспом, см. ловушку (а):
    # "`docs/requirements.md`, версия 1.18" и "`requirements.md` v1.18".
    # `.search` (не `.findall`) берёт ПЕРВОЕ совпадение — см. ловушку (в).
    return re.compile(rf"`(?:docs/)?{name}`(?:\s*,\s*версия\s+|\s+v)(\d+)\.(\d+)")


def _cited_version(
    basis_block_text: str, upstream_path: str, target_path: str
) -> tuple[int, int] | None:
    upstream_filename = Path(upstream_path).name
    if upstream_filename not in basis_block_text:
        # Имя не упомянуто вовсе - сравнивать нечего. Это НЕ значит
        # "нарушений нет": вызывающая `find_violations` решает, является
        # ли отсутствие само по себе нарушением (см. kind="missing" и
        # _ALLOWED_WITHOUT_CITATION) - эта функция только сообщает факт.
        return None

    match = _citation_pattern(upstream_filename).search(basis_block_text)
    if match is None:
        raise UnrecognizedCitationFormat(
            f"{target_path}: \"{upstream_filename}\" упомянут в строке "
            f'"**Основание:**", но ни один из двух известных форматов цитаты '
            f'("`{upstream_filename}` versия X.Y" / "`{upstream_filename}` '
            f'vX.Y", с необязательным префиксом docs/) не совпал — цитата '
            f"есть, прочитать номер версии не удалось"
        )
    return int(match.group(1)), int(match.group(2))


def find_violations(changed_paths: list[str], documents: dict[str, str]) -> list[Violation]:
    violations: list[Violation] = []
    changed = set(changed_paths)

    for target_path, upstream_paths in _REQUIRED_CITATIONS.items():
        if target_path not in changed:
            continue
        if target_path not in documents:
            raise UnrecognizedCitationFormat(
                f"{target_path}: файл изменён, но его текста нет в documents"
            )

        basis_block_text = _basis_block(documents[target_path], target_path)
        if basis_block_text is None:
            raise UnrecognizedCitationFormat(
                f'{target_path}: не найдена строка "**Основание:**"'
            )

        for upstream_path in upstream_paths:
            cited = _cited_version(basis_block_text, upstream_path, target_path)
            if cited is None:
                if (target_path, upstream_path) in _ALLOWED_WITHOUT_CITATION:
                    continue
                violations.append(
                    Violation(
                        target_path=target_path,
                        upstream_path=upstream_path,
                        kind="missing",
                        message=(
                            f"{target_path}: обязан цитировать "
                            f"{Path(upstream_path).name} в строке "
                            f'"**Основание:**", но цитаты нет вовсе'
                        ),
                    )
                )
                continue

            if upstream_path not in documents:
                raise UnrecognizedCitationFormat(
                    f"{target_path}: цитирует {upstream_path}, но его текста "
                    f"нет в documents"
                )
            actual = _actual_version(documents[upstream_path], upstream_path)

            if cited < actual:
                violations.append(
                    Violation(
                        target_path=target_path,
                        upstream_path=upstream_path,
                        kind="stale",
                        message=(
                            f"{target_path}: «Основание» называет "
                            f"{Path(upstream_path).name} версии "
                            f"{cited[0]}.{cited[1]}, фактическая — "
                            f"{actual[0]}.{actual[1]}"
                        ),
                    )
                )
            elif cited > actual:
                violations.append(
                    Violation(
                        target_path=target_path,
                        upstream_path=upstream_path,
                        kind="ahead_of_actual",
                        message=(
                            f"{target_path}: «Основание» называет "
                            f"{Path(upstream_path).name} версии "
                            f"{cited[0]}.{cited[1]}, фактическая версия "
                            f"({actual[0]}.{actual[1]}) ещё не достигнута — "
                            f"ссылка на несуществующее состояние документа"
                        ),
                    )
                )

    return violations


def _changed_paths(pr_number: str | None) -> list[str]:
    args = ["gh", "pr", "diff"]
    if pr_number:
        args.append(pr_number)
    args.append("--name-only")

    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        hint = ""
        if "403" in result.stderr:
            hint = (
                "\nКод 403 почти всегда означает нехватку прав токена — "
                "проверить блок `permissions:` этого job'а в "
                ".github/workflows/ci.yml (нужны как минимум `contents: read` "
                "и `pull-requests: read`)."
            )
        print(
            f"не удалось получить список изменённых файлов PR через "
            f"`gh pr diff --name-only` (код {result.returncode}): "
            f"{result.stderr.strip()}{hint}",
            file=sys.stderr,
        )
        sys.exit(2)

    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    import os

    pr_number = os.environ.get("PR_NUMBER")
    changed_paths = _changed_paths(pr_number)

    documents = {}
    for doc in _DOCS:
        path = Path(doc)
        if path.is_file():
            documents[doc] = path.read_text(encoding="utf-8")

    try:
        violations = find_violations(changed_paths, documents)
    except UnrecognizedCitationFormat as exc:
        print(f"Не удалось разобрать строку «Основание» (T-M1-44): {exc}", file=sys.stderr)
        return 2

    if violations:
        print(
            "Строка «Основание» называет версию вышестоящего документа, "
            "расходящуюся с фактической (T-M1-44):",
            file=sys.stderr,
        )
        for violation in violations:
            print(f"  - {violation.message}", file=sys.stderr)
        return 1

    checked = [p for p in changed_paths if p in _REQUIRED_CITATIONS]
    print(
        f"Строки «Основание» в порядке ({len(checked)} из "
        f"{len(changed_paths)} изменённых файлов относятся к проверке)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

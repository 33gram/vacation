#!/usr/bin/env python3
r"""
Проверка: если среди коммитов PR есть хотя бы один, чей заголовок
НАЧИНАЕТСЯ с идентификатора задачи (`T-Mx-yy: …`), соответствующая
задача обязана стоять `[x]` в `docs/tasks.md` на голове этого PR.

T-M1-49: чек-бокс задачи (правило 6) забывался в трёх PR подряд —
`T-M1-45` (PR #143, найдено кругом ревью), `T-M1-18` (PR #144, не
найдено никем, отметка поставлена задним числом в чужой ветке),
`T-M1-48` (PR #146). Отметка делается последней в потоке работы, и до
этой проверки ничто её не требовало.

«Начинается с», а не «упоминает» (PR #144 vs PR #143, см. тесты).
Коммит, заводящий задачу («tasks.md v1.33: T-M1-46, …», PR #143),
называет её ID в середине заголовка, но не выполняет её — требовать
отметки от такого коммита нельзя. Шаблон — строгий якорь в начале
строки, `T-M<цифры>-<цифры>:`. Различие несёт вся проверка целиком:
реализация, которая ищет ID где угодно в строке, красит PR #143 тоже
(там T-M1-46 и T-M1-47 на момент того PR ещё не отмечены) — ровно
такую ошибку и ловит `test_pr143_mid_title_mentions_are_silent`.

Идентификатор, которого нет в `docs/tasks.md` вовсе (опечатка, задача
переименована, перенесена и т.п.) — это не «отметка не поставлена», а
ссылка в пустоту; отдельное сообщение (`kind="unknown_task"`), не слито
с основным случаем (`kind="not_ticked"`).

Логика вынесена в `find_violations` — чистую функцию от трёх значений
(заголовки коммитов, текст `docs/tasks.md`, текст описания PR), без
какого-либо обращения к git/CI/окружению. Так отрицательные контроли
(тесты) гоняются на настоящих исторических данных (PR #143, #144),
взятых `git log`/`git show`, а не имитируются.

Источник заголовков коммитов PR — `gh api repos/{repo}/pulls/{n}/commits`
(REST), а не `gh pr view --json commits` (GraphQL) и не `git log
base..head`:

- `git log base..head` ловушка: при событии `pull_request`
  `actions/checkout` по умолчанию берёт merge-ref с глубиной 1 — ни
  `base`, ни `head` в таком чекауте не резолвятся, `git log` на них
  дал бы пустой список молча. Обход — либо `fetch-depth: 0` (полная
  история на каждый прогон), либо API. Выбран API: он не требует
  раздувать чекаут этого job'а (проверка вообще не читает git-историю,
  только `docs/tasks.md` из уже отражённого в чекауте рабочего дерева и
  список коммитов по сети) — раннеры GitHub несут `gh` предустановленным,
  токен — `github.token`, второй сети не требуется.
- `gh pr view --json commits` (GraphQL `messageHeadline`) не выбран,
  хотя и рассматривался: прогон на PR #144 показал, что это поле
  МОЛЧА обрезает длинный первый абзац многоточием (см. тело коммита —
  прогон и сравнение с REST'ом задокументированы там, не здесь).
  Нашей проверке для якоря `^T-M\d+-\d+:` обрезка первой строки почти
  никогда не мешает (якорь — первые символы), но раз есть источник без
  этого дефекта вовсе — используется он: `gh api .../commits` отдаёт
  ПОЛНОЕ, необрезанное `commit.message`.

Описание PR берётся из контекста события (`github.event.pull_request.body`,
переменная окружения `PR_BODY`), а не отдельным запросом — оно уже
доступно там, откуда вызывается этот скрипт, второй запрос был бы
дублирующим. Передача именно через `env:`, а не подстановкой в текст
`run:`, — не эстетика: `github.event.pull_request.body` пишет
пользователь, открывающий PR, и подстановка `${{ github.event.pull_
request.body }}` прямо в текст команды была бы классической инъекцией
в shell (GitHub сам предупреждает об этом в документации Actions);
через `env:` то же значение становится переменной окружения процесса,
а не текстом команды, и не интерпретируется шеллом.

Job исполняется только на событие `pull_request`
(`if: github.event_name == 'pull_request'`), не на `push`: при `push`
нет ни номера PR, ни его описания, и пытаться угадать PR по коммиту
(например, по SHA через API) — ровно тот риск ложного срабатывания,
которого задача просит избежать.

CLI поддерживает два источника входных данных:
- CI: `PR_NUMBER` обязателен, `GITHUB_REPOSITORY` (Actions подставляет
  его автоматически в любом job'е) и `PR_BODY` (опционален, пусто —
  как у PR без описания) читаются из окружения без сетевых запросов
  сверх одного (список коммитов);
- локально (`make check-task-checkbox`, без `PR_NUMBER` в окружении):
  репозиторий/номер/описание определяются через `gh pr view` для
  текущей ветки — не работает в CI не потому, что не могло бы (в
  проверенном отдельно вызове `gh pr view` на чекауте по SHA PR-мержа
  тоже отвечает), а потому что это была бы ровно запрещённая выше
  «угадайка», а не чтение из контекста события.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_TASK_ID_AT_START = re.compile(r"^(T-M\d+-\d+):")
_TASK_CHECKBOX_LINE = re.compile(r"^- \[([ x])\] \*\*(T-M\d+-\d+)\*\*", re.MULTILINE)
_PARTIAL_CLOSURE = re.compile(r"^\s*частично:\s*(T-M\d+-\d+)\b", re.MULTILINE)


@dataclass(frozen=True)
class Violation:
    task_id: str
    kind: str  # "not_ticked" | "unknown_task"
    message: str


def find_violations(
    commit_titles: list[str],
    tasks_md_text: str,
    pr_body: str,
) -> list[Violation]:
    task_ids: list[str] = []
    seen: set[str] = set()
    for title in commit_titles:
        match = _TASK_ID_AT_START.match(title)
        if match is None:
            continue
        task_id = match.group(1)
        if task_id not in seen:
            seen.add(task_id)
            task_ids.append(task_id)

    if not task_ids:
        return []

    checkbox_by_id: dict[str, str] = {}
    for state, task_id in _TASK_CHECKBOX_LINE.findall(tasks_md_text):
        checkbox_by_id.setdefault(task_id, state)

    partially_closed = {m.group(1) for m in _PARTIAL_CLOSURE.finditer(pr_body)}

    violations: list[Violation] = []
    for task_id in task_ids:
        if task_id not in checkbox_by_id:
            violations.append(
                Violation(
                    task_id=task_id,
                    kind="unknown_task",
                    message=(
                        f"{task_id}: коммит начинается с этого ID, но "
                        f"такой задачи нет в docs/tasks.md"
                    ),
                )
            )
            continue

        if checkbox_by_id[task_id] == "x":
            continue

        if task_id in partially_closed:
            continue

        violations.append(
            Violation(
                task_id=task_id,
                kind="not_ticked",
                message=(
                    f"{task_id}: коммит начинается с этого ID, но чек-бокс "
                    f"в docs/tasks.md не [x] (и в описании PR нет строки "
                    f'"частично: {task_id} — <причина>")'
                ),
            )
        )

    return violations


def _pr_commit_titles(repo: str, pr_number: str) -> list[str]:
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr_number}/commits", "--paginate", "--slurp"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(
            f"не удалось получить список коммитов PR через gh api (код "
            f"{result.returncode}): {result.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(2)

    # `--slurp` оборачивает СТРАНИЦЫ (каждая — свой JSON-массив) в общий
    # внешний массив, а не сами элементы — результат «список списков»,
    # даже когда страница одна (проверено прогоном на PR #144, не
    # предположено). Разворачиваем в плоский список.
    pages: list[list[dict]] = json.loads(result.stdout)
    commits = [commit for page in pages for commit in page]
    return [commit["commit"]["message"].split("\n", 1)[0] for commit in commits]


def _local_pr_context() -> tuple[str, str, str]:
    result = subprocess.run(
        ["gh", "pr", "view", "--json", "number,body,headRepository"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(
            "не удалось определить PR текущей ветки через `gh pr view` "
            f"(код {result.returncode}): {result.stderr.strip()}\n"
            "локальный запуск (`make check-task-checkbox`) требует ветки "
            "с открытым PR; в CI PR_NUMBER/PR_BODY берутся из контекста "
            "события, не через эту автогадалку.",
            file=sys.stderr,
        )
        sys.exit(2)

    data = json.loads(result.stdout)
    repo = data["headRepository"]["nameWithOwner"]
    return repo, str(data["number"]), data.get("body") or ""


def main() -> int:
    if "PR_NUMBER" in os.environ:
        repo = os.environ["GITHUB_REPOSITORY"]
        pr_number = os.environ["PR_NUMBER"]
        pr_body = os.environ.get("PR_BODY") or ""
    else:
        repo, pr_number, pr_body = _local_pr_context()

    commit_titles = _pr_commit_titles(repo, pr_number)
    tasks_md_text = Path("docs/tasks.md").read_text(encoding="utf-8")

    violations = find_violations(commit_titles, tasks_md_text, pr_body)

    if violations:
        print(
            "Чек-бокс задачи не отмечен, хотя её коммиты уже в PR "
            "(CLAUDE.md, правило 6):",
            file=sys.stderr,
        )
        for violation in violations:
            print(f"  - {violation.message}", file=sys.stderr)
        return 1

    print(f"Чек-боксы задач в порядке ({len(commit_titles)} коммитов PR проверено).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Массовое создание GitHub Milestones и Issues из docs/tasks.md.

Требования:
  - установлен и авторизован GitHub CLI (`gh auth login`)
  - запускать из корня репозитория (либо передать --tasks-file с полным путём)
  - целевой репозиторий указывается флагом --repo owner/name

Пример:
    python scripts/import_tasks_to_github.py --repo mycompany/vacation-tracker --dry-run
    python scripts/import_tasks_to_github.py --repo mycompany/vacation-tracker
    python scripts/import_tasks_to_github.py --repo mycompany/vacation-tracker --delay 1.5

Важно про идемпотентность:
  - Повторное создание Milestone с тем же названием безопасно пропускается.
  - Создание Issue НЕ проверяет дубликаты по заголовку — при повторном запуске
    задачи будут созданы заново. Запускайте скрипт один раз на «чистый» этап
    настройки репозитория, либо самостоятельно почистите дубликаты между запусками.
  - Отмеченные как выполненные в tasks.md задачи (`- [x]`) создаются с дополнительной
    меткой `status:done` — закрыть такие Issues вручную после импорта.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

MILESTONE_PATTERN = re.compile(r"^##\s+(M\d)\.\s+(.+)$")
# Секция сквозных подготовительных задач (T-000, T-001) — они вне этапов M0-M8
PREP_SECTION_PATTERN = re.compile(r"^##\s+Сквозные задачи")
PREP_MILESTONE_KEY = "M-pre"
PREP_MILESTONE_TITLE = "M-pre — Сквозные подготовительные задачи"
# ID задачи: этапная (T-M1-06) либо сквозная подготовительная (T-000)
TASK_PATTERN = re.compile(r"^- \[( |x)\]\s+\*\*(T-(?:M\d+-\d+|\d{3}))\*\*\s+(?:\[P\]\s+)?(.+)$")
REQ_REF_PATTERN = re.compile(r"`((?:FR|NFR|BR|A)-[A-Za-z0-9\-]+)`")

# Метки по функциональным модулям (расширяйте при необходимости под свои конвенции)
MODULE_LABELS: dict[str, str] = {
    "FR-AUTH": "module:auth",
    "FR-EMP": "module:employees",
    "FR-BAL": "module:balances",
    "FR-SCHED": "module:schedule",
    "FR-REQ": "module:requests",
    "FR-SICK": "module:sick-leave",
    "FR-DAYOFF": "module:day-off",
    "FR-APPR": "module:approvals",
    "FR-NOTIF": "module:notifications",
    "FR-CAL": "module:calendar",
    "FR-REPORT": "module:reports",
    "FR-ADMIN": "module:admin",
    "FR-AUDIT": "module:audit",
    "NFR-SEC": "area:security",
    "NFR-PERF": "area:performance",
    "NFR": "area:nfr",
}


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def ensure_label(repo: str, label: str) -> None:
    result = run(["gh", "label", "create", label, "--repo", repo, "--color", "ededed", "--force"])
    if result.returncode != 0 and "already exists" not in result.stderr:
        print(f"  ! Не удалось создать метку {label}: {result.stderr.strip()}", file=sys.stderr)


def create_milestone(repo: str, title: str, description: str) -> None:
    result = run(
        [
            "gh", "api", f"repos/{repo}/milestones",
            "-f", f"title={title}",
            "-f", f"description={description}",
            "-f", "state=open",
        ]
    )
    if result.returncode != 0:
        if "already_exists" in result.stdout or "already_exists" in result.stderr:
            print(f"  · Milestone «{title}» уже существует — пропуск")
        else:
            print(f"  ! Ошибка создания milestone «{title}»: {result.stderr.strip()}", file=sys.stderr)
    else:
        print(f"  + Создан milestone «{title}»")


def module_labels_for(text: str) -> list[str]:
    labels: set[str] = set()
    for ref in REQ_REF_PATTERN.findall(text):
        for prefix, label in MODULE_LABELS.items():
            if ref.startswith(prefix):
                labels.add(label)
                break
    return sorted(labels)


def create_issue(repo: str, milestone_title: str, task_id: str, description: str, done: bool) -> None:
    title = f"[{task_id}] {description[:200]}"
    body_lines = [
        f"**ID задачи:** `{task_id}`",
        f"**Этап:** {milestone_title}",
        "",
        description,
        "",
        "---",
        "_Задача импортирована автоматически из `docs/tasks.md`. "
        "Полный контекст, зависимости и критерии приёмки (EARS) — в `docs/tasks.md` и `docs/requirements.md`._",
    ]
    labels = ["task"] + module_labels_for(description)
    if done:
        labels.append("status:done")

    cmd = [
        "gh", "issue", "create",
        "--repo", repo,
        "--title", title,
        "--body", "\n".join(body_lines),
        "--milestone", milestone_title,
    ]
    for label in labels:
        cmd += ["--label", label]

    result = run(cmd)
    if result.returncode != 0:
        print(f"  ! Ошибка создания issue {task_id}: {result.stderr.strip()}", file=sys.stderr)
    else:
        print(f"  + {task_id} -> {result.stdout.strip()}")


def parse_tasks(tasks_file: Path) -> tuple[dict[str, str], list[tuple[str, str, str, bool]]]:
    milestones: dict[str, str] = {}
    tasks: list[tuple[str, str, str, bool]] = []  # (milestone_title, task_id, description, done)
    current_milestone: str | None = None

    for raw_line in tasks_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if PREP_SECTION_PATTERN.match(line):
            current_milestone = PREP_MILESTONE_TITLE
            milestones[PREP_MILESTONE_KEY] = PREP_MILESTONE_TITLE
            continue

        m = MILESTONE_PATTERN.match(line)
        if m:
            key, title = m.group(1), m.group(2)
            current_milestone = f"{key} — {title}"
            milestones[key] = current_milestone
            continue

        t = TASK_PATTERN.match(line)
        if t and current_milestone:
            done_mark, task_id, description = t.groups()
            tasks.append((current_milestone, task_id, description.strip(), done_mark == "x"))

    return milestones, tasks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="owner/name целевого репозитория на GitHub")
    parser.add_argument("--tasks-file", default="docs/tasks.md", help="путь к tasks.md (по умолчанию docs/tasks.md)")
    parser.add_argument("--dry-run", action="store_true", help="только показать, что будет создано, без вызова gh")
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="пауза в секундах между созданием issue (по умолчанию 1.0) — страховка от вторичных "
             "лимитов GitHub (~80 создающих контент запросов в минуту)",
    )
    args = parser.parse_args()

    tasks_file = Path(args.tasks_file)
    if not tasks_file.exists():
        sys.exit(
            f"Файл не найден: {tasks_file}. "
            f"Укажите --tasks-file, если tasks.md лежит по другому пути или называется иначе."
        )

    milestones, tasks = parse_tasks(tasks_file)
    print(f"Найдено этапов: {len(milestones)}, задач: {len(tasks)}\n")

    if not tasks:
        sys.exit("Не удалось распознать ни одной задачи — проверьте формат tasks.md и регулярные выражения в скрипте.")

    if args.dry_run:
        print("--- DRY RUN: ничего не будет создано на GitHub ---\n")
        for key, title in milestones.items():
            print(f"[milestone] {title}")
        print()
        for milestone_title, task_id, description, done in tasks:
            mark = "x" if done else " "
            print(f"  [{mark}] {task_id} ({milestone_title}): {description[:100]}")
        return

    print("Создание milestones...")
    for key, title in milestones.items():
        if key == PREP_MILESTONE_KEY:
            description = "Сквозные подготовительные задачи до начала этапа M0 (проект «Система учёта отпусков»)"
        else:
            description = f"Этап {key} проекта «Система учёта отпусков»"
        create_milestone(args.repo, title, description)

    print("\nСоздание меток модулей...")
    for label in sorted(set(MODULE_LABELS.values()) | {"task", "status:done"}):
        ensure_label(args.repo, label)

    print(f"\nСоздание issues (пауза между вызовами: {args.delay} сек)...")
    for index, (milestone_title, task_id, description, done) in enumerate(tasks):
        create_issue(args.repo, milestone_title, task_id, description, done)
        if args.delay > 0 and index < len(tasks) - 1:
            time.sleep(args.delay)

    print("\nГотово. Проверьте вкладки Issues и Milestones репозитория.")


if __name__ == "__main__":
    main()

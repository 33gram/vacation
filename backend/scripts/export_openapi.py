"""Экспортирует OpenAPI-схему FastAPI-приложения в JSON-файл.

Используется frontend'ом для автогенерации TypeScript-клиента (plan.md §7).
Запуск: python scripts/export_openapi.py
"""

import json
from pathlib import Path

from app.main import app

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "openapi.json"


def main() -> None:
    OUTPUT_PATH.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"OpenAPI schema written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

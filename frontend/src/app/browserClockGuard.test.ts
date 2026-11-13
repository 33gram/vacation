import { describe, expect, it } from "vitest";

/** Сторож «бизнес-дата по часам браузера» (`T-M2-36`/`T-M2-38`, `NFR-LOC-002`).
 *
 * Класс дефектов - значение «сегодня» из часов браузера там, где нужен бизнес-день сервера - дал
 * три задачи (`T-M2-34`, `T-M2-36`, `T-M2-38`), и каждую находили поиском руками. Сторож ОБНАРУЖИВАЕТ
 * (не перечисляет): ищет в `frontend/src` (кроме тестов, `test-utils` и сгенерированного клиента)
 * вызовы БЕЗ аргументов - `new Date()`, `Date.now()`, `dayjs()` - и требует, чтобы каждый адрес
 * (файл) стоял в явном списке `ALLOWED` с ПРИЧИНОЙ. Сегодня список ПУСТ: в приложении нет ни
 * одного такого вызова, день берётся из сессии (`useBusinessDay`) или из ответа остатка.
 *
 * ГРАНИЦЫ (правило 8), что сторож НЕ видит: вызов с аргументом, который сам несёт часы
 * (`new Date(Date.now())` ловится по `Date.now()`, но `dayjs(undefined)`, `new Date(undefined)`,
 * `dayjs(new Date())` ловится только по внутреннему `new Date()`), псевдоним импорта
 * (`import { now } ...`), `Intl.DateTimeFormat().resolvedOptions()`, `performance.now()`, строки и
 * шаблоны; комментарии срезаются грубо (строчные и блочные; строка с двумя косыми чертами внутри
 * литерала срежется целиком - это даёт ПРОПУСК, а не ложное срабатывание). Сторож доказывает отсутствие ловимых
 * вызовов, а не отсутствие часов браузера вообще. */
const SOURCES = import.meta.glob(
  [
    "../**/*.{ts,tsx}",
    "!../**/*.test.{ts,tsx}",
    "!../test-utils/**",
    "!../api/generated/**",
  ],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

/** Адрес (путь файла относительно `src`) -> причина, по которой вызов без аргументов здесь верен
 * (формат показа, внутренности элемента выбора). Пусто сегодня; запись без причины недопустима. */
const ALLOWED: Record<string, string> = {};

/** Ключи `import.meta.glob` - пути ОТНОСИТЕЛЬНО этого файла (`./X` для соседей, `../x/Y` для
 * остальных); адрес для `ALLOWED` - путь от `src` (`app/X`, `pages/Y`). */
function address(key: string): string {
  return key.startsWith("./") ? `app/${key.slice(2)}` : key.replace(/^\.\.\//, "");
}

const CALLS = [/new\s+Date\(\s*\)/g, /Date\.now\(\s*\)/g, /\bdayjs\(\s*\)/g];

function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
}

export function findClockCalls(source: string): string[] {
  const code = stripComments(source);
  return CALLS.flatMap((pattern) => code.match(pattern) ?? []);
}

describe("browser clock guard (T-M2-36, T-M2-38)", () => {
  it("scans the application sources, not an empty set", () => {
    // Порог = измеренное число файлов (приложение без тестов, test-utils и generated): сторож не
    // слепнет, если glob перестанет находить файлы.
    expect(Object.keys(SOURCES).length).toBeGreaterThanOrEqual(MEASURED_FILES);
  });

  it("finds the calls without arguments and ignores calls with arguments and comments", () => {
    expect(findClockCalls("const a = new Date();")).toEqual(["new Date()"]);
    expect(findClockCalls("const a = Date.now();")).toEqual(["Date.now()"]);
    expect(findClockCalls("const a = dayjs( );")).toEqual(["dayjs( )"]);
    expect(findClockCalls("new Date(2026, 1, 1); dayjs('2026-01-01'); dayjs(x)")).toEqual([]);
    expect(findClockCalls("// new Date()\\n/* dayjs() */ const a = 1;")).toEqual([]);
  });

  it("every call without arguments sits at an address allowed WITH a reason", () => {
    const offenders = Object.entries(SOURCES)
      .map(([key, source]) => [address(key), findClockCalls(source)] as const)
      .filter(([, calls]) => calls.length > 0)
      .filter(([path]) => !(path in ALLOWED) || ALLOWED[path].trim() === "")
      .map(([path, calls]) => `${path}: ${calls.join(", ")}`);

    expect(offenders).toEqual([]);
  });

  it("the allowed list holds no stale address: each listed file still has such a call", () => {
    const sourceAt = new Map(Object.entries(SOURCES).map(([key, text]) => [address(key), text]));
    const stale = Object.keys(ALLOWED).filter(
      (path) => findClockCalls(sourceAt.get(path) ?? "").length === 0,
    );

    expect(stale).toEqual([]);
  });
});

/** Число файлов приложения на момент заведения сторожа (измерено: `Object.keys(SOURCES).length`). */
const MEASURED_FILES = 64;

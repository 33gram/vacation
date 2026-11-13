import { describe, expect, it } from "vitest";

/** Сторож «бизнес-дата по часам браузера» (`T-M2-36`/`T-M2-38`, `NFR-LOC-002`).
 *
 * Класс дефектов - значение «сегодня» из часов браузера там, где нужен бизнес-день сервера - дал
 * три задачи (`T-M2-34`, `T-M2-36`, `T-M2-38`), и каждую находили поиском руками. Сторож ОБНАРУЖИВАЕТ
 * (не перечисляет): ищет в `frontend/src` (кроме тестов, `test-utils` и сгенерированного клиента)
 * вызовы БЕЗ аргументов - `new Date()` и `new Date` (без скобок), `new globalThis.Date()`,
 * `new window.Date()`, `Date.now()` (в том числе `globalThis.Date.now()`), `Date["now"]()`,
 * `dayjs()`, `dayjs.utc()`, `moment()` - и требует, чтобы каждый адрес (файл) стоял в явном списке
 * `ALLOWED` с ПРИЧИНОЙ. Сегодня список ПУСТ: в приложении нет ни одного такого вызова, день берётся
 * из сессии (`useBusinessDay`) или из ответа остатка. Маска файлов: `.ts`, `.tsx`, `.js`, `.jsx`,
 * `.mts`, `.mjs`.
 *
 * ГРАНИЦЫ (правило 8), что сторож НЕ видит (измерено синтетическими примерами): вызов с аргументом,
 * который сам несёт часы (`dayjs(undefined)`, `new Date(undefined)`, `dayjs(new Date())` ловится
 * только по внутреннему `new Date()`), псевдоним импорта (`import { now as n }`, `const D = Date;
 * new D()`), доступ через переменную (`Date[name]()`), `Reflect.construct(Date, [])`,
 * `performance.now()`, `Intl.DateTimeFormat().resolvedOptions()`, регулярные литералы с кавычками
 * (`/"/`) - они сбивают срезание строк и комментариев. Сторож доказывает отсутствие ЛОВИМЫХ вызовов,
 * а не отсутствие часов браузера вообще.
 *
 * Срезание комментариев и строк - посимвольный разбор (строчные и блочные комментарии, строки
 * в одинарных и двойных кавычках, шаблонные строки), а не регулярное выражение: маркер комментария
 * ВНУТРИ строки (`"/*"`) больше не срезает код между маркерами. Текст в кавычках отбрасывается
 * (вызов в тексте сообщения - не вызов); код внутри `${...}` шаблона остаётся виден. */
const SOURCES = import.meta.glob(
  [
    "../**/*.{ts,tsx,js,jsx,mts,mjs}",
    "!../**/*.test.{ts,tsx,js,jsx,mts,mjs}",
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

const CALLS = [
  /new\s+(?:(?:globalThis|window|self)\s*\.\s*)?Date\b(?!\s*\([^)])(?:\s*\(\s*\))?/g,
  /(?:(?:globalThis|window|self)\s*\.\s*)?\bDate\s*\.\s*now\s*\(\s*\)/g,
  /\bdayjs\s*\(\s*\)/g,
  /\bdayjs\s*\.\s*utc\s*\(\s*\)/g,
  /\bmoment\s*\(\s*\)/g,
];

/** Код без комментариев и текста в кавычках. Состояния: код, одинарные и двойные кавычки, шаблон, строчный и блочный комментарии. */
function stripCommentsAndStrings(source: string, keepStrings = false): string {
  let out = "";
  let i = 0;
  while (i < source.length) {
    const c = source[i];
    const next = source[i + 1];
    if (c === "/" && next === "/") {
      while (i < source.length && source[i] !== "\n") i += 1;
    } else if (c === "/" && next === "*") {
      const end = source.indexOf("*/", i + 2);
      i = end === -1 ? source.length : end + 2;
    } else if (c === "'" || c === '"') {
      out += c;
      i += 1;
      while (i < source.length && source[i] !== c && source[i] !== "\n") {
        if (keepStrings) out += source.slice(i, i + (source[i] === "\\" ? 2 : 1));
        i += source[i] === "\\" ? 2 : 1;
      }
      out += c;
      i += 1;
    } else if (c === "`") {
      // Шаблон: текст остаётся (в нём бывает `${код}`), маркеры комментариев внутри не действуют.
      out += c;
      i += 1;
      while (i < source.length && source[i] !== "`") {
        out += source[i] === "\\" ? source.slice(i, i + 2) : source[i];
        i += source[i] === "\\" ? 2 : 1;
      }
      out += "`";
      i += 1;
    } else {
      out += c;
      i += 1;
    }
  }
  return out;
}

function findClockCalls(source: string): string[] {
  const code = stripCommentsAndStrings(source);
  // `Date["now"]()` читает имя из строки, поэтому ищется там, где строки сохранены.
  const withStrings = stripCommentsAndStrings(source, true);
  const bracket = withStrings.match(/\bDate\s*\[\s*["']now["']\s*\]\s*\(\s*\)/g) ?? [];
  return [...CALLS.flatMap((pattern) => code.match(pattern) ?? []), ...bracket].map((call) =>
    call.trim(),
  );
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
    expect(findClockCalls("// new Date()\n/* dayjs() */ const a = 1;")).toEqual([]);
  });

  it("finds the cheap-to-catch spellings (PR #197 review)", () => {
    expect(findClockCalls("const a = new Date;")).toEqual(["new Date"]);
    expect(findClockCalls("const a = new globalThis.Date();")).toEqual(["new globalThis.Date()"]);
    expect(findClockCalls("const a = new window.Date();")).toEqual(["new window.Date()"]);
    expect(findClockCalls("const a = globalThis.Date.now();")).toEqual(["globalThis.Date.now()"]);
    expect(findClockCalls('const a = Date["now"]();')).toEqual(['Date["now"]()']);
    expect(findClockCalls("const a = dayjs.utc();")).toEqual(["dayjs.utc()"]);
    expect(findClockCalls("const a = moment();")).toEqual(["moment()"]);
    expect(findClockCalls("const a = new Date(2026, 1, 1); const b = new DateFormatter;")).toEqual(
      [],
    );
  });

  it("a comment marker INSIDE a string literal does not hide the code between (PR #197 review)", () => {
    // Раньше срезалось всё от `/*` в первой строке до `*/` во второй - вызов между ними
    // пропускался (пропуск, не ложное срабатывание).
    expect(findClockCalls('const a = "/*"; const b = new Date(); const c = "*/";')).toEqual([
      "new Date()",
    ]);
    expect(findClockCalls('const url = "http://x"; const b = Date.now();')).toEqual(["Date.now()"]);
    expect(findClockCalls('const text = "new Date()";')).toEqual([]); // текст строки - не вызов
    expect(findClockCalls("const a = `${new Date()}`;")).toEqual(["new Date()"]); // код в шаблоне
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

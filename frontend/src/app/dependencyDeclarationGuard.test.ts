import { describe, expect, it } from "vitest";

import lockText from "../../package-lock.json?raw";
import packageText from "../../package.json?raw";

/** Сторож «импорт объявлен» (`T-M2-35`).
 *
 * Класс дефектов - ГОЛЫЙ импорт пакета, которого нет в `package.json`, - держался на транзитивной
 * зависимости (`dayjs` приходил с `antd`), и ни `tsc`, ни `eslint` (правила
 * `import/no-extraneous-dependencies` в конфиге нет, плагин не добавлялся: новая зависимость требует
 * своего обоснования) его не ловят. Сторож ОБНАРУЖИВАЕТ: собирает голые спецификаторы импорта в
 * `frontend/src` (кроме `api/generated/**`), приводит к имени пакета (`@scope/name`, подпуть
 * `antd/es/table` -> `antd`) и сверяет с `package.json` на ДВУХ уровнях:
 *   - файлы ПРИЛОЖЕНИЯ - только `dependencies` (иначе промышленная сборка зависела бы от
 *     `devDependencies`: `import "vitest"` в приложении собирается у разработчика и ломается там,
 *     где ставят без devDependencies; сегодня `npm ci` в CI и в `frontend/Dockerfile` ставит всё,
 *     поэтому это защита от будущего дефекта, а не от нынешнего);
 *   - ТЕСТЫ (`*.test.*`, `test-utils/**`, `setupTests.*`) - `dependencies` или `devDependencies`.
 * Исключения - `ALLOWED` (пакет -> причина), проверка в обе стороны. Встроенные `node:*` и
 * относительные пути не считаются.
 *
 * Вторая проверка - по `package-lock.json`: КАЖДАЯ объявленная в `dependencies` зависимость стоит
 * в замке ровно одной копией (`node_modules/<имя>` без вложенных). Вторая копия `dayjs` развела бы
 * классы `Dayjs` приложения и `DatePicker`, вторая `react` сломала бы хуки. Расширение до «ни один
 * пакет не продублирован» отвергнуто: сегодня в замке 14 из 381 имён стоят в нескольких копиях
 * (`minimatch`, `semver`, `lru-cache`, ...), это транзитивные инструменты сборки и линтера с
 * несовместимыми мажорами, их объекты между приложением и библиотекой не ходят, а общий запрет
 * потребовал бы перечня из 14 исключений, который никто не читает.
 *
 * ГРАНИЦЫ (правило 8). Не видит: динамический `import("x")`, `require("x")`, спецификатор из
 * переменной, `vi.mock("x")`, `/// <reference types>` и импорт в `*.d.ts` без оператора импорта,
 * пакеты `@types/*` (тип подтягивается неявно, а не спецификатором); регулярные литералы с
 * кавычками (`/"/`) сбивают срезание комментариев. Встроенный модуль без префикса (`fs`) будет
 * считаться необъявленным пакетом. Сторож доказывает, что каждый ЛОВИМЫЙ импорт объявлен, а не что
 * объявленная версия подходит и не что пакет существует в реестре. */
const SOURCES = import.meta.glob(
  ["../**/*.{ts,tsx,js,jsx,mts,mjs}", "!../api/generated/**"],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

/** Пакет -> причина, по которой импорт без объявления верен. Пусто сегодня; без причины недопустимо. */
const ALLOWED: Record<string, string> = {};

/** Число просмотренных файлов `frontend/src` (кроме `api/generated/**`) на момент заведения: измерено. */
const MEASURED_FILES = 95;

interface Manifest {
  dependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
}

const MANIFEST = JSON.parse(packageText) as Manifest;
const LOCK = JSON.parse(lockText) as { packages: Record<string, unknown> };

/** Ключи `import.meta.glob`: `./X` для соседей, `../x/Y` для остальных; адрес - путь от `src`. */
function address(key: string): string {
  return key.startsWith("./") ? `app/${key.slice(2)}` : key.replace(/^\.\.\//, "");
}

function isTestLevel(path: string): boolean {
  return /\.test\.[a-z]+$/.test(path) || path.startsWith("test-utils/") || /^setupTests\./.test(path);
}

/** Исходник без комментариев в двух видах ОДНОЙ длины: `plain` (строки как есть, из них читается
 * спецификатор) и `masked` (содержимое строк заменено на `\0`, по нему ищутся ключевые слова, чтобы
 * `import` внутри текста - `path="/employees/import"` - не принимался за оператор). Посимвольный
 * разбор, как в `browserClockGuard.test.ts`: маркер комментария внутри строки не срезает код. */
function stripComments(source: string): { plain: string; masked: string } {
  let plain = "";
  let masked = "";
  const put = (text: string, hidden = false) => {
    plain += text;
    masked += hidden ? "\0".repeat(text.length) : text;
  };
  let i = 0;
  while (i < source.length) {
    const c = source[i];
    const next = source[i + 1];
    if (c === "/" && next === "/") {
      while (i < source.length && source[i] !== "\n") i += 1;
    } else if (c === "/" && next === "*") {
      const end = source.indexOf("*/", i + 2);
      i = end === -1 ? source.length : end + 2;
    } else if (c === "'" || c === '"' || c === "`") {
      put(c);
      i += 1;
      while (i < source.length && source[i] !== c && (c === "`" || source[i] !== "\n")) {
        const step = source[i] === "\\" ? 2 : 1;
        put(source.slice(i, i + step), true);
        i += step;
      }
      put(c);
      i += 1;
    } else {
      put(c);
      i += 1;
    }
  }
  return { plain, masked };
}

/** Имя пакета по спецификатору; `null` - не пакет (относительный путь, `node:`, абсолютный). */
function packageOf(specifier: string): string | null {
  if (/^(?:\.|\/|node:)/.test(specifier)) return null;
  const parts = specifier.split("/");
  return specifier.startsWith("@") ? parts.slice(0, 2).join("/") : parts[0];
}

/** Имена пакетов, которые импортирует исходник: `import ... from "x"`, `export ... from "x"`,
 * `import "x"` (в том числе `import type`). */
function importedPackages(source: string): string[] {
  const { plain, masked } = stripComments(source);
  const patterns = [
    /\b(?:import|export)\b[^'";`]*?\bfrom\s*["']\0+["']/g,
    /\bimport\s*["']\0+["']/g,
  ];
  const specifiers = patterns.flatMap((pattern) =>
    [...masked.matchAll(pattern)].map((m) => {
      // Содержимое строки в `masked` - только `\0`, поэтому открывающая кавычка - последняя такая же до закрывающей.
      const close = m[0].length - 1;
      const open = m[0].lastIndexOf(m[0][close], close - 1);
      return plain.slice((m.index ?? 0) + open + 1, (m.index ?? 0) + close);
    }),
  );
  return specifiers.flatMap((specifier) => packageOf(specifier) ?? []);
}

function violations(
  files: Record<string, string>,
  manifest: Manifest,
  allowed: Record<string, string>,
): string[] {
  const runtime = new Set(Object.keys(manifest.dependencies ?? {}));
  const tooling = new Set(Object.keys(manifest.devDependencies ?? {}));
  const found: string[] = [];
  for (const [path, source] of Object.entries(files)) {
    const testLevel = isTestLevel(path);
    for (const name of new Set(importedPackages(source))) {
      if (name in allowed && allowed[name].trim() !== "") continue;
      if (runtime.has(name)) continue;
      if (testLevel && tooling.has(name)) continue;
      const kind = testLevel ? "теста" : "приложения";
      const why = tooling.has(name)
        ? "только в devDependencies, а файл"
        : "нет ни в dependencies, ни в devDependencies, файл";
      found.push(`${path}: ${name} (${why} ${kind})`);
    }
  }
  return found.sort();
}

/** Пути записей замка, чей пакет называется `name` (`node_modules/x` и вложенные `.../node_modules/x`). */
function lockCopies(packages: Record<string, unknown>, name: string): string[] {
  return Object.keys(packages).filter(
    (key) => key.slice(key.lastIndexOf("node_modules/") + "node_modules/".length) === name &&
      key.includes("node_modules/"),
  );
}

function duplicatedRuntimeDependencies(manifest: Manifest, packages: Record<string, unknown>): string[] {
  return Object.keys(manifest.dependencies ?? {})
    .map((name) => [name, lockCopies(packages, name)] as const)
    .filter(([, copies]) => copies.length !== 1)
    .map(([name, copies]) => `${name}: ${copies.length} копий (${copies.join(", ") || "нет"})`);
}

const FILES = Object.fromEntries(Object.entries(SOURCES).map(([key, text]) => [address(key), text]));

describe("dependency declaration guard (T-M2-35)", () => {
  it("scans the application and test sources, not an empty set", () => {
    expect(Object.keys(FILES).length).toBeGreaterThanOrEqual(MEASURED_FILES);
  });

  it("reduces specifiers to package names and skips relative and built-in ones", () => {
    const source = [
      'import a from "react";',
      'import { b } from "antd/es/table";',
      'import type { C } from "@tanstack/react-query";',
      'import "@testing-library/jest-dom/vitest";',
      'export { d } from "dayjs";',
      'import e from "./local";',
      'import f from "../up/x";',
      'import g from "node:fs";',
      'import {\n  h,\n  i,\n} from "react-dom/client";',
      '// import j from "commented";',
      '/* import k from "blocked"; */',
    ].join("\n");

    expect(importedPackages(source)).toEqual([
      "react",
      "antd",
      "@tanstack/react-query",
      "dayjs",
      "react-dom",
      "@testing-library/jest-dom",
    ]);
  });

  it("does not take the word import inside a string or a comment for an import statement", () => {
    expect(importedPackages('<Route path="/employees/import" label="import \'x\'" />')).toEqual([]);
    expect(importedPackages('const t = "import a from \'dayjs\'";')).toEqual([]);
  });

  it("every import is declared, and application files import only from dependencies", () => {
    expect(violations(FILES, MANIFEST, ALLOWED)).toEqual([]);
  });

  it("flags an undeclared package, a devDependency in the application and a made-up package", () => {
    const manifest: Manifest = { dependencies: { react: "^18" }, devDependencies: { vitest: "^5" } };
    const files = {
      "pages/A.tsx": 'import dayjs from "dayjs"; import { it } from "vitest"; import x from "no-such-pkg";',
      "pages/A.test.tsx": 'import { it } from "vitest"; import y from "dayjs";',
    };

    expect(violations(files, manifest, {})).toEqual([
      "pages/A.test.tsx: dayjs (нет ни в dependencies, ни в devDependencies, файл теста)",
      "pages/A.tsx: dayjs (нет ни в dependencies, ни в devDependencies, файл приложения)",
      "pages/A.tsx: no-such-pkg (нет ни в dependencies, ни в devDependencies, файл приложения)",
      "pages/A.tsx: vitest (только в devDependencies, а файл приложения)",
    ]);
  });

  it("the allowed list holds no stale package: each listed package is still imported undeclared", () => {
    const undeclared = new Set(
      Object.entries(FILES).flatMap(([, source]) => importedPackages(source)),
    );
    const declared = new Set(Object.keys(MANIFEST.dependencies ?? {}));
    const stale = Object.keys(ALLOWED).filter((name) => !undeclared.has(name) || declared.has(name));

    expect(stale).toEqual([]);
    expect(Object.values(ALLOWED).filter((reason) => reason.trim() === "")).toEqual([]);
  });

  it("the lock holds exactly one copy of every declared dependency", () => {
    expect(duplicatedRuntimeDependencies(MANIFEST, LOCK.packages)).toEqual([]);
    expect(lockCopies(LOCK.packages, "dayjs")).toEqual(["node_modules/dayjs"]);
  });

  it("finds a second copy in a synthetic lock", () => {
    const packages = {
      "": {},
      "node_modules/dayjs": {},
      "node_modules/rc-picker/node_modules/dayjs": {},
      "node_modules/react": {},
    };

    expect(duplicatedRuntimeDependencies({ dependencies: { dayjs: "^1", react: "^18" } }, packages)).toEqual([
      "dayjs: 2 копий (node_modules/dayjs, node_modules/rc-picker/node_modules/dayjs)",
    ]);
  });
});

#!/usr/bin/env node
// T-M1-18: гейт версии Node, ограниченный ЭТИМ проектом (сравнивается только
// с требованием ЭТОГО репозитория — `.nvmrc`), а не общий `engine-strict` в
// `.npmrc`. `engine-strict=true` отказывает в установке при несоответствии
// `engines` ЛЮБОГО пакета в дереве зависимостей, не только корневого
// `package.json`, — то есть способен уронить `npm ci` в CI из-за чужого
// `package.json` (например, будущей минорной версии какой-нибудь
// транзитивной зависимости, ужесточившей свой `engines`), а не из-за версии
// Node у разработчика. Этот скрипт проверяет ровно наше условие.
//
// Источник требуемой версии — `.nvmrc` в корне репозитория, а не второе
// число здесь: `.nvmrc` хранит только МАЖОР (согласовано с CI,
// `node-version-file: .nvmrc` в `.github/workflows/ci.yml`), поэтому
// сравнение здесь — тоже по мажору, не полный диапазон `engines.node` из
// `package.json` (`^22.12.0 || ^24.0.0 || >=26.0.0`, точнее и рассчитан на
// другого потребителя — npm/редакторы). Разница на практике не имеет
// значения: `nvm use` в корне репозитория ставит ПОСЛЕДНИЙ доступный
// патч мажора, а не 22.0, и все текущие 22.x давно выше 22.12.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const nvmrcPath = join(scriptDir, "..", "..", ".nvmrc");

function fail(message) {
  console.error(message);
  process.exit(1);
}

let requiredMajor;
try {
  const nvmrcContent = readFileSync(nvmrcPath, "utf8").trim();
  requiredMajor = Number.parseInt(nvmrcContent, 10);
  if (Number.isNaN(requiredMajor)) {
    fail(`Не удалось разобрать версию Node из ${nvmrcPath}: содержимое "${nvmrcContent}" — не число.`);
  }
} catch (error) {
  fail(`Не удалось прочитать ${nvmrcPath}: ${error.message}`);
}

const actualVersion = process.versions.node;
const actualMajor = Number.parseInt(actualVersion.split(".")[0], 10);

if (actualMajor < requiredMajor) {
  fail(
    `Требуется Node ${requiredMajor}.x или новее (версия задана в .nvmrc в корне репозитория) — ` +
      `установлена ${actualVersion}. Переключите версию (например, "nvm use" из корня репозитория) ` +
      `и повторите установку.`,
  );
}

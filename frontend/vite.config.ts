import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { configDefaults } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/setupTests.ts'],
    // T-M1-24: Playwright E2E-спеки живут в `e2e/*.spec.ts` — тот же
    // суффикс, что у vitest-юнит-тестов, и дефолтный `exclude` vitest 5
    // (`configDefaults.exclude`) — это только `node_modules`/`.git`, он
    // их не отфильтровал бы. Явный список ЗАМЕНЯЕТ дефолт, а не
    // дополняет его (поведение vitest) — поэтому дефолт здесь
    // распакован явно, а не потерян молча.
    exclude: [...configDefaults.exclude, 'e2e/**'],
    // T-M1-19 review raised this to 15000 (from the vitest default 5000)
    // to paper over CPU contention under parallel-file runs, rather than
    // removing the contention - T-M1-54 reversed that: `fileParallelism:
    // false` below removes the contention this was compensating for, so
    // the timeout goes back to the value these pages were actually
    // written against. Measured, not assumed (T-M1-54): on a
    // `--cpuset-cpus=0,1` container (`os.availableParallelism() === 2`,
    // matching the ubuntu-latest CI runner's own `nproc`/
    // `availableParallelism()`, confirmed from an actual CI run on this
    // branch), 10/10 `npm run test -- --run` passed at this value with
    // `fileParallelism: false`, vs. 6/10 FAILED (clustered right at this
    // timeout's boundary, ~5000-5150ms) with parallelism left on and
    // `--maxWorkers=2` instead - contention survives capping worker
    // COUNT, because file-level parallelism still schedules multiple
    // files' renders onto those same 2 cores at once; only turning
    // parallelism off removes it. Full raw numbers in the commit body.
    testTimeout: 5000,
    // See testTimeout's comment: this is what running test FILES one at
    // a time, instead of in parallel, buys back - the same CPU-
    // contention class of flake without needing a wider timeout window
    // to paper over it. ~61-64s per full local run on 2 measured cores,
    // not meaningfully slower than the parallel run it replaces (~60-
    // 62s) on the same hardware - the parallelism wasn't buying
    // wall-clock time on a 2-core runner to begin with. On an 8-core
    // machine it is a real cost: ~42s sequential vs. ~15-20s parallel
    // here (see commit body) - a deliberate, accepted trade for a single
    // run mode.
    //
    // Отклонённая альтернатива: `fileParallelism: false` только в CI
    // (через переменную окружения/CLI-флаг), параллельно локально -
    // отклонена, потому что именно это расхождение между локальным
    // прогоном и CI и было ценой двух кругов правок (PR #153): флак,
    // видимый только на раннере и не воспроизводимый у разработчика.
    // Единый режим для обоих стоит дороже по времени на многоядерной
    // машине разработчика, но именно он делает воспроизводимость
    // возможной без догадок о чужом железе - по аналогии с отклонённой
    // альтернативой в тексте `T-M1-44`.
    //
    // Измерение привязано к мажору Node 22 (`.nvmrc`); точный patch-релиз
    // образа `node:22` не зафиксирован (в отличие от digest-закрепления
    // `T-M1-43`'s `constraints.txt`-генератора) - при смене мажора Node
    // измерение придётся повторить.
    fileParallelism: false,
  },
})

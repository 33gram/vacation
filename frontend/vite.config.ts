/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/setupTests.ts'],
    // T-M1-19 review: the per-test default (5000ms) is separate from
    // `@testing-library/dom`'s own `asyncUtilTimeout` (raised in
    // setupTests.ts) - it caps the WHOLE test, not a single `waitFor`/
    // `findBy*` call, and a test doing several sequential renders/user
    // events under the default parallel-file run can exceed it on CPU
    // contention alone even when no individual step hangs. Reproduced
    // directly: `EmployeeCreatePage.test.tsx`'s checklist test timed out
    // here after this round's list-page changes added more concurrent
    // rendering, still green with `--no-file-parallelism`.
    testTimeout: 15000,
  },
})

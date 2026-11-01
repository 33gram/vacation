import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // PR #186: a variable destructured NEXT TO a rest property (`const { code,
      // ...rest } = values`) is how a field is left out of a copy; it is not
      // "unused". Only that case: checked with a probe, an ordinary unused
      // variable and an unused import stay errors with this flag on.
      '@typescript-eslint/no-unused-vars': [
        'error',
        { ignoreRestSiblings: true },
      ],
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
    },
  },
  // T-M1-24: e2e/ runs under Node (Playwright test runner), not the
  // browser - `process.env`/top-level `fetch` are Node globals there,
  // not DOM ones; the block above's `globals.browser` wouldn't know them.
  {
    files: ['e2e/**/*.ts'],
    languageOptions: {
      globals: globals.node,
    },
  },
)

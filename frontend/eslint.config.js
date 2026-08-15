// Flat ESLint config for the dashboard (ESLint 9 + typescript-eslint 8).
//
// Philosophy mirrors the backend Ruff setup: adopt linting ADDITIVELY. We extend
// typescript-eslint's (non-type-checked) `recommended` preset and the React Hooks
// rules, but "grandfather" the specific rules the existing sources already trip so
// `eslint .` exits 0 today WITHOUT mass source edits. Each relaxation is annotated;
// they can be tightened one at a time in dedicated cleanup PRs.
import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'

export default tseslint.config(
  // Build output, Playwright's HTML report and saved auth state are generated, not source.
  { ignores: ['dist', 'e2e/report', 'e2e/.auth'] },

  js.configs.recommended,
  ...tseslint.configs.recommended,

  {
    files: ['**/*.{ts,tsx}'],
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Fast-refresh hint only (a warning never fails the gate).
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],

      // ── Grandfathered rules (relaxed to keep the existing tree green) ─────────
      // This dashboard predates linting and leans on `any` for loosely-typed API
      // payloads; these are the codes the current sources actually trip. Downgraded
      // to "off"/"warn" so they don't block CI; tighten incrementally later.
      '@typescript-eslint/no-explicit-any': 'off',        // pervasive on API payloads
      '@typescript-eslint/no-unused-vars': 'warn',        // import/var hygiene (non-blocking)
      '@typescript-eslint/ban-ts-comment': 'off',         // a few pragmatic @ts- pragmas
      '@typescript-eslint/no-empty-object-type': 'off',   // `{}`-style prop bags
      // The tree uses `cond ? a() : b()` and `cond && fn()` as statements; allow those
      // intentional idioms while still flagging genuinely dead expressions.
      '@typescript-eslint/no-unused-expressions': ['error', {
        allowShortCircuit: true,
        allowTernary: true,
        allowTaggedTemplates: true,
      }],
      'react-hooks/exhaustive-deps': 'warn',              // intentional dep omissions exist
      'prefer-const': 'warn',
      'no-empty': 'warn',
    },
  },
)

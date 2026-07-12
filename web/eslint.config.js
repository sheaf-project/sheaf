import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // Timezone guard: dates must render through the blessed formatters
      // (useDateFormatters / formatDate / formatDateTime in @/lib/date-format)
      // so they honour the user's timezone preference and carry a zone stamp.
      // Raw toLocale* on a date silently renders in the browser's zone and
      // dodges the preference - see lib/date-format.ts. Numeric
      // `n.toLocaleString()` is unaffected (only the Date form is caught).
      'no-restricted-syntax': [
        'error',
        {
          selector: "CallExpression[callee.property.name='toLocaleDateString']",
          message:
            'Do not format dates with toLocaleDateString (ignores the timezone preference). Use formatDate from useDateFormatters()/@/lib/date-format.',
        },
        {
          selector: "CallExpression[callee.property.name='toLocaleTimeString']",
          message:
            'Do not format times with toLocaleTimeString (ignores the timezone preference). Use formatDateTime from useDateFormatters()/@/lib/date-format.',
        },
        {
          selector:
            "CallExpression[callee.property.name='toLocaleString'][callee.object.callee.name='Date']",
          message:
            'Do not format a Date with toLocaleString (ignores the timezone preference). Use formatDateTime from useDateFormatters()/@/lib/date-format. (Numeric toLocaleString is fine.)',
        },
      ],
    },
  },
  // shadcn/ui components export variant helpers alongside components
  {
    files: ['src/components/ui/**/*.{ts,tsx}'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
  // Context files export both provider and context
  {
    files: ['src/contexts/**/*.{ts,tsx}'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
])

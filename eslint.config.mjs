import { defineConfig, globalIgnores } from 'eslint/config'
import nextVitals from 'eslint-config-next/core-web-vitals'
import prettier from 'eslint-config-prettier/flat'

export default defineConfig([
  ...nextVitals,
  prettier,
  {
    rules: {
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/static-components': 'off',
    },
  },
  globalIgnores(['.next/**', 'out/**', 'biliup/web/public/**', 'docs/**', 'next-env.d.ts']),
])

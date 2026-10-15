import path from 'node:path'

import { loadConfigFromFile, mergeConfig } from 'vite'
import { configDefaults, defineConfig } from 'vitest/config'

/** A test needing neither a DOM nor the shared setup — see the `projects` note below. */
const NODE_TEST_GLOB = 'src/**/*.node.{test,spec}.{ts,tsx}'

/** Extends **`vite.config.ts`** so Vitest resolves the same **`@`**, **`loadEnv`**, and plugins Tailwind/ssl as **`pnpm dev`/`build`** (see **`web/README`**). */
export default defineConfig(async () => {
  const loaded = await loadConfigFromFile(
    { command: 'serve', mode: 'test' },
    path.resolve(import.meta.dirname, 'vite.config.ts'),
  )
  if (loaded === null) {
    throw new Error('Failed to load vite.config.ts for Vitest')
  }
  return mergeConfig(loaded.config, {
    test: {
      pool: 'threads',
      isolate: true,
      coverage: {
        provider: 'v8',
        reporter: ['text', 'lcov'],
      },
      // Standing up jsdom and `src/test/setup.ts` costs more than most test files spend running, so
      // the ones that touch neither say so in their name and get a bare Node environment. The two
      // projects partition the suite on the same list — `NODE_TEST_GLOB` is what one includes and the
      // other excludes — because a file matching neither would simply not run.
      projects: [
        {
          extends: true,
          test: {
            name: 'node',
            environment: 'node',
            // `extends` inherits the root config, setup files included, so emptying this is what
            // buys the saving rather than a detail of tidiness.
            setupFiles: [],
            include: [NODE_TEST_GLOB],
          },
        },
        {
          extends: true,
          test: {
            name: 'jsdom',
            environment: 'jsdom',
            setupFiles: ['./src/test/setup.ts'],
            include: ['src/**/*.{test,spec}.{ts,tsx}'],
            // Spread rather than replaced: a bare `exclude` drops Vitest's own defaults.
            exclude: [...configDefaults.exclude, NODE_TEST_GLOB],
            // Suppresses Node's own Web Storage so jsdom's `localStorage` is the one in scope.
            execArgv: ['--no-webstorage'],
          },
        },
      ],
    },
  })
})

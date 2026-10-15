import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

import '@testing-library/jest-dom/vitest'
import i18n, { i18nReady } from '@/i18n/i18n.ts'

await i18nReady
// Tests assert on the English copy whatever the machine's locale.
await i18n.changeLanguage('en')

afterEach(() => {
  cleanup()
})

import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

import '@testing-library/jest-dom/vitest'
import i18n, { i18nReady } from '@/i18n/i18n.ts'

await i18nReady
// Tests assert on the English copy whatever the machine's locale.
await i18n.changeLanguage('en')

// jsdom has `<dialog>` but not its methods.
Object.assign(HTMLDialogElement.prototype, {
  showModal(this: HTMLDialogElement) {
    this.open = true
  },
  close(this: HTMLDialogElement) {
    this.open = false
  },
})

afterEach(() => {
  cleanup()
  // What a screen keeps in the browser (the folder tree's open levels) does not leak into the next test.
  window.localStorage.clear()
})

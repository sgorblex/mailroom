import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

import '@testing-library/jest-dom/vitest'
import i18n, { i18nReady } from '@/i18n/i18n.ts'

await i18nReady
// Tests assert on the English copy whatever the machine's locale.
await i18n.changeLanguage('en')

// jsdom has `<dialog>` but not its methods. As a browser does, opening it focuses the first thing in
// it that takes focus.
Object.assign(HTMLDialogElement.prototype, {
  showModal(this: HTMLDialogElement) {
    this.open = true
    this.querySelector<HTMLElement>('input, select, textarea, button, a[href], [tabindex]:not([tabindex="-1"])')?.focus()
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

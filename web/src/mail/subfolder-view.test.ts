import { afterEach, describe, expect, it } from 'vitest'

import { forgetStoredSubfolders, renameStoredSubfolders, storedSubfolders, storeSubfolders } from './subfolder-view.ts'

describe('the subfolder choice kept in this browser', () => {
  afterEach(() => {
    window.localStorage.clear()
  })

  it('follows a renamed folder and the folders under it, and is forgotten with a deleted one', () => {
    storeSubfolders('work', 'Lists', true)
    storeSubfolders('work', 'Lists/Rust', true)
    storeSubfolders('work', 'Listsmore', true)

    renameStoredSubfolders('work', 'Lists', 'Mailing', '/')
    expect(storedSubfolders('work', 'Lists')).toBe(false)
    expect(storedSubfolders('work', 'Mailing')).toBe(true)
    expect(storedSubfolders('work', 'Mailing/Rust')).toBe(true)
    expect(storedSubfolders('work', 'Listsmore')).toBe(true)

    forgetStoredSubfolders('work', 'Mailing', '/')
    expect(storedSubfolders('work', 'Mailing')).toBe(false)
    expect(storedSubfolders('work', 'Mailing/Rust')).toBe(false)
    expect(storedSubfolders('work', 'Listsmore')).toBe(true)
  })
})

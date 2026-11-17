import { afterEach, describe, expect, it } from 'vitest'

import { forgetStoredSubfolders, renameStoredSubfolders, storedSubfolders, storeSubfolders } from './subfolder-view.ts'

describe('the subfolder choice kept in this browser', () => {
  afterEach(() => {
    window.localStorage.clear()
  })

  it('follows a renamed folder and the folders under it, and is forgotten with a deleted one', () => {
    storeSubfolders(1, 'Lists', true)
    storeSubfolders(1, 'Lists/Rust', true)
    storeSubfolders(1, 'Listsmore', true)

    renameStoredSubfolders(1, 'Lists', 'Mailing', '/')
    expect(storedSubfolders(1, 'Lists')).toBe(false)
    expect(storedSubfolders(1, 'Mailing')).toBe(true)
    expect(storedSubfolders(1, 'Mailing/Rust')).toBe(true)
    expect(storedSubfolders(1, 'Listsmore')).toBe(true)

    forgetStoredSubfolders(1, 'Mailing', '/')
    expect(storedSubfolders(1, 'Mailing')).toBe(false)
    expect(storedSubfolders(1, 'Mailing/Rust')).toBe(false)
    expect(storedSubfolders(1, 'Listsmore')).toBe(true)
  })
})

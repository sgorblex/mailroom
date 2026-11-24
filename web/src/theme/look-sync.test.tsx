import { describe, expect, it } from 'vitest'

import { DEFAULT_APPEARANCE } from '@/api/auth.ts'
import { renderSignedIn } from '@/test/signed-in.tsx'

import { LookSync } from './look-sync.tsx'
import { startLook } from './look.ts'

describe('LookSync', () => {
  it('replaces this browser’s copy with the look saved on the server', () => {
    window.localStorage.setItem('mailroom.ui.look', JSON.stringify({ theme: 'light', accent: 'blue', text_size: 'small' }))
    const stop = startLook()
    expect(document.documentElement.dataset['accent']).toBe('blue')

    renderSignedIn(<LookSync />, {
      username: 'alice',
      is_admin: false,
      time_zone: null,
      mark_read_subfolders: true,
      index_max_age_days: null,
      conversations: true,
      undo_send_seconds: 10,
      appearance: { ...DEFAULT_APPEARANCE, theme: 'dark', accent: 'green', text_size: 'large' },
    })
    const root = document.documentElement
    expect(root).toHaveClass('dark')
    expect(root.dataset['accent']).toBe('green')
    expect(root.dataset['textSize']).toBe('large')
    expect(JSON.parse(window.localStorage.getItem('mailroom.ui.look') ?? 'null')).toEqual({ theme: 'dark', accent: 'green', text_size: 'large' })
    stop()
  })
})

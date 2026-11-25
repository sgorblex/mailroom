import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { appearanceSchema, DEFAULT_APPEARANCE, type RowAction, type UserJson, withRowAction } from '@/api/auth.ts'
import { bodiesTo, type FakeMailState, HOME, messageItem, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const inbox = Array.from({ length: 3 }, (_, index) => messageItem(WORK, 'INBOX', 10 - index))
const second = inbox[1]

function state(extra: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 3 },
        { path: 'Archive', role: 'archive', unread: 0, total: 0 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 0 }],
    },
    messages: { '1/INBOX': inbox },
    views: {},
    ...extra,
  }
}

const alice = (row_actions: RowAction[]): UserJson => ({
  username: 'alice',
  is_admin: false,
  time_zone: null,
  mark_read_subfolders: true,
  index_max_age_days: null,
  conversations: true,
  undo_send_seconds: 10,
  appearance: { ...DEFAULT_APPEARANCE, row_actions },
})

async function row(): Promise<HTMLElement> {
  const link = await screen.findByRole('link', { name: /Message 9Preview/ })
  const item = link.closest('li')
  if (item === null) {
    throw new Error('no row')
  }
  return item
}

const names = (item: HTMLElement): (string | null)[] =>
  within(item)
    .queryAllByRole('button')
    .map((button) => button.getAttribute('aria-label'))

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('row_actions as read', () => {
  it('are in one order, each once, the default where one is unknown or they are too many', () => {
    const read = (row_actions: unknown): RowAction[] => appearanceSchema.parse({ row_actions }).row_actions
    expect(read(undefined)).toEqual(['star', 'handled'])
    expect(read(['trash', 'read', 'trash'])).toEqual(['read', 'trash'])
    expect(read([])).toEqual([])
    expect(read(['star', 'future'])).toEqual(['star', 'handled'])
    expect(read(['star', 'read', 'trash', 'move'])).toEqual(['star', 'handled'])
    expect(withRowAction(['trash'], 'read', true)).toEqual(['read', 'trash'])
    expect(withRowAction(['read', 'trash'], 'read', false)).toEqual(['trash'])
  })
})

describe("a row's actions", () => {
  it('are the chosen ones, acting on that message alone, opening nothing', async () => {
    const fetchMock = stubMailApi(state())
    const history = renderMailApp('/mail', { user: alice(['read', 'archive', 'snooze']) })
    const item = await row()
    await waitFor(() => {
      expect(names(item)).toEqual([expect.stringMatching(/^Mark as (un)?read$/), 'Archive', 'Snooze'])
    })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Message 8' }))
    await userEvent.click(within(item).getByRole('button', { name: 'Archive' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/archive')).toEqual([{ refs: [second?.ref], source_folder: 'INBOX' }])
    })
    expect(history.location.search).not.toContain('message=')
  })

  it('open the picker of Move to… on the row, and ask when to snooze it', async () => {
    stubMailApi(state())
    renderMailApp('/mail', { user: alice(['move', 'snooze']) })
    const item = await row()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Message 8' }))
    await userEvent.click(await within(item).findByRole('button', { name: 'Move to…' }))
    expect(screen.getByRole('checkbox', { name: 'Select Message 9' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Select Message 8' })).not.toBeChecked()
    expect(await screen.findByRole('dialog', { name: 'Move to' })).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    await userEvent.click(within(item).getByRole('button', { name: 'Snooze' }))
    expect(await screen.findByRole('dialog', { name: /Snooze/ })).toBeInTheDocument()
  })

  it('leave out what the row cannot do, and grey out, saying why, what it cannot do yet', async () => {
    const noArchive = state({ keywords: { [WORK]: false } })
    noArchive.folders[WORK] = noArchive.folders[WORK]?.filter((folder) => folder.role !== 'archive') ?? []
    const fetchMock = stubMailApi(noArchive)
    renderMailApp('/mail', { user: alice(['handled', 'archive', 'mute']) })
    const item = await row()
    // Muting is a hidden filter's: not where the instance runs none.
    expect(names(item)).toEqual(['Handled'])
    const handled = within(item).getByRole('button', { name: 'Handled' })
    await waitFor(() => {
      expect(handled).toHaveAttribute('aria-disabled', 'true')
    })
    expect(handled.title).toMatch(/^Mark Handled: /)
    await userEvent.click(handled)
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toEqual([])
  })

  it('show the star for Handled where the account keeps it as the star', async () => {
    stubMailApi(state({ handled: { [WORK]: 'star' } }))
    renderMailApp('/mail', { user: alice(['handled']) })
    const item = await row()
    await waitFor(() => {
      expect(names(item)).toEqual(['Star'])
    })
    expect(within(item).getByRole('button')).toHaveAttribute('title', "Star (the star is this account's Handled mark)")
  })

  it('are none, with nothing over the date, when none is chosen; the context menu has them all', async () => {
    stubMailApi(state())
    renderMailApp('/mail', { user: alice([]) })
    const item = await row()
    expect(names(item)).toEqual([])
    expect(item.querySelector('[data-row-actions]')).toBeNull()
    await userEvent.pointer({ keys: '[MouseRight]', target: within(item).getByRole('link') })
    for (const name of ['Star', 'Mark Handled', 'Archive', 'Snooze']) {
      expect(await screen.findByRole('menuitem', { name })).toBeInTheDocument()
    }
  })
})

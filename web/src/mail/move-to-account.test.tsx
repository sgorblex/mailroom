import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { bodiesTo, type FakeMailState, HOME, messageItem, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const UNREAD = { seen: false, flagged: false, answered: false, forwarded: false, draft: false, to_sort: false }
const inbox = Array.from({ length: 3 }, (_, index) => messageItem(WORK, 'INBOX', 10 - index, { flags: UNREAD }))
const [, , third] = inbox
const MOVE_TO_ACCOUNT = '/api/v1/mail/messages/move-to-account'

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK, HOME],
    // Named: the web shows names, never ids.
    names: { [WORK]: { name: 'Work', colour: 'blue' }, [HOME]: { name: 'Home', colour: 'pink' } },
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 3, total: 3 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
      ],
      [HOME]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 1 },
        { path: 'Drafts', role: 'drafts', unread: 0, total: 0 },
        { path: 'Sent', role: 'sent', unread: 0, total: 0 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
      ],
    },
    messages: {
      '1/INBOX': inbox,
      '2/INBOX': [messageItem(HOME, 'INBOX', 1, { subject: 'From home' })],
    },
    views: {},
    ...overrides,
  }
}

const messagesList = (): Promise<HTMLElement> => screen.findByRole('list', { name: 'Messages' })
const actions = (): Promise<HTMLElement> => screen.findByRole('group', { name: 'Message actions' })

async function reported(live: 'polite' | 'assertive', text: string): Promise<void> {
  await waitFor(() => {
    expect(document.getElementById(live === 'polite' ? 'toast-status' : 'triage-outcome-alert')).toHaveTextContent(text)
  })
}

/** Selects Message 8 and opens the picker on Home's folders. */
async function pickHome(): Promise<HTMLElement> {
  const list = await messagesList()
  await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
  const button = within(await actions()).getByRole('button', { name: 'Move to another account…' })
  await userEvent.click(button)
  expect(button).toHaveAttribute('aria-expanded', 'true')
  // One other account: straight to its folders.
  const picker = screen.getByRole('dialog', { name: 'Move to another account' })
  expect(within(picker).getByText('Move to a folder of Home')).toBeInTheDocument()
  await within(picker).findByRole('button', { name: 'Inbox' })
  expect(within(picker).getByRole('radio', { name: 'go to Trash here' })).toBeChecked()
  return picker
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('moving to another account', () => {
  it('moves the selection to a folder of another account, without Drafts or Sent, its originals to Trash', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const picker = await pickHome()
    expect(within(picker).getAllByRole('button').map((button) => button.textContent)).toEqual(['Inbox', 'Trash'])
    await userEvent.click(within(picker).getByRole('button', { name: 'Inbox' }))

    await reported('polite', 'Moved 1 message to Inbox in Home.')
    expect(bodiesTo(fetchMock, MOVE_TO_ACCOUNT)).toEqual([
      { refs: [third?.ref], account: HOME, folder: 'INBOX', removal: 'trash' },
    ])
    expect(within(await messagesList()).queryByRole('link', { name: /Message 8Preview/ })).not.toBeInTheDocument()
  })

  it('counts the originals into Trash, and closes on Escape with focus back on its button', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    let picker = await pickHome()
    await userEvent.keyboard('{Escape}')
    expect(picker).not.toBeInTheDocument()
    const button = within(await actions()).getByRole('button', { name: 'Move to another account…' })
    expect(button).toHaveFocus()
    // Escape closed the picker only: the selection is still there.
    expect(await actions()).toHaveTextContent('1 selected')

    await userEvent.click(button)
    picker = screen.getByRole('dialog', { name: 'Move to another account' })
    await userEvent.click(await within(picker).findByRole('button', { name: 'Inbox' }))
    await reported('polite', 'Moved 1 message to Inbox in Home.')
    const folders = screen.getByRole('tree', { name: 'Folders of Work' })
    expect(within(folders).getByRole('treeitem', { name: 'Inbox, 2 unread' })).toBeInTheDocument()
    expect(within(folders).getByRole('treeitem', { name: 'Trash, 1 unread' })).toBeInTheDocument()
  })

  it('asks before deleting the originals for good, and does nothing on Cancel', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    let picker = await pickHome()
    await userEvent.click(within(picker).getByRole('radio', { name: 'are deleted for good' }))
    await userEvent.click(within(picker).getByRole('button', { name: 'Inbox' }))
    let question = screen.getByRole('dialog', { name: 'Delete the originals for good?' })
    expect(question).toHaveTextContent(
      '1 message will be copied to Inbox in Home, then deleted for good from Work. This cannot be undone.',
    )
    await userEvent.click(within(question).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog', { name: 'Delete the originals for good?' })).not.toBeInTheDocument()
    expect(bodiesTo(fetchMock, MOVE_TO_ACCOUNT)).toEqual([])

    await userEvent.click(within(await actions()).getByRole('button', { name: 'Move to another account…' }))
    picker = screen.getByRole('dialog', { name: 'Move to another account' })
    await userEvent.click(within(picker).getByRole('radio', { name: 'are deleted for good' }))
    await userEvent.click(await within(picker).findByRole('button', { name: 'Inbox' }))
    question = screen.getByRole('dialog', { name: 'Delete the originals for good?' })
    await userEvent.click(within(question).getByRole('button', { name: 'Move and delete the original' }))

    await reported('polite', 'Moved 1 message to Inbox in Home, and deleted the original for good.')
    expect(bodiesTo(fetchMock, MOVE_TO_ACCOUNT)).toEqual([
      { refs: [third?.ref], account: HOME, folder: 'INBOX', removal: 'delete' },
    ])
  })

  it('says when messages were copied but not removed, so they are in both', async () => {
    stubMailApi(state({ copiedNotRemoved: { '1/INBOX': 'server_error' } }))
    renderMailApp('/mail')

    const picker = await pickHome()
    await userEvent.click(within(picker).getByRole('button', { name: 'Inbox' }))

    await reported(
      'assertive',
      'Work, Inbox: copied to Home but not removed here, so it is in both: do not move it again',
    )
    expect(within(await messagesList()).getByRole('link', { name: /Message 8Preview/ })).toBeInTheDocument()
  })

  it('is not offered with a single account, or when the selection spans every account', async () => {
    stubMailApi(state({ accounts: [WORK] }))
    renderMailApp('/mail')
    const list = await messagesList()
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
    expect(within(await actions()).queryByRole('button', { name: 'Move to another account…' })).not.toBeInTheDocument()
    cleanup()

    const results = {
      messages: [messageItem(HOME, 'INBOX', 1, { subject: 'From home' }), messageItem(WORK, 'INBOX', 9)],
      next_cursor: null,
      failures: [],
    }
    stubMailApi(state({ searchResult: results }))
    renderMailApp('/mail?q=%7B%7D')
    await screen.findByRole('list', { name: 'Search results' })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all' }))
    expect(within(await actions()).queryByRole('button', { name: 'Move to another account…' })).not.toBeInTheDocument()
  })
})

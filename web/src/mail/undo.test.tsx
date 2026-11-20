import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { bodiesTo, type FakeMailState, HOME, messageItem, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

import { TOAST_MS } from './toast.tsx'

const inbox = Array.from({ length: 3 }, (_, index) => messageItem(WORK, 'INBOX', 10 - index))
const [first] = inbox

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 3 },
        { path: 'Archive', role: 'archive', unread: 0, total: 0 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
        { path: 'Projects', role: null, unread: 0, total: 0 },
      ],
      [HOME]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 0 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
      ],
    },
    messages: { '1/INBOX': inbox },
    views: {},
    ...overrides,
  }
}

const MOVE = '/api/v1/mail/messages/move'
const FLAG = '/api/v1/mail/messages/flag'
const messagesList = (): Promise<HTMLElement> => screen.findByRole('list', { name: 'Messages' })
const actions = (): Promise<HTMLElement> => screen.findByRole('group', { name: 'Message actions' })
const undoButton = (): HTMLElement | null => screen.queryByRole('button', { name: 'Undo' })

function toast(live: 'polite' | 'assertive'): HTMLElement {
  const region = document.querySelector<HTMLElement>(`#${live === 'polite' ? 'toast-status' : 'toast-alert'}`)
  if (region === null) {
    throw new Error('no toast region')
  }
  return region
}

async function select(list: HTMLElement, subject: string): Promise<void> {
  await userEvent.click(within(list).getByRole('checkbox', { name: `Select ${subject}` }))
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('undo', () => {
  it('moves archived messages back to where they were, and reads the list again', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await select(list, 'Message 9')
    await select(list, 'Message 8')
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Archive' }))
    await waitFor(() => {
      expect(toast('polite')).toHaveTextContent('Archived 2 messages.')
    })
    expect(within(list).queryByRole('link', { name: /Message 9Preview/ })).not.toBeInTheDocument()
    const undo = screen.getByRole('button', { name: 'Undo' })
    expect(undo).toHaveAccessibleDescription('Archived 2 messages.')

    await userEvent.click(undo)
    await waitFor(() => {
      expect(toast('polite')).toHaveTextContent('Undone.')
    })
    // The refs the archive answered, back to the folder they came from.
    expect(bodiesTo(fetchMock, MOVE)).toEqual([
      { refs: ['ref-1-Archive-2', 'ref-1-Archive-1'], folder: 'INBOX' },
    ])
    expect(await within(list).findAllByRole('link', { name: /Message \d+Preview/ })).toHaveLength(3)
    expect(undoButton()).not.toBeInTheDocument()
  })

  it('sets back the flags a star changed, the refs being the same', async () => {
    const starred = messageItem(WORK, 'INBOX', 7)
    starred.flags = { ...starred.flags, flagged: true }
    const fetchMock = stubMailApi(state({ messages: { '1/INBOX': [...inbox, starred] } }))
    renderMailApp('/mail')

    const list = await messagesList()
    await select(list, 'Message 10')
    await select(list, 'Message 7')
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Star' }))
    await waitFor(() => {
      expect(toast('polite')).toHaveTextContent('Starred 2 messages.')
    })
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => {
      expect(toast('polite')).toHaveTextContent('Undone.')
    })
    // Message 7 was starred already: only Message 10 is unstarred.
    expect(bodiesTo(fetchMock, FLAG)).toEqual([
      { refs: [first?.ref, starred.ref], flagged: true },
      { refs: [first?.ref], flagged: false },
    ])
    const star = (subject: string): HTMLElement => {
      const row = within(list).getByRole('link', { name: new RegExp(`${subject}Preview`) }).closest('li')
      if (row === null) {
        throw new Error(`no row for ${subject}`)
      }
      return within(row).getByRole('button', { name: 'Star' })
    }
    expect(star('Message 10')).toHaveAttribute('aria-pressed', 'false')
    expect(star('Message 7')).toHaveAttribute('aria-pressed', 'true')
  })

  it('says it cannot be undone when the server gave no new refs', async () => {
    const fetchMock = stubMailApi(state({ noNewRefs: true }))
    renderMailApp('/mail')

    const list = await messagesList()
    await select(list, 'Message 9')
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Move to Trash' }))
    await waitFor(() => {
      expect(toast('polite')).toHaveTextContent(
        'Moved 1 message to Trash. It cannot be undone: the mail server did not say where the messages went.',
      )
    })
    expect(undoButton()).not.toBeInTheDocument()
    expect(bodiesTo(fetchMock, MOVE)).toEqual([])
  })

  it('offers no Undo for a move to another account', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await select(list, 'Message 9')
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Move to another account…' }))
    const picker = screen.getByRole('dialog', { name: 'Move to another account' })
    await userEvent.click(await within(picker).findByRole('button', { name: 'Inbox' }))
    await waitFor(() => {
      expect(toast('polite')).toHaveTextContent(/Moved 1 message to Inbox in/)
    })
    expect(undoButton()).not.toBeInTheDocument()
  })

  it('stays on show across a folder change', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await select(list, 'Message 9')
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Archive' }))
    expect(await screen.findByRole('button', { name: 'Undo' })).toBeInTheDocument()
    const folders = screen.getByRole('tree', { name: 'Folders of work' })
    await userEvent.click(within(folders).getByRole('treeitem', { name: 'Archive' }))
    expect(await screen.findByRole('list', { name: 'Messages' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument()
  })

  it('closes after eight seconds, the clock paused while the pointer or focus is on it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    const user = userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) })
    await user.click(within(list).getByRole('checkbox', { name: 'Select Message 9' }))
    await user.click(within(await actions()).getByRole('button', { name: 'Archive' }))
    const undo = await screen.findByRole('button', { name: 'Undo' })
    const card = undo.parentElement
    if (card === null) {
      throw new Error('no toast')
    }

    await act(() => vi.advanceTimersByTimeAsync(TOAST_MS / 2))
    fireEvent.mouseEnter(card)
    await act(() => vi.advanceTimersByTimeAsync(TOAST_MS))
    expect(undoButton()).toBeInTheDocument()
    fireEvent.mouseLeave(card)

    act(() => {
      undo.focus()
    })
    await act(() => vi.advanceTimersByTimeAsync(TOAST_MS))
    expect(undoButton()).toBeInTheDocument()
    act(() => {
      undo.blur()
    })

    await act(() => vi.advanceTimersByTimeAsync(TOAST_MS / 2 - 500))
    expect(undoButton()).toBeInTheDocument()
    await act(() => vi.advanceTimersByTimeAsync(1000))
    expect(undoButton()).not.toBeInTheDocument()
    expect(toast('polite')).toBeEmptyDOMElement()
  })

  it('says so when the undo fails', async () => {
    const fake = state()
    stubMailApi(fake)
    renderMailApp('/mail')

    const list = await messagesList()
    await select(list, 'Message 9')
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Archive' }))
    const undo = await screen.findByRole('button', { name: 'Undo' })
    fake.groupFailures = { '1/Archive': 'not_found' }
    await userEvent.click(undo)
    await waitFor(() => {
      expect(toast('assertive')).toHaveTextContent('Could not undo every change.')
    })
  })

  it('says so when the undo is refused', async () => {
    const fake = state()
    stubMailApi(fake)
    renderMailApp('/mail')

    const list = await messagesList()
    await select(list, 'Message 9')
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Archive' }))
    const undo = await screen.findByRole('button', { name: 'Undo' })
    fake.refuse = { [`POST ${MOVE}`]: { status: 502, body: { detail: { code: 'server_error', message: 'The mail server failed.' } } } }
    await userEvent.click(undo)
    await waitFor(() => {
      expect(toast('assertive')).toHaveTextContent('Could not undo every change.')
    })
  })

  it('says it cannot be undone when the server gave fewer new refs than it moved', async () => {
    stubMailApi(state({ shortNewRefs: true }))
    renderMailApp('/mail')

    const list = await messagesList()
    await select(list, 'Message 9')
    await select(list, 'Message 8')
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Archive' }))
    await waitFor(() => {
      expect(toast('polite')).toHaveTextContent(
        'Archived 2 messages. It cannot be undone: the mail server did not say where the messages went.',
      )
    })
    expect(undoButton()).not.toBeInTheDocument()
  })

  it('keeps focus on the page when Undo is chosen with the keyboard', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await select(list, 'Message 9')
    await userEvent.click(within(await actions()).getByRole('button', { name: 'Archive' }))
    const undo = await screen.findByRole('button', { name: 'Undo' })
    const before = within(list).getByRole('checkbox', { name: 'Select Message 10' })
    act(() => {
      before.focus()
    })
    act(() => {
      undo.focus()
    })
    await userEvent.keyboard('{Enter}')
    await waitFor(() => {
      expect(toast('polite')).toHaveTextContent('Undone.')
    })
    expect(document.activeElement).toBe(before)
  })
})

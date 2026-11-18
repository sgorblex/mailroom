import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { bodiesTo, type FakeMailState, HOME, messageItem, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

import { MESSAGES_TYPE } from './message-drag.ts'

const inbox = Array.from({ length: 3 }, (_, index) => messageItem(WORK, 'INBOX', 10 - index))
const [first, second, third] = inbox

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 3 },
        { path: 'Sent', role: 'sent', unread: 0, total: 0 },
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
const MOVE_TO_ACCOUNT = '/api/v1/mail/messages/move-to-account'

/** jsdom has no DataTransfer: what a drag carries, as the browser keeps it. */
function dataTransfer(): DataTransfer {
  const data = new Map<string, string>()
  return {
    get types() {
      return [...data.keys()]
    },
    setData: (type: string, value: string) => {
      data.set(type, value)
    },
    getData: (type: string) => data.get(type) ?? '',
    setDragImage: vi.fn(),
    effectAllowed: 'none',
    dropEffect: 'none',
  } as unknown as DataTransfer
}

function row(list: HTMLElement, subject: string): HTMLElement {
  const item = within(list).getByRole('link', { name: new RegExp(`${subject}Preview`) }).closest('li')
  if (item === null) {
    throw new Error(`no row for ${subject}`)
  }
  return item
}

async function folder(name: string): Promise<HTMLElement> {
  return within(await screen.findByRole('tree', { name: /^Folders of / })).findByRole('treeitem', { name })
}

/** Drags `source` over `target`; whether the target took it, as its dragover's default prevented. */
function dragOver(source: HTMLElement, target: HTMLElement, transfer: DataTransfer): boolean {
  fireEvent.dragStart(source, { dataTransfer: transfer })
  fireEvent.dragEnter(target, { dataTransfer: transfer })
  return !fireEvent.dragOver(target, { dataTransfer: transfer })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('dragging messages onto a folder', () => {
  it('moves the selection when a selected row is dragged, and offers Undo', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await screen.findByRole('list', { name: 'Messages' })
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 10' }))
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 8' }))
    const transfer = dataTransfer()
    const projects = await folder('Projects')
    expect(dragOver(row(list, 'Message 8'), projects, transfer)).toBe(true)
    expect(JSON.parse(transfer.getData(MESSAGES_TYPE))).toEqual([first?.ref, third?.ref])
    // Taken: highlighted while the drag is over it.
    expect(projects.parentElement).toHaveClass('ring-2')
    fireEvent.drop(projects, { dataTransfer: transfer })

    await waitFor(() => {
      expect(document.getElementById('toast-status')).toHaveTextContent('Moved 2 messages to Projects.')
    })
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument()
    expect(projects.parentElement).not.toHaveClass('ring-2')
    expect(bodiesTo(fetchMock, MOVE)).toEqual([{ refs: [first?.ref, third?.ref], folder: 'Projects' }])
  })

  it('moves an unselected row alone, and refuses its own folder and Sent', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await screen.findByRole('list', { name: 'Messages' })
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 10' }))
    const source = row(list, 'Message 9')
    expect(source).toHaveAttribute('draggable', 'true')
    // The row drags its messages, not its link's URL.
    expect(within(source).getByRole('link')).toHaveAttribute('draggable', 'false')

    expect(dragOver(source, await folder('Inbox'), dataTransfer())).toBe(false)
    expect(dragOver(source, await folder('Sent'), dataTransfer())).toBe(false)
    fireEvent.dragEnd(source)

    const transfer = dataTransfer()
    const trash = await folder('Trash')
    expect(dragOver(source, trash, transfer)).toBe(true)
    fireEvent.drop(trash, { dataTransfer: transfer })
    await waitFor(() => {
      expect(bodiesTo(fetchMock, MOVE)).toEqual([{ refs: [second?.ref], folder: 'Trash' }])
    })
  })

  it('refuses a level that holds only folders', async () => {
    const fake = state()
    fake.folders[WORK]?.push(
      { path: 'Clients', role: null, unread: 0, total: 0, selectable: false },
      { path: 'Clients/Acme', role: null, unread: 0, total: 0 },
    )
    stubMailApi(fake)
    renderMailApp('/mail')

    const list = await screen.findByRole('list', { name: 'Messages' })
    expect(dragOver(row(list, 'Message 9'), await folder('Clients'), dataTransfer())).toBe(false)
  })

  it('refuses a drag that carries no messages from this page', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const transfer = dataTransfer()
    transfer.setData('text/uri-list', 'https://example.org/')
    fireEvent.dragEnter(await folder('Projects'), { dataTransfer: transfer })
    expect(fireEvent.dragOver(await folder('Projects'), { dataTransfer: transfer })).toBe(true)
  })

  it('opens the account picker on a folder of another account, the originals going to Trash', async () => {
    const fromHome = messageItem(HOME, 'INBOX', 1, { subject: 'From home' })
    const fetchMock = stubMailApi(
      state({ searchResult: { messages: [fromHome, messageItem(WORK, 'INBOX', 9)], next_cursor: null, failures: [] } }),
    )
    renderMailApp('/mail?q=%7B%7D')

    const list = await screen.findByRole('list', { name: 'Search results' })
    const transfer = dataTransfer()
    const projects = await folder('Projects')
    expect(dragOver(row(list, 'From home'), projects, transfer)).toBe(true)
    fireEvent.drop(projects, { dataTransfer: transfer })

    const picker = await screen.findByRole('dialog', { name: 'Move to another account' })
    const target = await within(picker).findByRole('button', { name: 'Projects' })
    expect(target).toHaveFocus()
    expect(within(picker).getByRole('radio', { name: 'go to Trash here' })).toBeChecked()
    expect(bodiesTo(fetchMock, MOVE_TO_ACCOUNT)).toEqual([])
    // The row dragged alone is what the picker acts on.
    expect(screen.getByRole('group', { name: 'Message actions' })).toHaveTextContent('1 selected')

    await userEvent.click(target)
    await waitFor(() => {
      expect(bodiesTo(fetchMock, MOVE_TO_ACCOUNT)).toEqual([
        { refs: [fromHome.ref], account: WORK, folder: 'Projects', removal: 'trash' },
      ])
    })
  })
})

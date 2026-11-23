import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { bodiesTo, type FakeMailState, messageItem, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { scrollListToEnd } from '@/test/fake-virtualizer.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

// More than a page: the list has more to load.
const inbox = Array.from({ length: 30 }, (_, index) => messageItem(WORK, 'INBOX', 100 - index))

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 1200 },
        { path: 'Archive', role: 'archive', unread: 0, total: 0 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
        { path: 'Projects', role: null, unread: 0, total: 0 },
      ],
    },
    messages: { '1/INBOX': inbox },
    views: {},
    matching: { total: 1200 },
    ...overrides,
  }
}

const BULK = '/api/v1/mail/messages/bulk'
const COUNT = '/api/v1/mail/messages/count'
const selection = (): Promise<HTMLElement> => screen.findByRole('group', { name: 'Selected messages' })

/** Selects the loaded rows, then every message the folder matches. */
async function selectMatching(total = 1200): Promise<void> {
  await screen.findByRole('list', { name: 'Messages' })
  await userEvent.click(screen.getByRole('checkbox', { name: 'Select all' }))
  expect(screen.getByText('All 25 loaded messages are selected.')).toBeInTheDocument()
  await userEvent.click(await screen.findByRole('button', { name: `Select all ${String(total)} matching` }))
  expect(screen.getByText(`All ${String(total)} matching messages are selected.`)).toBeInTheDocument()
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('select all matching', () => {
  it('trashes every message the folder matches, 500 a call, then reads the list again', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    await selectMatching()
    const toolbar = await selection()
    expect(within(toolbar).getByText('All 1200 matching selected')).toBeInTheDocument()
    await userEvent.click(within(toolbar).getByRole('button', { name: 'Move to Trash' }))
    const dialog = await screen.findByRole('dialog', { name: 'Act on every matching message' })
    expect(
      await within(dialog).findByText(/^Move to Trash: 1200 matching messages, 500 at a time\./),
    ).toBeInTheDocument()
    const lists = (): number => fetchMock.mock.calls.filter(([path]) => String(path).includes('/accounts/1/messages')).length
    const before = lists()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Move to Trash' }))

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    const scope = { account: WORK, folder: 'INBOX' }
    expect(bodiesTo(fetchMock, COUNT)).toEqual([scope, { ...scope, action: { kind: 'trash' } }])
    expect(bodiesTo(fetchMock, BULK)).toEqual(
      [1200, 700, 200].map((expected) => ({ ...scope, action: { kind: 'trash' }, expected })),
    )
    expect(document.querySelector('#toast-status')).toHaveTextContent('Moved 1200 messages to Trash.')
    // The list is read again and nothing stays selected.
    await waitFor(() => {
      expect(lists()).toBeGreaterThan(before)
    })
    expect(screen.queryByText(/matching messages are selected/)).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Select all' })).not.toBeChecked()
  })

  it('greys out the actions that cannot act on every matching message', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    await selectMatching()
    const archive = within(await selection()).getByRole('button', { name: 'Archive' })
    expect(archive).toHaveAttribute('aria-disabled', 'true')
    expect(archive).toHaveAttribute(
      'title',
      'Archive (e): only Move, Trash, star, To sort and read marks act on every matching message',
    )
    await userEvent.keyboard('e')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('stars them, with the flag change as the action, and a key runs it too', async () => {
    const fetchMock = stubMailApi(state({ matching: { total: 40 } }))
    renderMailApp('/mail')

    await selectMatching(40)
    await userEvent.keyboard('s')
    const dialog = await screen.findByRole('dialog', { name: 'Act on every matching message' })
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Star' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(bodiesTo(fetchMock, BULK)).toEqual([
      { account: WORK, folder: 'INBOX', action: { kind: 'flag', flagged: true }, expected: 40 },
    ])
  })

  it('offers both ways of each flag change, counting only what the one picked would change', async () => {
    const fetchMock = stubMailApi(state({ matching: { total: 1200, changing: 30 } }))
    renderMailApp('/mail')

    await selectMatching()
    const toolbar = await selection()
    for (const name of ['Star', 'Unstar', 'Mark To sort', 'Mark sorted', 'Mark as read', 'Mark as unread']) {
      expect(within(toolbar).getByRole('button', { name: new RegExp(`^${name}`) })).not.toHaveAttribute('aria-disabled', 'true')
    }
    await userEvent.click(within(toolbar).getByRole('button', { name: /^Unstar/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Act on every matching message' })
    expect(await within(dialog).findByText(/^Unstar: 30 matching messages/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Unstar' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    const scope = { account: WORK, folder: 'INBOX' }
    const unstar = { kind: 'flag', flagged: false }
    expect(bodiesTo(fetchMock, COUNT)).toEqual([scope, { ...scope, action: unstar }])
    expect(bodiesTo(fetchMock, BULK)).toEqual([{ ...scope, action: unstar, expected: 30 }])
  })

  it('marks them sorted, with the To sort mark cleared as the action', async () => {
    const fetchMock = stubMailApi(state({ matching: { total: 1200, changing: 4 } }))
    renderMailApp('/mail')

    await selectMatching()
    await userEvent.click(within(await selection()).getByRole('button', { name: /^Mark sorted/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Act on every matching message' })
    expect(await within(dialog).findByText(/^Mark sorted: 4 matching messages/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Mark sorted' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(bodiesTo(fetchMock, BULK)).toEqual([
      { account: WORK, folder: 'INBOX', action: { kind: 'flag', to_sort: false }, expected: 4 },
    ])
  })

  it('asks again with the new count when the messages matching changed', async () => {
    const fake = state()
    let first = true
    fake.hold = (request) => {
      if (request === `POST ${BULK}` && first && fake.matching !== undefined) {
        first = false
        fake.matching.total = 1300
      }
      return undefined
    }
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail')

    await selectMatching()
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Move to Trash' }))
    const dialog = await screen.findByRole('dialog', { name: 'Act on every matching message' })
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Move to Trash' }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'The matching messages changed since they were counted: 1300 now. Nothing more was changed.',
    )
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Move to Trash' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    expect(bodiesTo(fetchMock, BULK).map((body) => (body as { expected: number }).expected)).toEqual([
      1200, 1300, 800, 300,
    ])
  })

  it('says why past 5,000 nothing can be selected', async () => {
    stubMailApi(state({ matching: { total: 6000 } }))
    renderMailApp('/mail')

    await screen.findByRole('list', { name: 'Messages' })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all' }))
    expect(
      await screen.findByText('6000 messages match: at most 5,000 can be acted on together. Narrow the search.'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Select all \d+ matching/ })).not.toBeInTheDocument()
  })

  it('stops between calls, says how far it got, and reads the list again on Close', async () => {
    const fake = state()
    let release = (): void => undefined
    fake.hold = (request) =>
      request === `POST ${BULK}`
        ? new Promise<void>((resolve) => {
            release = resolve
          })
        : undefined
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail')

    await selectMatching()
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Move to Trash' }))
    const dialog = await screen.findByRole('dialog', { name: 'Act on every matching message' })
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Move to Trash' }))
    expect(await within(dialog).findByText('0 of 1200 done…')).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Stop' }))
    expect(within(dialog).getByText('Stopping after this batch…')).toBeInTheDocument()
    release()

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Moved 500 messages to Trash.Stopped: the rest were left as they were.',
    )
    expect(bodiesTo(fetchMock, BULK)).toHaveLength(1)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Select all' })).not.toBeChecked()
  })

  it('stops at a folder that failed, saying how many were not changed', async () => {
    stubMailApi(state({ groupFailures: { '1/INBOX': 'timeout' } }))
    renderMailApp('/mail')

    await selectMatching()
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Move to Trash' }))
    const dialog = await screen.findByRole('dialog', { name: 'Act on every matching message' })
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Move to Trash' }))
    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent('1200 messages were not changed.')
    expect(alert).toHaveTextContent('Some messages could not be changed:')
  })

  it('acts on the search shown, with its criteria', async () => {
    const fetchMock = stubMailApi(
      state({ searchResult: { messages: inbox.slice(0, 25), next_cursor: 'before:76', failures: [] }, matching: { total: 60 } }),
    )
    renderMailApp(`/mail?q=${encodeURIComponent(JSON.stringify({ account: '*', folder: '*', subject: 'invoice' }))}`)

    await screen.findByRole('list', { name: 'Search results' })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Select all 60 matching' }))
    expect(bodiesTo(fetchMock, COUNT)).toEqual([{ account: '*', folder: '*', subject: 'invoice' }])
  })

  it('says when the matching messages could not be counted', async () => {
    stubMailApi(state({ refuse: { [`POST ${COUNT}`]: { status: 500, body: { detail: 'Internal Server Error' } } } }))
    renderMailApp('/mail')

    await screen.findByRole('list', { name: 'Messages' })
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select all' }))
    expect(await screen.findByText('Could not count the matching messages.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Select all \d+ matching/ })).not.toBeInTheDocument()
  })

  it('acts on every matching message from the menu of a row loaded after the mode began', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    await selectMatching()
    scrollListToEnd()
    const list = screen.getByRole('list', { name: 'Messages' })
    const link = await within(list).findByRole('link', { name: /Message 75Preview/ })
    expect(within(list).getByRole('checkbox', { name: 'Select Message 75' })).toBeChecked()
    await userEvent.pointer({ keys: '[MouseRight]', target: link })
    await userEvent.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: /Move to Trash/ }))
    expect(await screen.findByRole('dialog', { name: 'Act on every matching message' })).toBeInTheDocument()
  })

  it('leaves the mode when a row is unselected', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    await selectMatching()
    const list = screen.getByRole('list', { name: 'Messages' })
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Select Message 100' }))
    expect(screen.queryByText(/matching messages are selected/)).not.toBeInTheDocument()
    expect(within(await selection()).getByText('24 selected')).toBeInTheDocument()
  })
})

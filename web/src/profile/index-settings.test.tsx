import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { UserJson } from '@/api/auth.ts'
import { bodiesTo, HOME, indexFolder, refusal, stubMailApi, WORK, type FakeMailState } from '@/test/fake-mail-api.ts'
import { renderSignedIn } from '@/test/signed-in.tsx'

import { IndexSettings } from './index-settings.tsx'

const ALICE: UserJson = { username: 'alice', is_admin: false, time_zone: 'UTC', mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }
const STATUS_PATH = `/api/v1/mail/index/accounts/${String(WORK)}`
const FOLDERS_PATH = `${STATUS_PATH}/folders`

function state(fields: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK],
    folders: {},
    messages: {},
    views: {},
    index: {
      [WORK]: {
        folders: [
          indexFolder('INBOX', {
            role: 'inbox',
            enabled: true,
            total: 120,
            state: 'ready',
            indexed: 120,
            unread: 3,
            last_synced_at: '2026-11-20T10:00:00Z',
          }),
          indexFolder('Archive', { role: 'archive', enabled: true, total: 900, state: 'syncing', indexed: 400, unread: 0 }),
          indexFolder('Projects', { enabled: true, total: 10, state: 'failed', error: 'auth_failed' }),
          indexFolder('Junk', { role: 'junk', total: 50 }),
        ],
      },
    },
    ...fields,
  }
}

async function folderList(name: string): Promise<HTMLElement> {
  return screen.findByRole('list', { name: `Folders of ${name}` })
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function statusReads(fetchMock: ReturnType<typeof stubMailApi>): number {
  return fetchMock.mock.calls.filter(([path, init]) => {
    const method = (init as RequestInit | undefined)?.method ?? 'GET'
    return new URL(path as string, 'http://mailroom.test').pathname === STATUS_PATH && method === 'GET'
  }).length
}

describe('IndexSettings', () => {
  it('lists each folder with its switch, sync state and counts', async () => {
    stubMailApi(state())
    renderSignedIn(<IndexSettings />, ALICE)
    const list = await folderList('work')

    const inbox = within(list).getByRole('checkbox', { name: 'INBOX' })
    expect(inbox).toBeChecked()
    expect(inbox).toHaveAccessibleDescription(/^120 messages indexed, 3 unread · updated /)
    expect(within(list).getByRole('checkbox', { name: 'Archive' })).toHaveAccessibleDescription('Indexing: 400 of 900 messages')
    expect(within(list).getByRole('checkbox', { name: 'Projects' })).toHaveAccessibleDescription(/^Indexing failed: /)
    const junk = within(list).getByRole('checkbox', { name: 'Junk' })
    expect(junk).not.toBeChecked()
    expect(junk).toHaveAccessibleDescription('Not indexed: read from the server')
    expect(screen.getByText('Every message is indexed.')).toBeInTheDocument()
  })

  it('switches a folder on and off', async () => {
    const fetchMock = stubMailApi(state())
    renderSignedIn(<IndexSettings />, ALICE)
    const list = await folderList('work')

    await userEvent.click(within(list).getByRole('checkbox', { name: 'Junk' }))
    expect(await within(list).findByRole('checkbox', { name: 'Junk' })).toHaveAccessibleDescription('Waiting to be indexed')
    expect(within(list).getByRole('checkbox', { name: 'Junk' })).toBeChecked()

    await userEvent.click(within(list).getByRole('checkbox', { name: 'INBOX' }))
    await vi.waitFor(() => {
      expect(within(list).getByRole('checkbox', { name: 'INBOX' })).not.toBeChecked()
    })
    expect(bodiesTo(fetchMock, FOLDERS_PATH)).toEqual([
      { folder: 'Junk', enabled: true },
      { folder: 'INBOX', enabled: false },
    ])
  })

  it('reads the folders again while a folder switched on waits to be indexed', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fetchMock = stubMailApi(state({ index: { [WORK]: { folders: [indexFolder('Junk', { role: 'junk', total: 50 })] } } }))
    renderSignedIn(<IndexSettings />, ALICE)
    const list = await folderList('work')
    const user = userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) })
    await user.click(within(list).getByRole('checkbox', { name: 'Junk' }))
    expect(await within(list).findByRole('checkbox', { name: 'Junk' })).toHaveAccessibleDescription('Waiting to be indexed')

    const before = statusReads(fetchMock)
    await act(() => vi.advanceTimersByTimeAsync(15_000))
    await vi.waitFor(() => {
      expect(statusReads(fetchMock)).toBeGreaterThan(before)
    })
  })

  it('shows the age limit day as it is, whatever the time zone', async () => {
    stubMailApi(
      state({
        index: {
          [WORK]: {
            folders: [indexFolder('INBOX', { enabled: true, state: 'ready', indexed: 1, unread: 0, oldest_indexed: '2026-05-01' })],
          },
        },
      }),
    )
    renderSignedIn(<IndexSettings />, { ...ALICE, time_zone: 'America/New_York' })
    const list = await folderList('work')
    expect(within(list).getByRole('checkbox', { name: 'INBOX' })).toHaveAccessibleDescription(/since May 1, 2026/)
  })

  it('says when a folder could not be switched', async () => {
    stubMailApi(state({ refuse: { [`PUT ${FOLDERS_PATH}`]: refusal(404, 'mailbox_missing') } }))
    renderSignedIn(<IndexSettings />, ALICE)
    const list = await folderList('work')
    await userEvent.click(within(list).getByRole('checkbox', { name: 'Junk' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(within(list).getByRole('checkbox', { name: 'Junk' })).not.toBeChecked()
  })

  it('shows Gmail labels and live folders without a switch of their own', async () => {
    stubMailApi(
      state({
        index: {
          [WORK]: {
            gmail: true,
            folders: [
              indexFolder('INBOX', { role: 'inbox', indexed_as: 'label', enabled: true, state: 'ready' }),
              indexFolder('Work', { indexed_as: 'label', enabled: false }),
              indexFolder('[Gmail]/All Mail', { role: 'all', enabled: true, state: 'pending', indexed: 0, unread: 0 }),
              indexFolder('[Gmail]/Spam', { role: 'junk', indexed_as: 'live' }),
            ],
          },
        },
      }),
    )
    renderSignedIn(<IndexSettings />, ALICE)
    const list = await folderList('work')
    const inbox = within(list).getByRole('checkbox', { name: 'INBOX' })
    expect(inbox).toBeDisabled()
    expect(inbox).toHaveAccessibleDescription('Read from All Mail')
    // A label whose All Mail is off the index is read live.
    expect(within(list).getByRole('checkbox', { name: 'Work' })).toHaveAccessibleDescription('Not indexed: read from the server')
    expect(within(list).getByRole('checkbox', { name: '[Gmail]/Spam' })).toBeDisabled()
    expect(within(list).getByRole('checkbox', { name: '[Gmail]/All Mail' })).toBeEnabled()
    expect(screen.getByText(/On Gmail, All Mail is indexed/)).toBeInTheDocument()
  })

  it('shows the folders as a tree, a parent ticking its subfolders and showing when only some are on', async () => {
    const fetchMock = stubMailApi(
      state({
        index: {
          [WORK]: {
            folders: [
              indexFolder('INBOX', { role: 'inbox', enabled: true, state: 'ready', indexed: 1, unread: 0 }),
              indexFolder('Projects', { enabled: true, state: 'ready', indexed: 1, unread: 0 }),
              indexFolder('Projects/Alpha', { enabled: true, state: 'ready', indexed: 1, unread: 0 }),
              indexFolder('Projects/Beta'),
              indexFolder('Lists/News'),
            ],
          },
        },
      }),
    )
    renderSignedIn(<IndexSettings />, ALICE)
    const list = await folderList('work')

    const projects = within(list).getByRole('checkbox', { name: 'Projects' })
    expect(projects).toBePartiallyChecked()
    const branch = projects.closest('li')
    expect(branch).not.toBeNull()
    expect(within(branch as HTMLElement).getByRole('checkbox', { name: 'Projects/Beta' })).toBeInTheDocument()
    expect(within(branch as HTMLElement).getByText('Beta')).toBeInTheDocument()
    // A level the server did not list ticks what is under it.
    const lists = within(list).getByRole('checkbox', { name: 'Lists' })
    expect(lists).not.toBeChecked()
    expect(lists).toBeEnabled()

    await userEvent.click(projects)
    await vi.waitFor(() => {
      expect(within(list).getByRole('checkbox', { name: 'Projects' })).toBeChecked()
    })
    expect(within(list).getByRole('checkbox', { name: 'Projects/Beta' })).toBeChecked()

    await userEvent.click(within(list).getByRole('checkbox', { name: 'Projects' }))
    await userEvent.click(screen.getByRole('button', { name: 'Stop indexing' }))
    await vi.waitFor(() => {
      expect(within(list).getByRole('checkbox', { name: 'Projects' })).not.toBeChecked()
    })
    expect(within(list).getByRole('checkbox', { name: 'INBOX' })).toBeChecked()
    expect(bodiesTo(fetchMock, FOLDERS_PATH)).toEqual([
      { folder: 'Projects/Beta', enabled: true },
      { folder: 'Projects', enabled: false },
      { folder: 'Projects/Alpha', enabled: false },
      { folder: 'Projects/Beta', enabled: false },
    ])
  })

  it('filters the folders, keeping the folders above a match', async () => {
    stubMailApi(
      state({
        accounts: [WORK],
        index: {
          [WORK]: {
            folders: [
              indexFolder('INBOX', { role: 'inbox' }),
              indexFolder('Projects'),
              indexFolder('Projects/Alpha'),
              indexFolder('Projects/Beta'),
            ],
          },
        },
      }),
    )
    renderSignedIn(<IndexSettings />, ALICE)
    const list = await folderList('work')
    await userEvent.type(screen.getByRole('searchbox', { name: 'Filter folders' }), 'alp')

    expect(within(list).getByRole('checkbox', { name: 'Projects' })).toBeInTheDocument()
    expect(within(list).getByRole('checkbox', { name: 'Projects/Alpha' })).toBeInTheDocument()
    expect(within(list).queryByRole('checkbox', { name: 'Projects/Beta' })).not.toBeInTheDocument()
    expect(within(list).queryByRole('checkbox', { name: 'INBOX' })).not.toBeInTheDocument()

    await userEvent.type(screen.getByRole('searchbox', { name: 'Filter folders' }), 'zzz')
    expect(screen.queryByRole('list', { name: 'Folders of work' })).not.toBeInTheDocument()
    expect(screen.getByText('No folder matches the filter.')).toBeInTheDocument()
  })

  it('switches every folder shown on or off with All and None', async () => {
    const fetchMock = stubMailApi(
      state({
        index: {
          [WORK]: {
            folders: [
              indexFolder('INBOX', { role: 'inbox', enabled: true, state: 'ready', indexed: 1, unread: 0 }),
              indexFolder('Projects'),
              indexFolder('Projects/Alpha'),
              indexFolder('[Gmail]/Spam', { role: 'junk', indexed_as: 'live' }),
            ],
          },
        },
      }),
    )
    renderSignedIn(<IndexSettings />, ALICE)
    const list = await folderList('work')

    await userEvent.type(screen.getByRole('searchbox', { name: 'Filter folders' }), 'proj')
    await userEvent.click(screen.getByRole('button', { name: 'Index every folder of work shown' }))
    await vi.waitFor(() => {
      expect(within(list).getByRole('checkbox', { name: 'Projects' })).toBeChecked()
    })
    expect(bodiesTo(fetchMock, FOLDERS_PATH)).toEqual([
      { folder: 'Projects', enabled: true },
      { folder: 'Projects/Alpha', enabled: true },
    ])

    await userEvent.clear(screen.getByRole('searchbox', { name: 'Filter folders' }))
    await userEvent.click(screen.getByRole('button', { name: 'Index no folder of work shown' }))
    expect(screen.getByText('Stop indexing 3 folders of work? Their indexed mail is dropped.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Stop indexing' }))
    await vi.waitFor(() => {
      expect(within(list).getByRole('checkbox', { name: 'INBOX' })).not.toBeChecked()
    })
    expect(within(list).getByRole('checkbox', { name: 'Projects/Alpha' })).not.toBeChecked()
    expect(bodiesTo(fetchMock, FOLDERS_PATH)).toHaveLength(5)
    expect(screen.getByRole('button', { name: 'Index no folder of work shown' })).toBeDisabled()
  })

  it('asks before switching more than one folder off, and switches none when cancelled', async () => {
    const fetchMock = stubMailApi(
      state({
        index: {
          [WORK]: {
            folders: [
              indexFolder('INBOX', { role: 'inbox', enabled: true, state: 'ready', indexed: 1, unread: 0 }),
              indexFolder('Projects', { enabled: true, state: 'ready', indexed: 1, unread: 0 }),
            ],
          },
        },
      }),
    )
    renderSignedIn(<IndexSettings />, ALICE)
    const list = await folderList('work')

    await userEvent.click(screen.getByRole('button', { name: 'Index no folder of work shown' }))
    expect(screen.getByText('Stop indexing 2 folders of work? Their indexed mail is dropped.')).toBeInTheDocument()
    expect(within(list).getByRole('checkbox', { name: 'INBOX' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByText(/Stop indexing 2 folders/)).not.toBeInTheDocument()
    expect(within(list).getByRole('checkbox', { name: 'INBOX' })).toBeChecked()
    expect(within(list).getByRole('checkbox', { name: 'INBOX' })).toBeEnabled()
    expect(bodiesTo(fetchMock, FOLDERS_PATH)).toEqual([])
  })

  it('stops a batch at the first folder refused, keeping those switched before it', async () => {
    const fetchMock = stubMailApi(
      state({
        index: {
          [WORK]: {
            folders: [indexFolder('Projects'), indexFolder('Projects/Alpha'), indexFolder('Projects/Beta')],
          },
        },
      }),
    )
    const real = fetchMock.getMockImplementation()
    if (real === undefined) {
      throw new Error('stubMailApi gave no implementation')
    }
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PUT' && typeof init.body === 'string' && init.body.includes('"Projects/Alpha"')) {
        const { body } = refusal(404, 'mailbox_missing')
        return Promise.resolve(new Response(JSON.stringify(body), { status: 404, headers: { 'Content-Type': 'application/json' } }))
      }
      return real(input, init) as Promise<Response>
    })
    renderSignedIn(<IndexSettings />, ALICE)
    const list = await folderList('work')

    await userEvent.click(within(list).getByRole('checkbox', { name: 'Projects' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    await vi.waitFor(() => {
      expect(within(list).getByRole('checkbox', { name: 'Projects' })).toBeEnabled()
    })
    expect(bodiesTo(fetchMock, FOLDERS_PATH)).toEqual([
      { folder: 'Projects', enabled: true },
      { folder: 'Projects/Alpha', enabled: true },
    ])
    expect(within(list).getByRole('checkbox', { name: 'Projects' })).toBePartiallyChecked()
    expect(within(list).getByRole('checkbox', { name: 'Projects/Alpha' })).not.toBeChecked()
    expect(within(list).getByRole('checkbox', { name: 'Projects/Beta' })).not.toBeChecked()
    expect(screen.getByRole('button', { name: 'Index every folder of work shown' })).toBeEnabled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('keeps a folder the user cannot switch disabled, with switchable folders under it', async () => {
    stubMailApi(
      state({
        index: {
          [WORK]: {
            folders: [indexFolder('Junk', { role: 'junk', indexed_as: 'live' }), indexFolder('Junk/Old')],
          },
        },
      }),
    )
    renderSignedIn(<IndexSettings />, ALICE)
    const list = await folderList('work')
    const junk = within(list).getByRole('checkbox', { name: 'Junk' })
    expect(junk).toBeDisabled()
    expect(junk).not.toBeChecked()
    expect(junk).toHaveAccessibleDescription('Always read from the server')
    expect(within(list).getByRole('checkbox', { name: 'Junk/Old' })).toBeEnabled()
  })

  it('saves the age limit and shows the one in force under the cap', async () => {
    const fetchMock = stubMailApi(state({ indexMaxAgeCap: 365 }))
    renderSignedIn(<IndexSettings />, ALICE)
    expect(await screen.findByText(/Messages from the last 365 days are indexed. This server indexes at most 365 days./)).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText('Days of mail to index'), '30')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
    expect(bodiesTo(fetchMock, '/api/v1/auth/me/index-max-age')).toEqual([{ index_max_age_days: 30 }])
    expect(await screen.findByText(/Messages from the last 30 days are indexed./)).toBeInTheDocument()
  })

  it('says when the age limit could not be saved', async () => {
    stubMailApi(state({ refuse: { 'PUT /api/v1/auth/me/index-max-age': refusal(422, 'invalid_argument') } }))
    renderSignedIn(<IndexSettings />, ALICE)
    const field = screen.getByLabelText('Days of mail to index')
    await userEvent.type(field, '30')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('clears the age limit when the field is left empty', async () => {
    const fetchMock = stubMailApi(state({ indexMaxAgeDays: 90 }))
    renderSignedIn(<IndexSettings />, { ...ALICE, index_max_age_days: 90 })
    const field = screen.getByLabelText('Days of mail to index')
    expect(field).toHaveValue(90)
    await userEvent.clear(field)
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
    expect(bodiesTo(fetchMock, '/api/v1/auth/me/index-max-age')).toEqual([{ index_max_age_days: null }])
  })

  it('gives each account its own list, and says when one cannot be read', async () => {
    stubMailApi(state({ accounts: [WORK, HOME] }))
    renderSignedIn(<IndexSettings />, ALICE)
    await folderList('work')
    const home = screen.getByRole('region', { name: 'home' })
    expect(await within(home).findByRole('alert')).toBeInTheDocument()
  })
})

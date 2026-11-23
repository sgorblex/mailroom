import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { expectNoAxeViolations } from '@/test/axe.ts'
import { renderSignedIn } from '@/test/signed-in.tsx'

import { ActivityScreen } from './activity-screen.tsx'

const ALICE = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }

function record(id: number, fields: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id,
    at: '2026-11-10T09:30:00Z',
    actor: 'user',
    app: null,
    client_id: null,
    rule_set: null,
    rule_set_id: null,
    run_id: null,
    action: 'trash',
    account_id: 1,
    result: 'ok',
    detail: { folder: 'INBOX', destination: 'Trash', from: 'ana@example.org', uid: id },
    ...fields,
  }
}

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), { status }))
}

/** The accounts list and the audit trail, whose pages `audit` answers by the query string. */
function stubApi(
  audit: (query: URLSearchParams) => Promise<Response>,
  accounts: { id: number; name: string | null; colour: string | null }[] = [],
): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((path: string) => {
    if (path === '/api/v1/accounts') {
      return json(
        accounts.map((account) => ({
          ...account,
          display_name: account.name ?? `me@${String(account.id)}.example`,
          login: `me@${String(account.id)}.example`,
          authserv_id: null,
          send_mode: 'drafts',
          imap: { host: 'imap.example', port: 993, tls: 'implicit' },
          smtp: { host: 'smtp.example', port: 465, tls: 'implicit' },
          identities: [],
          auto_approve: [],
          has_password: true,
        })),
      )
    }
    const url = new URL(path, 'https://app.example')
    expect(url.pathname).toBe('/api/v1/me/audit')
    return audit(url.searchParams)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ActivityScreen', () => {
  it('lists who did what, newest first, and opens a record to its fields', async () => {
    stubApi(() =>
      json({
        records: [
          record(3, { actor: 'agent', app: 'Claude', action: 'send', result: 'rate_limited', detail: { to: ['bo@example.org'] } }),
          record(2, { actor: 'filter', rule_set: 'Shops', run_id: 7, action: 'move to Archive' }),
          record(1),
        ],
        next_cursor: null,
      }),
    )
    renderSignedIn(<ActivityScreen />, ALICE)

    const list = await screen.findByRole('list', { name: 'Recorded activity' })
    const items = within(list).getAllByRole('listitem')
    expect(items).toHaveLength(3)
    const [sent, filtered, trashed] = items as [HTMLElement, HTMLElement, HTMLElement]
    expect(sent).toHaveTextContent('Claude')
    expect(sent).toHaveTextContent('Sent')
    expect(sent).toHaveTextContent('Not done: rate_limited')
    expect(sent).toHaveTextContent('→ bo@example.org')
    expect(filtered).toHaveTextContent('Rule set: Shops')
    expect(filtered).toHaveTextContent('move to Archive')
    expect(trashed).toHaveTextContent('You')
    expect(trashed).toHaveTextContent('Moved to Trash')
    expect(trashed).toHaveTextContent('ana@example.org INBOX → Trash')

    await userEvent.click(within(trashed).getByText('Moved to Trash'))
    expect(within(trashed).getByText('To folder')).toBeVisible()
    expect(within(trashed).getByText('UID')).toBeVisible()
    await expectNoAxeViolations()
  })

  it('names each account by its name, and filters by its id', async () => {
    const fetchMock = stubApi(
      () =>
        json({
          records: [
            record(1, {
              action: 'move_to_account',
              detail: { folder: 'INBOX', destination: 'Archive', destination_account: 2, uid: 1 },
            }),
            record(2, {
              action: 'move_to_account',
              account_id: 2,
              detail: { folder: 'Archive', source_account: 1, source_folder: 'INBOX', uid: 7 },
            }),
          ],
          next_cursor: null,
        }),
      [
        { id: 1, name: 'Work mail', colour: 'teal' },
        { id: 2, name: 'Home', colour: null },
      ],
    )
    renderSignedIn(<ActivityScreen />, ALICE)

    const list = await screen.findByRole('list', { name: 'Recorded activity' })
    const [moved, copy] = within(list).getAllByRole('listitem') as [HTMLElement, HTMLElement]
    expect(await within(moved).findByText('Work mail')).toBeInTheDocument()
    await userEvent.click(within(moved).getByText('Work mail'))
    expect(within(moved).getByText('To account').nextElementSibling).toHaveTextContent('Home')
    await userEvent.click(within(copy).getByText('Home'))
    expect(within(copy).getByText('From account').nextElementSibling).toHaveTextContent('Work mail')
    expect(within(copy).getByText('From folder').nextElementSibling).toHaveTextContent('INBOX')

    await userEvent.selectOptions(screen.getByLabelText('Account'), 'Home')
    await screen.findByRole('list', { name: 'Recorded activity' })
    const queries = fetchMock.mock.calls.map(([path]) => String(path)).filter((path) => path.startsWith('/api/v1/me/audit'))
    expect(queries.at(-1)).toContain('account_id=2')
  })

  it('names an account that is gone a removed account, never by its id', async () => {
    stubApi(
      () =>
        json({
          records: [
            record(1, {
              action: 'move_to_account',
              account_id: 7,
              detail: { folder: 'INBOX', destination: 'Archive', destination_account: 8, uid: 1 },
            }),
          ],
          next_cursor: null,
        }),
      [{ id: 1, name: 'work', colour: null }],
    )
    renderSignedIn(<ActivityScreen />, ALICE)

    const list = await screen.findByRole('list', { name: 'Recorded activity' })
    const [moved] = within(list).getAllByRole('listitem') as [HTMLElement]
    const named = (await within(moved).findAllByText('Removed account')) as [HTMLElement, ...HTMLElement[]]
    await userEvent.click(named[0])
    expect(within(moved).getByText('To account').nextElementSibling?.textContent).toBe('Removed account')
    expect(named[0].textContent).toBe('Removed account')
    expect(screen.getByRole('option', { name: 'work' })).toBeInTheDocument()
  })

  it('drops a page asked for under the filters it had once they change', async () => {
    let release: (value: Response) => void = () => undefined
    stubApi((query) => {
      if (query.get('cursor') === '2') {
        return new Promise<Response>((resolve) => {
          release = resolve
        })
      }
      return query.get('actor') === 'filter'
        ? json({ records: [record(9, { actor: 'filter', rule_set: 'Shops' })], next_cursor: null })
        : json({ records: [record(3), record(2)], next_cursor: '2' })
    })
    renderSignedIn(<ActivityScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Load more' }))
    await userEvent.selectOptions(screen.getByLabelText('Who'), 'Filters')
    expect(await screen.findByText('Rule set: Shops')).toBeInTheDocument()
    release(new Response(JSON.stringify({ records: [record(1)], next_cursor: null })))
    await new Promise((resolve) => setTimeout(resolve, 0))
    const list = screen.getByRole('list', { name: 'Recorded activity' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(1)
  })

  it('words what it kept in the reader\'s language, leaving out what is empty', async () => {
    stubApi(() =>
      json({
        records: [record(1, { action: 'send', detail: { filed: true, delivery: 'drafted', error: null } })],
        next_cursor: null,
      }),
    )
    renderSignedIn(<ActivityScreen />, ALICE)
    const row = await screen.findByRole('listitem')
    await userEvent.click(within(row).getByText('Sent'))
    expect(within(row).getByText('Yes')).toBeVisible()
    expect(within(row).getByText('Saved as a draft')).toBeVisible()
    expect(within(row).queryByText('Error')).not.toBeInTheDocument()
  })

  it('words a folder action and what it changed', async () => {
    stubApi(() =>
      json({
        records: [
          record(2, { action: 'folder_rename', detail: { folder: 'Old', destination: 'Lists/New', rule_sets: [4, 9], filters: [12] } }),
          record(1, { action: 'folder_delete', result: 'folder_changed', detail: { folder: 'Lists', messages: 3 } }),
        ],
        next_cursor: null,
      }),
    )
    renderSignedIn(<ActivityScreen />, ALICE)
    const [renamed, deleted] = (await screen.findAllByRole('listitem')) as [HTMLElement, HTMLElement]
    expect(renamed).toHaveTextContent('Folder renamed')
    expect(renamed).toHaveTextContent('Old → Lists/New')
    await userEvent.click(within(renamed).getByText('Folder renamed'))
    expect(within(renamed).getByText('Rule sets')).toBeVisible()
    expect(within(renamed).getByText('4, 9')).toBeVisible()
    expect(deleted).toHaveTextContent('Folder deleted')
    expect(deleted).toHaveTextContent('Not done: folder_changed')
    await userEvent.click(within(deleted).getByText('Folder deleted'))
    expect(within(deleted).getByText('Messages')).toBeVisible()
  })

  it('loads more with the cursor, and filters by who acted', async () => {
    const fetchMock = stubApi((query) => {
      if (query.get('actor') === 'agent') {
        return json({ records: [], next_cursor: null })
      }
      return query.get('cursor') === '2'
        ? json({ records: [record(1)], next_cursor: null })
        : json({ records: [record(3), record(2)], next_cursor: '2' })
    })
    renderSignedIn(<ActivityScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Load more' }))
    const list = screen.getByRole('list', { name: 'Recorded activity' })
    expect(await within(list).findAllByRole('listitem')).toHaveLength(3)
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Who'), 'Connected apps')
    expect(await screen.findByText('Nothing matches these filters.')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenLastCalledWith('/api/v1/me/audit?actor=agent', expect.anything())
  })

  it('says when nothing is recorded, and when the list cannot load', async () => {
    stubApi(() => json({ records: [], next_cursor: null }))
    renderSignedIn(<ActivityScreen />, ALICE)
    expect(await screen.findByText('Nothing recorded yet.')).toBeInTheDocument()

    vi.unstubAllGlobals()
    stubApi(() => json({ detail: { code: 'failed', message: 'Something broke.' } }, 500))
    renderSignedIn(<ActivityScreen />, ALICE)
    expect(await screen.findByRole('alert')).toHaveTextContent('Something broke.')
  })
})

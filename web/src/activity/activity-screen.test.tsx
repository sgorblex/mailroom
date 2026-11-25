import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ApiToken } from '@/api/api-tokens.ts'
import type { ConnectedApp } from '@/api/oauth.ts'
import { expectNoAxeViolations } from '@/test/axe.ts'
import { renderRouted } from '@/test/routed.tsx'

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
  apps: { grants?: ConnectedApp[]; tokens?: ApiToken[]; fail?: boolean } = {},
): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((path: string) => {
    if (path === '/api/v1/oauth/grants' || path === '/api/v1/me/api-tokens') {
      const { grants = [], tokens = [], fail = false } = apps
      const body = path.endsWith('grants') ? { auth: 'builtin', grants, groups: [] } : { tokens, groups: [], accounts: [] }
      return fail ? json({ detail: 'Server error' }, 500) : json(body)
    }
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

const LIMITS = { groups: [], account_ids: null, send_ceiling: null }

function grant(id: number, name: string | null, created_at = '2026-10-25T12:00:00Z'): ConnectedApp {
  return { ...LIMITS, id, client_name: name, client_host: null, created_at, last_used_at: null }
}

function token(id: number, name: string): ApiToken {
  return { ...LIMITS, id, name, prefix: 'mrt_0a1b2c3d', created_at: '2026-10-25T12:00:00Z', last_used_at: null, expires_at: null, expired: false }
}

function auditQueries(fetchMock: ReturnType<typeof vi.fn>): string[] {
  return fetchMock.mock.calls
    .map(([path]) => String(path))
    .filter((path) => path.startsWith('/api/v1/me/audit'))
    .map((path) => decodeURIComponent(path.slice('/api/v1/me/audit'.length)))
}

/** What the App filter offers, and shows now. */
function appFilter(): { offered: (string | null)[]; shown: string | undefined } {
  const select = screen.getByRole<HTMLSelectElement>('combobox', { name: 'App' })
  return {
    offered: within(select).getAllByRole('option').map((option) => option.textContent),
    shown: select.selectedOptions[0]?.textContent ?? undefined,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ActivityScreen', () => {
  it('names an API token, and says when it was revoked since', async () => {
    const agent = { actor: 'agent', token_id: 4 }
    stubApi(() => json({ records: [record(2, { ...agent, app: 'Backup script' }), record(1, agent)], next_cursor: null }))
    renderRouted(<ActivityScreen />, ALICE, '/activity')
    const list = await screen.findByRole('list', { name: 'Recorded activity' })
    const [named, revoked] = within(list).getAllByRole('listitem')
    expect(named).toHaveTextContent('Backup script')
    expect(revoked).toHaveTextContent('A revoked API token')
  })

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
    renderRouted(<ActivityScreen />, ALICE, '/activity')

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
    renderRouted(<ActivityScreen />, ALICE, '/activity')

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
    renderRouted(<ActivityScreen />, ALICE, '/activity')

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
    renderRouted(<ActivityScreen />, ALICE, '/activity')

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
    renderRouted(<ActivityScreen />, ALICE, '/activity')
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
    renderRouted(<ActivityScreen />, ALICE, '/activity')
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
    renderRouted(<ActivityScreen />, ALICE, '/activity')

    await userEvent.click(await screen.findByRole('button', { name: 'Load more' }))
    const list = screen.getByRole('list', { name: 'Recorded activity' })
    expect(await within(list).findAllByRole('listitem')).toHaveLength(3)
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Who'), 'Connected apps')
    expect(await screen.findByText('Nothing matches these filters.')).toBeInTheDocument()
    expect(auditQueries(fetchMock).at(-1)).toBe('?actor=agent')
  })

  it('says when nothing is recorded, and when the list cannot load', async () => {
    stubApi(() => json({ records: [], next_cursor: null }))
    renderRouted(<ActivityScreen />, ALICE, '/activity')
    expect(await screen.findByText('Nothing recorded yet.')).toBeInTheDocument()

    vi.unstubAllGlobals()
    stubApi(() => json({ detail: { code: 'failed', message: 'Something broke.' } }, 500))
    renderRouted(<ActivityScreen />, ALICE, '/activity')
    expect(await screen.findByRole('alert')).toHaveTextContent('Something broke.')
  })

  it('filters as the URL says, and keeps there what is chosen', async () => {
    const fetchMock = stubApi(() => json({ records: [record(1)], next_cursor: null }), [{ id: 2, name: 'Home', colour: null }])
    const history = renderRouted(<ActivityScreen />, ALICE, '/activity?account=2&actor=agent&action=send&result=failed')
    await screen.findByRole('list', { name: 'Recorded activity' })
    expect(auditQueries(fetchMock)).toEqual(['?actor=agent&action=send&result=failed&account_id=2'])
    expect(await screen.findByRole('option', { name: 'Home', selected: true })).toBeInTheDocument()
    expect(screen.getByLabelText('Who')).toHaveValue('agent')
    expect(screen.getByLabelText('Action')).toHaveValue('send')
    expect(screen.getByLabelText('Result')).toHaveValue('failed')

    await userEvent.selectOptions(screen.getByLabelText('Action'), 'Any')
    await vi.waitFor(() => {
      expect(auditQueries(fetchMock).at(-1)).toBe('?actor=agent&result=failed&account_id=2')
    })
    expect(history.location.search).toBe('?account=2&actor=agent&result=failed')
    expect(history.length).toBe(1)
  })

  it('leaves out of the URL what is no filter', async () => {
    const fetchMock = stubApi(() => json({ records: [record(1)], next_cursor: null }))
    renderRouted(<ActivityScreen />, ALICE, '/activity?account=x&actor=nobody&action=7&result=maybe&app=grant:0')
    await screen.findByRole('list', { name: 'Recorded activity' })
    expect(auditQueries(fetchMock)).toEqual([''])
  })

  it('says an account the URL names is gone, rather than any', async () => {
    stubApi(() => json({ records: [], next_cursor: null }), [{ id: 1, name: 'Work', colour: null }])
    renderRouted(<ActivityScreen />, ALICE, '/activity?account=9')
    expect(await screen.findByRole('option', { name: 'Removed account', selected: true })).toBeInTheDocument()
  })

  it('offers each connected app and token, telling apps of one name apart', async () => {
    const fetchMock = stubApi(() => json({ records: [record(1)], next_cursor: null }), [], {
      grants: [grant(1, 'Claude'), grant(2, 'Claude', '2026-11-02T12:00:00Z'), grant(3, null)],
      tokens: [token(4, 'Backup')],
    })
    const history = renderRouted(<ActivityScreen />, ALICE, '/activity')
    await userEvent.selectOptions(await screen.findByLabelText('App'), await screen.findByRole('option', { name: 'Backup (API token)' }))
    await vi.waitFor(() => {
      expect(auditQueries(fetchMock).at(-1)).toBe('?token_id=4')
    })
    expect(decodeURIComponent(history.location.search)).toBe('?app=token:4')
    const { offered, shown } = appFilter()
    expect(shown).toBe('Backup (API token)')
    expect(offered).toEqual(['Any', expect.stringMatching(/^Claude \(connected .*25.*\)$/), expect.stringMatching(/^Claude \(connected .*2.*\)$/), 'An app with no name', 'Backup (API token)'])
    expect(offered[1]).not.toBe(offered[2])
    await expectNoAxeViolations()
  })

  it.each([
    ['grant:1', '?grant_id=1', 'Claude'],
    ['grant:8', '?grant_id=8', 'A disconnected app'],
    ['token:8', '?token_id=8', 'A revoked API token'],
    ['client:https://app.example/c', '?client_id=https://app.example/c', 'https://app.example/c'],
  ])('lists one app, %s, gone or not', async (app, query, name) => {
    const fetchMock = stubApi(() => json({ records: [], next_cursor: null }), [], { grants: [grant(1, 'Claude')] })
    renderRouted(<ActivityScreen />, ALICE, `/activity?app=${encodeURIComponent(app)}`)
    expect(await screen.findByText('Nothing matches these filters.')).toBeInTheDocument()
    expect(auditQueries(fetchMock)).toEqual([query])
    await vi.waitFor(() => {
      expect(appFilter().shown).toBe(name)
    })
  })

  it('does not call an app gone when the apps could not be loaded', async () => {
    const fetchMock = stubApi(() => json({ records: [record(1)], next_cursor: null }), [], { fail: true })
    renderRouted(<ActivityScreen />, ALICE, '/activity?app=token:4')
    await screen.findByRole('list', { name: 'Recorded activity' })
    expect(appFilter().shown).toBe('The one chosen')
    expect(auditQueries(fetchMock)).toEqual(['?token_id=4'])
  })

  it('does not ask for connected apps with MCP off', async () => {
    const fetchMock = stubApi(() => json({ records: [record(1)], next_cursor: null }), [], { tokens: [token(4, 'Backup')] })
    renderRouted(<ActivityScreen />, ALICE, '/activity', ['mail'])
    expect(await screen.findByRole('option', { name: 'Backup (API token)' })).toBeInTheDocument()
    expect(fetchMock.mock.calls.map(([path]) => String(path))).not.toContain('/api/v1/oauth/grants')
  })

  it('narrows the list to the app a record came through', async () => {
    const claude = { actor: 'agent', app: 'Claude', client_id: 'c1' }
    stubApi(() =>
      json({
        records: [
          record(4, { ...claude, grant_id: '7' }),
          record(3, { ...claude, grant_id: null }),
          record(2, { actor: 'agent', app: 'Backup', token_id: 4 }),
          record(1),
        ],
        next_cursor: null,
      }),
    )
    const history = renderRouted(<ActivityScreen />, ALICE, '/activity?actor=agent')
    const rows = await screen.findAllByRole('listitem')
    for (const row of rows) {
      await userEvent.click(within(row).getByText('Moved to Trash'))
    }
    const links = screen.getAllByRole('link').map((link) => decodeURIComponent(link.getAttribute('href') ?? ''))
    expect(links).toEqual(['/activity?app=grant:7', '/activity?app=client:c1', '/activity?app=token:4'])
    await userEvent.click(screen.getByRole('link', { name: 'Only Backup' }))
    expect(decodeURIComponent(history.location.search)).toBe('?app=token:4')
  })
})

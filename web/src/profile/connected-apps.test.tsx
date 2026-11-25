import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AppLimits, ConnectedApp, ConnectedApps as Listing } from '@/api/oauth.ts'
import { renderRouted } from '@/test/routed.tsx'

import { ConnectedApps } from './connected-apps.tsx'

const ALICE = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }

const SERVED: Listing['groups'] = [
  { name: 'mail_read', description: 'list, search and read mail', requires: [], default: true },
  {
    name: 'mail_organize',
    description: 'move, trash and flag mail',
    requires: ['mail_read'],
    default: true,
  },
  {
    name: 'mail_transfer',
    description: "move mail to another of the user's accounts",
    requires: ['mail_read'],
    default: false,
  },
]

const DRAFTING: Listing['groups'] = [
  ...SERVED,
  { name: 'mail_draft', description: 'write drafts', requires: ['mail_read'], default: true },
]

const ACCOUNTS: Listing['accounts'] = [
  { id: 1, name: 'Work' },
  { id: 2, name: 'Home' },
]

function app(
  id: number,
  name: string | null,
  lastUsed: string | null = null,
  groups: string[] = ['mail_read', 'mail_organize', 'mail_transfer'],
): ConnectedApp {
  return {
    id,
    client_name: name,
    client_host: name === 'Claude' ? 'claude.ai' : null,
    created_at: '2026-10-25T12:00:00Z',
    last_used_at: lastUsed,
    groups,
    account_ids: null,
    send_ceiling: null,
  }
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

/** A fake API holding `grants`; DELETE removes one, or fails to reach the API when `failRevoke`. */
function stubApi(auth: Listing['auth'], grants: ConnectedApp[], { failRevoke = false, failLimits = false, served = SERVED } = {}) {
  let held = [...grants]
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    if (path === '/api/v1/oauth/grants') {
      const groups = auth === 'builtin' || auth === 'oidc' ? served : []
      return Promise.resolve(json(200, { auth, grants: held, groups, accounts: ACCOUNTS }))
    }
    const changed = /^\/api\/v1\/oauth\/grants\/(\d+)\/groups$/.exec(path)
    if (changed !== null && init?.method === 'PUT') {
      const { groups } = JSON.parse(init.body as string) as { groups: string[] }
      held = held.map((item) => (String(item.id) === changed[1] ? { ...item, groups } : item))
      return Promise.resolve(new Response(null, { status: 204 }))
    }
    const limited = /^\/api\/v1\/oauth\/grants\/(\d+)\/limits$/.exec(path)
    if (limited !== null && init?.method === 'PUT') {
      if (failLimits) {
        return Promise.reject(new TypeError('Failed to fetch'))
      }
      const limits = JSON.parse(init.body as string) as AppLimits
      held = held.map((item) => (String(item.id) === limited[1] ? { ...item, ...limits } : item))
      return Promise.resolve(new Response(null, { status: 204 }))
    }
    const revoked = /^\/api\/v1\/oauth\/grants\/(\d+)$/.exec(path)
    if (revoked !== null && init?.method === 'DELETE') {
      if (failRevoke) {
        return Promise.reject(new TypeError('Failed to fetch'))
      }
      held = held.filter((item) => String(item.id) !== revoked[1])
      return Promise.resolve(new Response(null, { status: 204 }))
    }
    return Promise.resolve(json(404, { detail: 'Not Found' }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function deleted(fetchMock: ReturnType<typeof stubApi>): unknown[][] {
  return fetchMock.mock.calls.filter(([, init]) => init?.method === 'DELETE')
}

function listed(fetchMock: ReturnType<typeof stubApi>): unknown[][] {
  return fetchMock.mock.calls.filter(([path]) => path === '/api/v1/oauth/grants')
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ConnectedApps', () => {
  it('links each app to its own activity', async () => {
    stubApi('builtin', [app(1, 'Claude'), app(2, 'Claude')])
    const history = renderRouted(<ConnectedApps />, ALICE, '/profile')
    const [first, second] = await screen.findAllByRole('link', { name: 'Activity: Claude' })
    expect(decodeURIComponent(first?.getAttribute('href') ?? '')).toBe('/activity?app=grant:1')
    if (second === undefined) {
      throw new Error('two links expected')
    }
    await userEvent.click(second)
    expect(await screen.findByText('Page /activity')).toBeInTheDocument()
    expect(decodeURIComponent(history.location.search)).toBe('?app=grant:2')
  })

  it('lists the apps and disconnects one', async () => {
    const fetchMock = stubApi('builtin', [
      app(1, 'Claude', '2026-10-25T13:00:00Z'),
      app(2, null),
    ])
    renderRouted(<ConnectedApps />, ALICE, '/profile')
    const list = await screen.findByRole('list', { name: 'Connected apps' })
    const [claude, unnamed, ...rest] = within(list).getAllByRole('listitem')
    expect(rest).toHaveLength(0)
    if (claude === undefined || unnamed === undefined) {
      throw new Error('two apps expected')
    }
    expect(within(claude).getByText('claude.ai')).toBeInTheDocument()
    expect(within(claude).getByText(/last used/)).toBeInTheDocument()
    expect(within(unnamed).getByText('An app with no name')).toBeInTheDocument()
    expect(within(unnamed).getByText(/never used/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Disconnect Claude' }))
    expect(within(claude).getByText(/Disconnect Claude\?/)).toBeInTheDocument()
    expect(deleted(fetchMock)).toHaveLength(0)
    const confirm = within(claude).getByRole('button', { name: 'Disconnect' })
    expect(within(claude).getByRole('button', { name: 'Cancel' })).toHaveFocus()
    await userEvent.click(confirm)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/oauth/grants/1',
      expect.objectContaining({ method: 'DELETE' }),
    )
    await vi.waitFor(() => {
      expect(screen.getAllByRole('listitem')).toHaveLength(1)
    })
    expect(screen.queryByRole('button', { name: 'Disconnect Claude' })).not.toBeInTheDocument()
  })

  it('keeps the app when the confirm is cancelled', async () => {
    const fetchMock = stubApi('builtin', [app(1, 'Claude')])
    renderRouted(<ConnectedApps />, ALICE, '/profile')
    await userEvent.click(await screen.findByRole('button', { name: 'Disconnect Claude' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByText(/Disconnect Claude\?/)).not.toBeInTheDocument()
    const trigger = screen.getByRole('button', { name: 'Disconnect Claude' })
    await vi.waitFor(() => {
      expect(trigger).toHaveFocus()
    })
    expect(deleted(fetchMock)).toHaveLength(0)
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
  })

  it('says when a disconnect fails and shows what is left', async () => {
    const fetchMock = stubApi('builtin', [app(1, 'Claude')], { failRevoke: true })
    renderRouted(<ConnectedApps />, ALICE, '/profile')
    await userEvent.click(await screen.findByRole('button', { name: 'Disconnect Claude' }))
    await userEvent.click(screen.getByRole('button', { name: 'Disconnect' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not disconnect it.')
    expect(deleted(fetchMock)).toHaveLength(1)
    await vi.waitFor(() => {
      expect(listed(fetchMock)).toHaveLength(2)
    })
    expect(screen.getByRole('button', { name: 'Disconnect Claude' })).toBeInTheDocument()
  })

  it('says so while nothing is connected', async () => {
    stubApi('builtin', [])
    renderRouted(<ConnectedApps />, ALICE, '/profile')
    expect(await screen.findByText(/None yet/)).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  it('points to the identity provider with an external issuer', async () => {
    stubApi('external', [app(3, 'Claude')])
    renderRouted(<ConnectedApps />, ALICE, '/profile')
    expect(await screen.findByText(/Your identity provider manages/)).toHaveTextContent(
      'Apps listed here were connected before.',
    )
    // Left from the built-in server, still revocable.
    expect(screen.getByRole('button', { name: 'Disconnect Claude' })).toBeInTheDocument()
  })

  it('mentions no listed apps with an external issuer when there are none', async () => {
    stubApi('external', [])
    renderRouted(<ConnectedApps />, ALICE, '/profile')
    expect(await screen.findByText(/Your identity provider manages/)).not.toHaveTextContent(
      'Apps listed here',
    )
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  it('stays out of the way while the MCP server is off and nothing is connected', async () => {
    const fetchMock = stubApi('off', [])
    renderRouted(<ConnectedApps />, ALICE, '/profile')
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1)
    })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.queryByRole('heading')).not.toBeInTheDocument()
  })
  it('shows the tools an app may use and approves those it was not approved for', async () => {
    const fetchMock = stubApi('builtin', [app(1, 'Claude', null, ['mail_read'])])
    renderRouted(<ConnectedApps />, ALICE, '/profile')
    expect(await screen.findByText('Tools: Read mail')).toBeInTheDocument()
    expect(
      screen.getByText('Not approved: Organize mail, Move mail between accounts'),
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Approve them' }))
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/oauth/grants/1/groups',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ groups: ['mail_read', 'mail_organize', 'mail_transfer'] }),
      }),
    )
    expect(
      await screen.findByText('Tools: Read mail, Organize mail, Move mail between accounts'),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Not approved/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve them' })).not.toBeInTheDocument()
  })

  it('changes the tools an app may use', async () => {
    const fetchMock = stubApi('builtin', [app(1, 'Claude')])
    renderRouted(<ConnectedApps />, ALICE, '/profile')
    await userEvent.click(await screen.findByRole('button', { name: 'Change tools' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Move mail between accounts' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/oauth/grants/1/groups',
      expect.objectContaining({ body: JSON.stringify({ groups: ['mail_read', 'mail_organize'] }) }),
    )
    expect(await screen.findByText('Tools: Read mail, Organize mail')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('offers no tool choice with an external issuer', async () => {
    stubApi('external', [app(3, 'Claude')])
    renderRouted(<ConnectedApps />, ALICE, '/profile')
    expect(await screen.findByText('Claude')).toBeInTheDocument()
    expect(screen.queryByText(/Tools:/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Change tools' })).not.toBeInTheDocument()
  })

  it('limits an app to some accounts and to a send ceiling', async () => {
    const fetchMock = stubApi('builtin', [app(1, 'Claude', null, ['mail_read'])])
    renderRouted(<ConnectedApps />, ALICE, '/profile')
    expect(await screen.findByText('Accounts: all of them')).toBeInTheDocument()
    expect(screen.queryByText(/Sending, at most/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Change limits' }))
    const save = screen.getByRole('button', { name: 'Save limits' })
    await userEvent.click(screen.getByRole('radio', { name: 'Only these' }))
    expect(save).toBeDisabled()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Home' }))
    await userEvent.click(save)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/oauth/grants/1/limits',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ account_ids: [2], send_ceiling: null }),
      }),
    )
    expect(await screen.findByText('Accounts: Home')).toBeInTheDocument()
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
  })

  it('shows the limits an app has, and lifts them', async () => {
    const fetchMock = stubApi(
      'builtin',
      [
        { ...app(1, 'Claude'), account_ids: [1], send_ceiling: 'drafts' },
        { ...app(2, 'Other'), account_ids: [], send_ceiling: 'direct' },
      ],
      { served: DRAFTING },
    )
    renderRouted(<ConnectedApps />, ALICE, '/profile')
    expect(await screen.findByText('Accounts: Work')).toBeInTheDocument()
    expect(screen.getByText('Sending, at most: Drafts only')).toBeInTheDocument()
    expect(screen.getByText('No accounts: those it had were removed.')).toBeInTheDocument()
    const [change] = screen.getAllByRole('button', { name: 'Change limits' })
    if (change === undefined) {
      throw new Error('a button expected')
    }
    await userEvent.click(change)
    const ceiling = screen.getByRole('combobox', { name: 'Sending, at most' })
    expect(ceiling).toHaveValue('drafts')
    expect(screen.getByRole('checkbox', { name: 'Work' })).toBeChecked()
    await userEvent.selectOptions(ceiling, 'As each account allows')
    await userEvent.click(screen.getByRole('radio', { name: /All of them/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Save limits' }))
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/oauth/grants/1/limits',
      expect.objectContaining({ body: JSON.stringify({ account_ids: null, send_ceiling: null }) }),
    )
  })

  it('says when limits could not be changed, keeping the choice', async () => {
    stubApi('builtin', [app(1, 'Claude')], { failLimits: true })
    renderRouted(<ConnectedApps />, ALICE, '/profile')
    await userEvent.click(await screen.findByRole('button', { name: 'Change limits' }))
    await userEvent.click(screen.getByRole('radio', { name: 'Only these' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Work' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save limits' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not change its limits.')
    expect(screen.getByRole('checkbox', { name: 'Work' })).toBeChecked()
  })
})

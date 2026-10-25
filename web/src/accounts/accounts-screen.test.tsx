import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderSignedIn } from '@/test/signed-in.tsx'

import { AccountsScreen } from './accounts-screen.tsx'

const ALICE = { username: 'alice', is_admin: false }

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

interface StoredAccount {
  id: string
  login: string
  send_mode: string
  imap: { host: string; port: number; tls: string }
  smtp: { host: string; port: number; tls: string }
  identities: { address: string; name?: string | null; default?: boolean }[]
  auto_approve: string[]
  has_password: boolean
  run_filters: boolean
}

const WORK: StoredAccount = {
  id: 'work',
  login: 'alice@example.org',
  send_mode: 'drafts',
  imap: { host: 'imap.example.org', port: 993, tls: 'implicit' },
  smtp: { host: 'smtp.example.org', port: 465, tls: 'implicit' },
  identities: [{ address: 'alice@example.org', name: 'Alice', default: true }],
  auto_approve: [],
  has_password: true,
  run_filters: false,
}

type Body = Record<string, unknown>

/** The account as listed: never the password. */
function withoutPassword(body: Body): Body {
  return Object.fromEntries(Object.entries(body).filter(([key]) => key !== 'password'))
}

/** A fake accounts API holding `initial`; refuses an id that is not a slug, as mailroom-core does. */
function stubApi(initial: StoredAccount[] = [WORK]): ReturnType<typeof vi.fn> {
  let accounts = [...initial]
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const body = init?.body == null ? {} : (JSON.parse(init.body as string) as Body)
    if (path === '/api/v1/accounts' && method === 'GET') {
      return Promise.resolve(json(200, accounts))
    }
    if (path === '/api/v1/accounts' && method === 'POST') {
      if (!/^[a-z0-9][a-z0-9-]*$/.test(body['id'] as string)) {
        const issues = [{ path: ['id'], message: 'must be lowercase letters, digits and dashes' }]
        return Promise.resolve(
          json(400, { detail: { code: 'invalid_account', message: 'imap.port: …', issues } }),
        )
      }
      const fields = withoutPassword(body)
      const created = { ...fields, has_password: true, run_filters: false } as unknown as StoredAccount
      accounts = [...accounts, created]
      return Promise.resolve(json(201, created))
    }
    const match = /^\/api\/v1\/accounts\/([^/]+)(\/.*)?$/.exec(path)
    const id = match?.[1]
    const rest = match?.[2] ?? ''
    const account = accounts.find((candidate) => candidate.id === id)
    if (account === undefined) {
      return Promise.resolve(json(404, { detail: 'No such account' }))
    }
    if (rest === '' && method === 'PUT') {
      const fields = withoutPassword(body)
      const changed = { ...account, ...fields }
      accounts = accounts.map((candidate) => (candidate.id === id ? changed : candidate))
      return Promise.resolve(json(200, changed))
    }
    if (rest === '' && method === 'DELETE') {
      accounts = accounts.filter((candidate) => candidate.id !== id)
      return Promise.resolve(new Response(null, { status: 204 }))
    }
    if (rest === '/test' && method === 'POST') {
      return Promise.resolve(
        json(200, account.login.startsWith('alice')
          ? { ok: true, code: null, message: null }
          : { ok: false, code: 'login_failed', message: 'Authentication failed.' }),
      )
    }
    if (rest === '/filter-settings' && method === 'PUT') {
      account.run_filters = body['run_filters'] as boolean
      return Promise.resolve(json(200, { run_filters: account.run_filters }))
    }
    return Promise.resolve(json(404, { detail: 'Not Found' }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** The body of the last `method` request to `path`. */
function sent(fetchMock: ReturnType<typeof vi.fn>, method: string, path: string): Body {
  const call = fetchMock.mock.calls.findLast(
    ([callPath, init]) => callPath === path && (init as RequestInit | undefined)?.method === method,
  )
  if (call === undefined) {
    throw new Error(`no ${method} ${path}`)
  }
  return JSON.parse((call[1] as RequestInit).body as string) as Body
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('AccountsScreen', () => {
  it('says when there are no accounts yet', async () => {
    stubApi([])
    renderSignedIn(<AccountsScreen />, ALICE)
    expect(await screen.findByText('No accounts yet.')).toBeInTheDocument()
  })

  it('adds an account, showing what mailroom-core refused next to its field', async () => {
    const fetchMock = stubApi([])
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Add an account' }))
    const form = screen.getByRole('form', { name: 'Add an account' })
    await userEvent.type(within(form).getByLabelText('Account id'), 'Home')
    await userEvent.type(within(form).getByLabelText('Email address'), 'me@home.example')
    await userEvent.type(within(form).getByLabelText('Login'), 'me@home.example')
    await userEvent.type(within(form).getByLabelText('Password'), 'mailbox secret')
    await userEvent.type(within(form).getByLabelText('IMAP server'), 'imap.home.example')
    await userEvent.type(within(form).getByLabelText('SMTP server'), 'smtp.home.example')
    await userEvent.click(within(form).getByRole('button', { name: 'Add account' }))

    expect(await within(form).findByRole('alert')).toHaveTextContent('Check the highlighted fields.')
    const id = within(form).getByLabelText('Account id')
    expect(id).toHaveAttribute('aria-invalid', 'true')
    expect(id).toHaveAccessibleDescription(
      expect.stringContaining('must be lowercase letters, digits and dashes'),
    )

    await userEvent.clear(id)
    await userEvent.type(id, 'home')
    await userEvent.click(within(form).getByRole('button', { name: 'Add account' }))
    const list = await screen.findByRole('list', { name: 'Mail accounts' })
    expect(within(list).getByText('home')).toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Add an account' })).not.toBeInTheDocument()

    expect(sent(fetchMock, 'POST', '/api/v1/accounts')).toEqual({
      id: 'home',
      login: 'me@home.example',
      password: 'mailbox secret',
      send_mode: 'drafts',
      imap: { host: 'imap.home.example', port: 993, tls: 'implicit' },
      smtp: { host: 'smtp.home.example', port: 465, tls: 'implicit' },
      identities: [{ address: 'me@home.example', name: null }],
      auto_approve: [],
    })
  })

  it('edits an account, keeping the stored password unless one is typed', async () => {
    const fetchMock = stubApi()
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Edit work' }))
    const form = screen.getByRole('form', { name: 'Edit work' })
    expect(within(form).queryByLabelText('Account id')).not.toBeInTheDocument()
    expect(within(form).getByLabelText('Password')).not.toBeRequired()
    await userEvent.selectOptions(within(form).getByLabelText('Sending'), 'Off')
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('button', { name: 'Edit work' })).toHaveFocus()
    const body = sent(fetchMock, 'PUT', '/api/v1/accounts/work')
    expect(body).not.toHaveProperty('password')
    expect(body).toMatchObject({
      send_mode: 'off',
      identities: [{ address: 'alice@example.org', name: 'Alice', default: true }],
    })
  })

  it('tests the login and reports a refusal', async () => {
    stubApi([WORK, { ...WORK, id: 'old', login: 'bob@example.org' }])
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Test the login of work' }))
    expect(await screen.findByRole('status')).toHaveTextContent('The login works.')

    await userEvent.click(screen.getByRole('button', { name: 'Test the login of old' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The server refused the login: Authentication failed.',
    )
  })

  it('removes an account only once confirmed', async () => {
    stubApi()
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Remove work' }))
    expect(screen.getByText('Remove work and its filters? The mail on the server stays.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove for good' })).toHaveFocus()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('button', { name: 'Remove work' })).toHaveFocus()

    await userEvent.click(screen.getByRole('button', { name: 'Remove work' }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove for good' }))
    expect(await screen.findByText('No accounts yet.')).toBeInTheDocument()
  })

  it('turns Run filters on and off', async () => {
    const fetchMock = stubApi()
    renderSignedIn(<AccountsScreen />, ALICE)

    const toggle = await screen.findByRole('switch', { name: 'Run filters on work' })
    expect(toggle).not.toBeChecked()
    expect(toggle).toHaveAccessibleDescription(
      'Off: nothing runs on this account until you turn it on.',
    )
    await userEvent.click(toggle)
    await vi.waitFor(() => {
      expect(toggle).toBeChecked()
    })
    expect(sent(fetchMock, 'PUT', '/api/v1/accounts/work/filter-settings')).toEqual({
      run_filters: true,
    })
    await userEvent.click(toggle)
    await vi.waitFor(() => {
      expect(toggle).not.toBeChecked()
    })
  })
})

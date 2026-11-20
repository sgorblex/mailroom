import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderSignedIn } from '@/test/signed-in.tsx'

import { AccountsScreen } from './accounts-screen.tsx'

const ALICE = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null }

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

interface StoredAccount {
  id: number
  /** The account's own, as stored; `null` when it has none (then listed by its login). */
  name: string | null
  colour: string | null
  authserv_id: string | null
  login: string
  send_mode: string
  imap: { host: string; port: number; tls: string }
  smtp: { host: string; port: number; tls: string }
  identities: { address: string; name?: string | null; default?: boolean }[]
  auto_approve: string[]
  has_password: boolean
  run_filters?: boolean
}

const WORK: StoredAccount = {
  id: 1,
  name: null,
  colour: null,
  authserv_id: null,
  login: 'work',
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

/** As the API lists it: the name as stored, and what to call it (its name, else its login). */
function listed(account: StoredAccount): StoredAccount & { display_name: string } {
  return { ...account, display_name: account.name ?? account.login }
}

const NAME_TAKEN = "Another account is already named 'Home'."

/**
 * Whether another of `accounts` than `id` has `name`, NFKC-normalised, trimmed and whatever its
 * case, as the API compares them; never when it is `stored`, the account's own name, kept.
 */
function nameTaken(
  accounts: StoredAccount[],
  id: number | undefined,
  name: unknown,
  stored: string | null = null,
): boolean {
  const key = (value: string): string => value.normalize('NFKC').trim().toLowerCase()
  return (
    typeof name === 'string' &&
    (stored === null || key(stored) !== key(name)) &&
    accounts.some((other) => other.id !== id && other.name !== null && key(other.name) === key(name))
  )
}

/** The API's refusal of a name another account has: 409, about the name field. */
function nameTakenResponse(): Response {
  const issues = [{ path: ['name'], message: NAME_TAKEN }]
  return json(409, { detail: { code: 'account_name_taken', message: NAME_TAKEN, issues } })
}

/** What the fake autoconfig route answers for an address; none means nothing found. */
type Autoconfig = (address: string) => Response | Promise<Response>

const NOTHING_FOUND: Autoconfig = (address) =>
  json(200, { domain: address.slice(address.indexOf('@') + 1), suggestion: null })

/** What the fake quota route answers for an account; by default, that the server reports none. */
type QuotaAnswer = (account: StoredAccount) => Response

const NO_QUOTA: QuotaAnswer = (account) => json(200, { account_id: account.id, storage: null, messages: null })

/**
 * A fake accounts API holding `initial`: it numbers a new account itself, never reusing an id,
 * refuses a login without an `@` as a duplicate, as mailroom-core refuses one, and refuses a name
 * another account has, as the API does.
 */
function stubApi(
  initial: StoredAccount[] = [WORK],
  autoconfig: Autoconfig = NOTHING_FOUND,
  quota: QuotaAnswer = NO_QUOTA,
): ReturnType<typeof vi.fn> {
  let accounts = [...initial]
  let lastId = Math.max(0, ...initial.map((account) => account.id))
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const body = init?.body == null ? {} : (JSON.parse(init.body as string) as Body)
    if (path.startsWith('/api/v1/accounts/autoconfig?') && method === 'GET') {
      return Promise.resolve(autoconfig(new URLSearchParams(path.split('?')[1]).get('address') ?? ''))
    }
    if (path === '/api/v1/accounts' && method === 'GET') {
      return Promise.resolve(json(200, accounts.map(listed)))
    }
    if (path === '/api/v1/accounts' && method === 'POST') {
      if (!(body['login'] as string).includes('@')) {
        const issues = [{ path: ['login'], message: `duplicate login "${body['login'] as string}"` }]
        return Promise.resolve(
          json(400, { detail: { code: 'invalid_account', message: 'login: …', issues } }),
        )
      }
      if (nameTaken(accounts, undefined, body['name'])) {
        return Promise.resolve(nameTakenResponse())
      }
      const fields = withoutPassword(body)
      lastId += 1
      const created = { ...fields, id: lastId, has_password: true, run_filters: false } as unknown as StoredAccount
      accounts = [...accounts, created]
      return Promise.resolve(json(201, listed(created)))
    }
    const match = /^\/api\/v1\/accounts\/([^/]+)(\/.*)?$/.exec(path)
    const id = Number(match?.[1])
    const rest = match?.[2] ?? ''
    const account = accounts.find((candidate) => candidate.id === id)
    if (account === undefined) {
      return Promise.resolve(json(404, { detail: 'No such account' }))
    }
    if (rest === '' && method === 'PUT') {
      if (nameTaken(accounts, id, body['name'], account.name)) {
        return Promise.resolve(nameTakenResponse())
      }
      const fields = withoutPassword(body)
      const changed = { ...account, ...fields }
      accounts = accounts.map((candidate) => (candidate.id === id ? changed : candidate))
      return Promise.resolve(json(200, listed(changed)))
    }
    if (rest === '' && method === 'DELETE') {
      accounts = accounts.filter((candidate) => candidate.id !== id)
      return Promise.resolve(new Response(null, { status: 204 }))
    }
    if (rest === '/test' && method === 'POST') {
      return Promise.resolve(
        json(200, account.login.startsWith('bob')
          ? { ok: false, code: 'auth_failed', message: 'The mail server rejected the login for this account.' }
          : account.login.startsWith('carol')
            ? {
                ok: false,
                code: 'unreachable',
                message: 'The mail server for this account could not be reached. Its TLS certificate did not verify.',
              }
            : { ok: true, code: null, message: null }),
      )
    }
    if (rest === '/quota' && method === 'GET') {
      return Promise.resolve(quota(account))
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

const SUGGESTION = {
  source: 'ispdb',
  origin: 'https://autoconfig.thunderbird.net/v1.1/home.example',
  provider: null,
  supported: true,
  note: null,
  login: 'local_part',
  imap: { host: 'mail.home.example', port: 143, tls: 'starttls', outside_domain: false },
  smtp: { host: 'mail.home.example', port: 587, tls: 'starttls', outside_domain: false },
  authserv_id: null,
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
    // The server numbers the account: no id to choose.
    expect(within(form).queryByLabelText(/\bid\b/i)).not.toBeInTheDocument()
    // It starts from the address alone.
    expect(within(form).queryByLabelText('Login')).not.toBeInTheDocument()
    await userEvent.type(within(form).getByLabelText('Email address'), 'me@home.example')
    await userEvent.click(within(form).getByRole('button', { name: 'Enter settings manually' }))
    expect(within(form).getByLabelText('Login')).toHaveFocus()
    await userEvent.type(within(form).getByLabelText('Login'), 'me')
    await userEvent.type(within(form).getByLabelText('Password'), 'mailbox secret')
    await userEvent.type(within(form).getByLabelText('IMAP server'), 'imap.home.example')
    await userEvent.type(within(form).getByLabelText('SMTP server'), 'smtp.home.example')
    await userEvent.click(within(form).getByRole('button', { name: 'Add account' }))

    expect(await within(form).findByRole('alert')).toHaveTextContent('Check the highlighted fields.')
    const login = within(form).getByLabelText('Login')
    expect(login).toHaveAttribute('aria-invalid', 'true')
    expect(login).toHaveAccessibleDescription(expect.stringContaining('duplicate login "me"'))

    await userEvent.type(login, '@home.example')
    await userEvent.click(within(form).getByRole('button', { name: 'Add account' }))
    const list = await screen.findByRole('list', { name: 'Mail accounts' })
    // Unnamed, it goes by its login.
    expect(within(list).getByText('me@home.example')).toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Add an account' })).not.toBeInTheDocument()

    expect(sent(fetchMock, 'POST', '/api/v1/accounts')).toEqual({
      name: null,
      colour: null,
      authserv_id: null,
      login: 'me@home.example',
      password: 'mailbox secret',
      send_mode: 'drafts',
      imap: { host: 'imap.home.example', port: 993, tls: 'implicit' },
      smtp: { host: 'smtp.home.example', port: 465, tls: 'implicit' },
      identities: [{ address: 'me@home.example', name: null }],
      auto_approve: [],
    })
  })

  it('fills the servers and login from the settings found, saying where they came from', async () => {
    const fetchMock = stubApi([], () => json(200, { domain: 'home.example', suggestion: SUGGESTION }))
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Add an account' }))
    const form = screen.getByRole('form', { name: 'Add an account' })
    // Enter in the address field looks it up.
    await userEvent.type(within(form).getByLabelText('Email address'), 'me@home.example{Enter}')

    expect(await within(form).findByRole('status')).toHaveTextContent(
      "Settings from Thunderbird's provider database (https://autoconfig.thunderbird.net/v1.1/home.example). " +
        'Check them before adding the account.',
    )
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/accounts/autoconfig?address=me%40home.example',
      expect.objectContaining({ method: 'GET' }),
    )
    expect(within(form).getByLabelText('Login')).toHaveValue('me')
    expect(within(form).getByLabelText('IMAP server')).toHaveValue('mail.home.example')
    expect(within(form).getByLabelText('IMAP port')).toHaveValue(143)
    expect(within(form).getByLabelText('IMAP security')).toHaveDisplayValue('STARTTLS')
    expect(within(form).getByLabelText('SMTP port')).toHaveValue(587)
    expect(within(form).queryByRole('button', { name: 'Enter settings manually' })).not.toBeInTheDocument()

    // The address stays, as the first identity.
    expect(within(form).getByLabelText('Email address')).toHaveValue('me@home.example')
  })

  /** The domain's own document, naming `imap` and `smtp` hosts outside it. */
  function documentNaming(imap: string, smtp: string): Autoconfig {
    const suggestion = {
      ...SUGGESTION,
      source: 'autoconfig',
      origin: 'https://autoconfig.home.example/mail/config-v1.1.xml',
      login: 'address',
      imap: { host: imap, port: 993, tls: 'implicit', outside_domain: !imap.endsWith('.home.example') },
      smtp: { host: smtp, port: 465, tls: 'implicit', outside_domain: !smtp.endsWith('.home.example') },
    }
    return () => json(200, { domain: 'home.example', suggestion })
  }

  it('asks before filling a server outside the domain, and fills it on Use', async () => {
    const fetchMock = stubApi([], documentNaming('imap.hoster.example', 'smtp.home.example'))
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Add an account' }))
    const form = screen.getByRole('form', { name: 'Add an account' })
    await userEvent.type(within(form).getByLabelText('Email address'), 'me@home.example{Enter}')

    expect(await within(form).findByRole('status')).toHaveTextContent(
      'Settings published by the domain (https://autoconfig.home.example/mail/config-v1.1.xml). ' +
        'These settings send your password to a server outside home.example: imap.hoster.example. ' +
        'Use them only if you know your mail is hosted there.',
    )
    // Nothing is filled until the user says so.
    expect(within(form).queryByLabelText('IMAP server')).not.toBeInTheDocument()
    expect(within(form).queryByRole('group', { name: 'Your provider' })).not.toBeInTheDocument()

    await userEvent.click(within(form).getByRole('button', { name: 'Use' }))
    expect(within(form).getByLabelText('IMAP server')).toHaveValue('imap.hoster.example')
    expect(within(form).getByLabelText('SMTP server')).toHaveValue('smtp.home.example')
    expect(within(form).getByLabelText('Login')).toHaveValue('me@home.example')
    expect(within(form).getByLabelText('Password')).toHaveFocus()
    expect(within(form).getByRole('status')).toHaveTextContent(/^Settings published by the domain .* Check them/)
    expect(within(form).queryByRole('button', { name: 'Use' })).not.toBeInTheDocument()

    await userEvent.type(within(form).getByLabelText('Password'), 'mailbox secret')
    await userEvent.click(within(form).getByRole('button', { name: 'Add account' }))
    await screen.findByRole('list', { name: 'Mail accounts' })
    // The flag is the lookup's, not the account's: the saved servers carry no outside_domain.
    expect(sent(fetchMock, 'POST', '/api/v1/accounts')).toMatchObject({
      imap: { host: 'imap.hoster.example', port: 993, tls: 'implicit' },
    })
    expect(sent(fetchMock, 'POST', '/api/v1/accounts')['imap']).not.toHaveProperty('outside_domain')
  })

  it("fills nothing on Don't use, offering the presets and manual entry", async () => {
    stubApi([], documentNaming('imap.hoster.example', 'smtp.relay.example'))
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Add an account' }))
    const form = screen.getByRole('form', { name: 'Add an account' })
    await userEvent.type(within(form).getByLabelText('Email address'), 'me@home.example{Enter}')
    // Both hosts named, in one sentence.
    expect(await within(form).findByRole('status')).toHaveTextContent(
      'These settings send your password to servers outside home.example: imap.hoster.example, smtp.relay.example.',
    )

    await userEvent.click(within(form).getByRole('button', { name: "Don't use" }))
    expect(within(form).getByRole('status')).toHaveTextContent(
      'Settings not used. Pick your provider or enter the settings yourself.',
    )
    expect(within(form).queryByRole('button', { name: 'Use' })).not.toBeInTheDocument()
    expect(within(form).getByRole('group', { name: 'Your provider' })).toBeInTheDocument()
    await userEvent.click(within(form).getByRole('button', { name: 'Enter settings manually' }))
    expect(within(form).getByLabelText('IMAP server')).toHaveValue('')
    expect(within(form).getByLabelText('Login')).toHaveValue('')
  })

  it('drops a lookup still running when the user enters the settings by hand', async () => {
    let answer: (response: Response) => void = () => undefined
    stubApi([], () => new Promise((resolve) => (answer = resolve)))
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Add an account' }))
    const form = screen.getByRole('form', { name: 'Add an account' })
    await userEvent.type(within(form).getByLabelText('Email address'), 'me@home.example')
    await userEvent.click(within(form).getByRole('button', { name: 'Find settings' }))
    await userEvent.click(within(form).getByRole('button', { name: 'Enter settings manually' }))
    await userEvent.type(within(form).getByLabelText('IMAP server'), 'imap.typed.example')
    answer(json(200, { domain: 'home.example', suggestion: SUGGESTION }))
    await new Promise((resolve) => setTimeout(resolve, 50))

    expect(within(form).getByRole('status')).toBeEmptyDOMElement()
    expect(within(form).getByLabelText('IMAP server')).toHaveValue('imap.typed.example')
    expect(within(form).getByLabelText('Login')).toHaveValue('')
  })

  it('forgets the settings found once the address changes, keeping the fields filled', async () => {
    stubApi([], () => json(200, { domain: 'home.example', suggestion: SUGGESTION }))
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Add an account' }))
    const form = screen.getByRole('form', { name: 'Add an account' })
    await userEvent.type(within(form).getByLabelText('Email address'), 'me@home.example{Enter}')
    expect(await within(form).findByRole('status')).toHaveTextContent(/^Settings from/)

    await userEvent.type(within(form).getByLabelText('Email address'), 'x')
    expect(within(form).getByRole('status')).toBeEmptyDOMElement()
    expect(within(form).getByLabelText('IMAP server')).toHaveValue('mail.home.example')
  })

  it('offers the presets when nothing is found', async () => {
    const fetchMock = stubApi([])
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Add an account' }))
    const form = screen.getByRole('form', { name: 'Add an account' })
    await userEvent.type(within(form).getByLabelText('Email address'), 'me@mydomain.example')
    await userEvent.click(within(form).getByRole('button', { name: 'Find settings' }))

    expect(await within(form).findByText(/No settings found for mydomain\.example\./)).toBeInTheDocument()
    expect(within(form).queryByLabelText('Login')).not.toBeInTheDocument()
    const presets = within(form).getByRole('group', { name: 'Your provider' })
    await userEvent.click(within(presets).getByRole('button', { name: 'Use Gmail' }))

    expect(within(form).getByRole('status')).toHaveTextContent(
      "Mailroom's own settings for Gmail. Gmail wants an app password, created in your Gmail account's " +
        'security settings, not your usual password.',
    )
    expect(within(form).getByLabelText('Password')).toHaveFocus()
    expect(within(form).getByLabelText('Login')).toHaveValue('me@mydomain.example')
    await userEvent.type(within(form).getByLabelText('Password'), 'app password')
    await userEvent.click(within(form).getByRole('button', { name: 'Add account' }))
    await screen.findByRole('list', { name: 'Mail accounts' })
    expect(sent(fetchMock, 'POST', '/api/v1/accounts')).toMatchObject({
      imap: { host: 'imap.gmail.com', port: 993, tls: 'implicit' },
      smtp: { host: 'smtp.gmail.com', port: 465, tls: 'implicit' },
      // Gmail's authentication server comes with its preset.
      authserv_id: 'mx.google.com',
    })
  })

  it('says Microsoft is not supported yet, leaving manual entry', async () => {
    const suggestion = {
      source: 'preset',
      origin: null,
      provider: 'Microsoft',
      supported: false,
      note: 'oauth_required',
      login: 'address',
      imap: { host: 'outlook.office365.com', port: 993, tls: 'implicit', outside_domain: false },
      smtp: { host: 'smtp.office365.com', port: 587, tls: 'starttls', outside_domain: false },
      authserv_id: null,
    }
    stubApi([], () => json(200, { domain: 'outlook.com', suggestion }))
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Add an account' }))
    const form = screen.getByRole('form', { name: 'Add an account' })
    await userEvent.type(within(form).getByLabelText('Email address'), 'me@outlook.com')
    await userEvent.click(within(form).getByRole('button', { name: 'Find settings' }))

    expect(await within(form).findByText(/Microsoft accounts are not supported yet/)).toBeInTheDocument()
    expect(within(form).queryByLabelText('IMAP server')).not.toBeInTheDocument()
    expect(within(form).queryByRole('group', { name: 'Your provider' })).not.toBeInTheDocument()
    await userEvent.click(within(form).getByRole('button', { name: 'Enter settings manually' }))
    expect(within(form).getByLabelText('IMAP server')).toHaveValue('')
  })

  it.each([
    {
      answer: () => json(429, { detail: { code: 'autoconfig_busy', message: 'Too many lookups' } }),
      shown: 'Too many lookups: try again in a minute.',
    },
    {
      answer: () => json(400, { detail: { code: 'invalid_address', message: 'Not an email address.' } }),
      shown: 'Enter a whole email address, such as you@example.org.',
    },
  ])('says why the lookup failed and offers the presets ($shown)', async ({ answer, shown }) => {
    stubApi([], answer)
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Add an account' }))
    const form = screen.getByRole('form', { name: 'Add an account' })
    await userEvent.type(within(form).getByLabelText('Email address'), 'me@home.example')
    await userEvent.click(within(form).getByRole('button', { name: 'Find settings' }))

    expect(await within(form).findByText(shown)).toBeInTheDocument()
    expect(within(form).getByRole('group', { name: 'Your provider' })).toBeInTheDocument()
    expect(within(form).getByRole('button', { name: 'Enter settings manually' })).toBeInTheDocument()
  })

  it('edits an account, keeping the stored password unless one is typed', async () => {
    const fetchMock = stubApi()
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Edit work' }))
    const form = screen.getByRole('form', { name: 'Edit work' })
    expect(within(form).queryByLabelText(/\bid\b/i)).not.toBeInTheDocument()
    expect(within(form).getByLabelText('Password')).not.toBeRequired()
    await userEvent.selectOptions(within(form).getByLabelText('Sending'), 'Off')
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('button', { name: 'Edit work' })).toHaveFocus()
    const body = sent(fetchMock, 'PUT', '/api/v1/accounts/1')
    expect(body).not.toHaveProperty('password')
    expect(body).toMatchObject({
      send_mode: 'off',
      identities: [{ address: 'alice@example.org', name: 'Alice' }],
    })
  })

  it('names an account and gives it a colour; unnamed, it goes by its login', async () => {
    const fetchMock = stubApi()
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Edit work' }))
    const form = screen.getByRole('form', { name: 'Edit work' })
    const name = within(form).getByLabelText('Account name')
    expect(name).toHaveValue('')
    expect(name).toHaveAttribute('placeholder', 'work')
    expect(within(form).getByRole('radio', { name: 'No colour' })).toBeChecked()
    await userEvent.type(name, 'Work mail')
    await userEvent.click(within(form).getByRole('radio', { name: 'Teal' }))
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('button', { name: 'Edit Work mail' })).toHaveFocus()
    expect(sent(fetchMock, 'PUT', '/api/v1/accounts/1')).toMatchObject({ name: 'Work mail', colour: 'teal' })
    const list = screen.getByRole('list', { name: 'Mail accounts' })
    expect(within(list).getByText('Work mail')).toBeInTheDocument()
    expect(within(list).queryByText('work')).not.toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Run filters on Work mail' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Edit Work mail' }))
    const again = screen.getByRole('form', { name: 'Edit Work mail' })
    expect(within(again).getByLabelText('Account name')).toHaveValue('Work mail')
    expect(within(again).getByRole('radio', { name: 'Teal' })).toBeChecked()
    await userEvent.clear(within(again).getByLabelText('Account name'))
    await userEvent.click(within(again).getByRole('radio', { name: 'No colour' }))
    await userEvent.click(within(again).getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('button', { name: 'Edit work' })).toBeInTheDocument()
    expect(sent(fetchMock, 'PUT', '/api/v1/accounts/1')).toMatchObject({ name: null, colour: null })
  })

  it('edits the authentication server, keeping it through other changes and clearing it when emptied', async () => {
    const fetchMock = stubApi([{ ...WORK, authserv_id: 'mx.example.org' }])
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Edit work' }))
    const form = screen.getByRole('form', { name: 'Edit work' })
    const server = within(form).getByLabelText('Authentication server')
    expect(server).toHaveValue('mx.example.org')
    expect(server).toHaveAccessibleDescription(expect.stringContaining('Authentication-Results') as string)
    await userEvent.selectOptions(within(form).getByLabelText('Sending'), 'Off')
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('button', { name: 'Edit work' })).toHaveFocus()
    expect(sent(fetchMock, 'PUT', '/api/v1/accounts/1')).toMatchObject({ authserv_id: 'mx.example.org' })

    await userEvent.click(screen.getByRole('button', { name: 'Edit work' }))
    const again = screen.getByRole('form', { name: 'Edit work' })
    await userEvent.clear(within(again).getByLabelText('Authentication server'))
    await userEvent.type(within(again).getByLabelText('Authentication server'), '   ')
    await userEvent.click(within(again).getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('button', { name: 'Edit work' })).toHaveFocus()
    expect(sent(fetchMock, 'PUT', '/api/v1/accounts/1')).toMatchObject({ authserv_id: null })
  })

  it('keeps the name and colour through every save, even a name that is the login', async () => {
    const fetchMock = stubApi([{ ...WORK, name: 'work', colour: 'amber' }])
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Edit work' }))
    const form = screen.getByRole('form', { name: 'Edit work' })
    expect(within(form).getByLabelText('Account name')).toHaveValue('work')
    expect(within(form).getByRole('radio', { name: 'Amber' })).toBeChecked()
    await userEvent.selectOptions(within(form).getByLabelText('Sending'), 'Off')
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('button', { name: 'Edit work' })).toHaveFocus()
    expect(sent(fetchMock, 'PUT', '/api/v1/accounts/1')).toMatchObject({
      name: 'work',
      colour: 'amber',
      send_mode: 'off',
    })
    const body = sent(fetchMock, 'PUT', '/api/v1/accounts/1')
    expect(body).not.toHaveProperty('display_name')
  })

  it('shows a name another account has as refused on the name field', async () => {
    const fetchMock = stubApi([WORK, { ...WORK, id: 2, name: 'Home', login: 'home' }])
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Edit work' }))
    const form = screen.getByRole('form', { name: 'Edit work' })
    const name = within(form).getByLabelText('Account name')
    await userEvent.type(name, '  HOME ')
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))

    const refused = 'Another of your accounts already has this name.'
    expect(await within(form).findByText(refused)).toBeInTheDocument()
    expect(name).toHaveAttribute('aria-invalid', 'true')
    expect(name).toHaveAccessibleDescription(expect.stringContaining(refused) as string)
    expect(within(form).getByText('Check the highlighted fields.')).toBeInTheDocument()
    expect(within(form).queryByText(NAME_TAKEN)).not.toBeInTheDocument()
    expect(sent(fetchMock, 'PUT', '/api/v1/accounts/1')).toMatchObject({ name: 'HOME' })

    await userEvent.clear(name)
    await userEvent.type(name, 'Work')
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('button', { name: 'Edit Work' })).toHaveFocus()
  })

  it('edits every identity: adds one, picks the default and removes another', async () => {
    const fetchMock = stubApi([
      {
        ...WORK,
        identities: [
          { address: 'alice@example.org', name: 'Alice', default: true },
          { address: 'boss@example.org', name: 'The boss', default: false },
        ],
      },
    ])
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Edit work' }))
    const form = screen.getByRole('form', { name: 'Edit work' })
    expect(within(form).getByLabelText('Other address 1')).toHaveValue('boss@example.org')
    expect(within(form).getByLabelText('Name for other address 1')).toHaveValue('The boss')
    await userEvent.click(within(form).getByRole('button', { name: 'Add another address' }))
    await userEvent.type(within(form).getByLabelText('Other address 2'), 'info@example.org')
    await userEvent.selectOptions(within(form).getByLabelText('Send as by default'), 'info@example.org')
    await userEvent.click(within(form).getByRole('button', { name: 'Remove other address 1' }))
    expect(within(form).getByRole('button', { name: 'Add another address' })).toHaveFocus()
    expect(within(form).getByLabelText('Other address 1')).toHaveValue('info@example.org')
    expect(within(form).getByLabelText('Send as by default')).toHaveDisplayValue('info@example.org')
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('button', { name: 'Edit work' })).toBeInTheDocument()
    expect(sent(fetchMock, 'PUT', '/api/v1/accounts/1')).toMatchObject({
      identities: [
        { address: 'alice@example.org', name: 'Alice', default: false },
        { address: 'info@example.org', name: null, default: true },
      ],
    })
  })

  it('shows a refused name and colour next to their controls', async () => {
    stubApi()
    const issues = [
      { path: ['name'], message: 'must not be blank' },
      { path: ['colour'], message: 'is not one of the colours' },
    ]
    const base = vi.mocked(fetch)
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, init?: RequestInit) =>
        init?.method === 'PUT'
          ? Promise.resolve(json(400, { detail: { code: 'invalid_account', message: '…', issues } }))
          : base(path, init),
      ),
    )
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Edit work' }))
    const form = screen.getByRole('form', { name: 'Edit work' })
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))

    const name = within(form).getByLabelText('Account name')
    expect(await within(form).findByText('must not be blank')).toBeInTheDocument()
    expect(name).toHaveAttribute('aria-invalid', 'true')
    expect(name).toHaveAccessibleDescription(expect.stringContaining('must not be blank') as string)
    expect(within(form).getByRole('group', { name: 'Colour' })).toHaveAccessibleDescription('is not one of the colours')
  })

  it('marks the identity a refusal is about', async () => {
    stubApi([{ ...WORK, identities: [...WORK.identities, { address: 'boss@example.org', default: false }] }])
    const issues = [{ path: ['identities', 1, 'address'], message: 'already used by account "home"' }]
    const base = vi.mocked(fetch)
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, init?: RequestInit) =>
        init?.method === 'PUT'
          ? Promise.resolve(json(400, { detail: { code: 'invalid_account', message: '…', issues } }))
          : base(path, init),
      ),
    )
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Edit work' }))
    const form = screen.getByRole('form', { name: 'Edit work' })
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))

    expect(await within(form).findByText('already used by account "home"')).toBeInTheDocument()
    expect(within(form).getByLabelText('Other address 1')).toHaveAttribute('aria-invalid', 'true')
    expect(within(form).getByLabelText('Email address')).toHaveAttribute('aria-invalid', 'false')

    // Its place is gone once it is removed: no issue moves onto another identity.
    await userEvent.click(within(form).getByRole('button', { name: 'Remove other address 1' }))
    expect(within(form).queryByText('already used by account "home"')).not.toBeInTheDocument()
  })

  it('tests the login and reports a refusal', async () => {
    stubApi([WORK, { ...WORK, id: 2, name: 'old', login: 'bob@example.org' }])
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Test the login of work' }))
    expect(await screen.findByRole('status')).toHaveTextContent('The login works.')

    await userEvent.click(screen.getByRole('button', { name: 'Test the login of old' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The mail server rejected the login for this account. Check its password in Accounts.',
    )
  })

  it('says when the login test met a certificate that did not verify', async () => {
    stubApi([{ ...WORK, name: 'selfsigned', login: 'carol@example.org' }])
    renderSignedIn(<AccountsScreen />, ALICE)

    await userEvent.click(await screen.findByRole('button', { name: 'Test the login of selfsigned' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The mail server for this account could not be reached. Its TLS certificate did not verify.',
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
    expect(sent(fetchMock, 'PUT', '/api/v1/accounts/1/filter-settings')).toEqual({
      run_filters: true,
    })
    await userEvent.click(toggle)
    await vi.waitFor(() => {
      expect(toggle).not.toBeChecked()
    })
  })

  it('lists an account without Run filters where the instance runs no filters', async () => {
    stubApi([{ ...WORK, run_filters: undefined }])
    renderSignedIn(<AccountsScreen />, ALICE, ['mail', 'mcp'])

    expect(await screen.findByRole('button', { name: 'Edit work' })).toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
    expect(screen.queryByText('Run filters')).not.toBeInTheDocument()
  })

  it.each([
    { features: ['mcp' as const], shown: true },
    { features: ['filters' as const], shown: false },
  ])('asks for the send mode only where something sends ($features)', async ({ features, shown }) => {
    const fetchMock = stubApi([{ ...WORK, send_mode: 'approval' }])
    renderSignedIn(<AccountsScreen />, ALICE, features)

    await userEvent.click(await screen.findByRole('button', { name: 'Edit work' }))
    const form = screen.getByRole('form', { name: 'Edit work' })
    expect(within(form).queryByLabelText('Sending') !== null).toBe(shown)
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))
    await screen.findByRole('button', { name: 'Edit work' })
    expect(sent(fetchMock, 'PUT', '/api/v1/accounts/1')).toMatchObject({ send_mode: 'approval' })
  })
  it("shows each account's usage, warning from 90% on", async () => {
    const GiB = 1024 ** 3
    stubApi([WORK, { ...WORK, id: 2, name: 'full', login: 'full@example.org' }], undefined, (account) =>
      json(200, {
        account_id: account.id,
        storage: account.id === 1 ? { used: GiB / 2, limit: 2 * GiB } : { used: 0.9 * GiB, limit: GiB },
        messages: account.id === 1 ? { used: 1200, limit: 10000 } : null,
      }),
    )
    renderSignedIn(<AccountsScreen />, ALICE)

    const storage = await screen.findByRole('meter', { name: 'Storage used by work' })
    expect(storage).toHaveAttribute('aria-valuetext', '512 MB of 2 GB (25%)')
    expect(storage).not.toHaveAttribute('data-warning')
    expect(storage).toHaveAttribute('aria-valuenow', '25')
    expect(storage).toHaveAttribute('aria-valuemax', '100')
    expect(screen.getByRole('meter', { name: 'Messages kept by work' })).toHaveAttribute(
      'aria-valuetext',
      '1,200 of 10,000 (12%)',
    )

    const full = await screen.findByRole('meter', { name: 'Storage used by full' })
    expect(full).toHaveAttribute('data-warning', 'nearly-full')
    expect(full).toHaveAttribute('aria-valuetext', 'Nearly full: 921.6 MB of 1 GB (90%)')
    expect(screen.queryByRole('meter', { name: 'Messages kept by full' })).not.toBeInTheDocument()
  })

  it('shows the real share past the limit, and a limit of 0, as over quota with the bar full', async () => {
    stubApi([WORK, { ...WORK, id: 2, name: 'none', login: 'none@example.org' }], undefined, (account) =>
      json(200, {
        account_id: account.id,
        storage: account.id === 1 ? { used: 1.1 * 1024 ** 3, limit: 1024 ** 3 } : { used: 5 * 1024 ** 3, limit: 0 },
        messages: account.id === 1 ? { used: 0, limit: 0 } : null,
      }),
    )
    renderSignedIn(<AccountsScreen />, ALICE)

    const over = await screen.findByRole('meter', { name: 'Storage used by work' })
    expect(over).toHaveAttribute('aria-valuetext', 'Over quota: 1.1 GB of 1 GB (110%)')
    expect(over).toHaveAttribute('aria-valuenow', '100')
    expect(over).toHaveAttribute('data-warning', 'over')
    expect(over.firstElementChild).toHaveStyle({ width: '100%' })
    expect(screen.getByText('Over quota: 1.1 GB of 1 GB (110%)')).toBeInTheDocument()

    const empty = screen.getByRole('meter', { name: 'Messages kept by work' })
    expect(empty).toHaveAttribute('aria-valuetext', 'Over quota: 0 of 0')
    expect(empty).toHaveAttribute('aria-valuenow', '100')

    const zero = await screen.findByRole('meter', { name: 'Storage used by none' })
    expect(zero).toHaveAttribute('aria-valuetext', 'Over quota: 5 GB of 0 byte')
    expect(zero).toHaveAttribute('data-warning', 'over')
  })

  it('says the usage is unknown where the server reports none', async () => {
    stubApi()
    renderSignedIn(<AccountsScreen />, ALICE)
    expect(await screen.findByText('Usage unknown: the mail server does not report it.')).toBeInTheDocument()
    expect(screen.queryByRole('meter')).not.toBeInTheDocument()
  })

  it('shows a usage that failed quietly, the other accounts unaffected, and asks again', async () => {
    let down = true
    stubApi([WORK, { ...WORK, id: 2, name: 'down', login: 'down@example.org' }], undefined, (account) =>
      account.id === 2 && down
        ? json(502, { detail: { code: 'unreachable', message: 'The mail server for this account could not be reached.' } })
        : json(200, { account_id: account.id, storage: { used: 1024, limit: 2048 }, messages: null }),
    )
    renderSignedIn(<AccountsScreen />, ALICE)

    const list = await screen.findByRole('list', { name: 'Mail accounts' })
    expect(
      await within(list).findByText(/^Usage not available: The mail server for this account could not be reached\./),
    ).toBeInTheDocument()
    expect(await screen.findByRole('meter', { name: 'Storage used by work' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    down = false
    await userEvent.click(within(list).getByRole('button', { name: 'Check again' }))
    expect(await screen.findByRole('meter', { name: 'Storage used by down' })).toBeInTheDocument()
  })
})

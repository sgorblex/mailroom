import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ApiToken, ApiTokens as Listing, NewApiToken } from '@/api/api-tokens.ts'
import { expectNoAxeViolations } from '@/test/axe.ts'
import { renderSignedIn } from '@/test/signed-in.tsx'

import { ApiTokens } from './api-tokens.tsx'

const ALICE = { username: 'alice', is_admin: false, time_zone: 'UTC', mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }
const PATH = '/api/v1/me/api-tokens'
const RAW = 'mrt_0a1b2c3d_the-secret'

const GROUPS: Listing['groups'] = [
  { name: 'mail_read', description: 'list, search and read mail', requires: [], default: true },
  { name: 'mail_send', description: 'send mail', requires: ['mail_read'], default: false },
]

const ACCOUNTS: Listing['accounts'] = [
  { id: 1, name: 'Work' },
  { id: 2, name: 'Home' },
]

function token(id: number, fields: Partial<ApiToken> = {}): ApiToken {
  return {
    id,
    name: `Script ${String(id)}`,
    prefix: 'mrt_0a1b2c3d',
    created_at: '2026-11-20T10:00:00Z',
    last_used_at: null,
    expires_at: null,
    expired: false,
    groups: ['mail_read'],
    account_ids: null,
    send_ceiling: null,
    ...fields,
  }
}

function answer(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), { status })
}

/** A fake API holding `tokens`; a POST adds one, or is refused with `refusal`, and a DELETE answers `revokeStatus`. */
function stubApi(tokens: ApiToken[], { groups = GROUPS, refusal = null as string | null, revokeStatus = 204 } = {}) {
  let held = tokens
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    if (path === PATH && init?.method === 'POST') {
      if (refusal !== null) {
        return Promise.resolve(answer(409, { detail: { code: refusal, message: 'Refused' } }))
      }
      const { expires_in_days, ...made } = JSON.parse(init.body as string) as NewApiToken
      const created = token(9, { ...made, expires_at: expires_in_days === null ? null : '2099-01-01T00:00:00Z' })
      held = [created, ...held]
      return Promise.resolve(answer(201, { ...created, token: RAW }))
    }
    if (path === PATH) {
      return Promise.resolve(answer(200, { tokens: held, groups, accounts: ACCOUNTS }))
    }
    if (init?.method === 'DELETE') {
      if (revokeStatus !== 500) {
        held = held.filter((item) => `${PATH}/${String(item.id)}` !== path)
      }
      return Promise.resolve(answer(revokeStatus, { detail: 'Server error' }))
    }
    return Promise.resolve(answer(404, { detail: 'Not Found' }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function sent(fetchMock: ReturnType<typeof stubApi>): unknown {
  const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
  return call === undefined ? null : JSON.parse(call[1]?.body as string)
}

async function openForm(): Promise<HTMLElement> {
  await userEvent.click(await screen.findByRole('button', { name: 'New token' }))
  return screen.getByRole('form', { name: 'New token' })
}

async function makeOne(): Promise<void> {
  const form = await openForm()
  await userEvent.type(within(form).getByLabelText('Name'), 'Backup')
  await userEvent.click(within(form).getByRole('checkbox', { name: 'Read mail' }))
  await userEvent.click(within(form).getByRole('button', { name: 'Create token' }))
}

afterEach(() => {
  vi.unstubAllGlobals()
  Reflect.deleteProperty(navigator, 'clipboard')
})

describe('ApiTokens', () => {
  it('lists the tokens with what each may do', async () => {
    stubApi([
      token(1, { account_ids: [2], send_ceiling: 'drafts', groups: ['mail_read', 'mail_send'], last_used_at: '2026-11-21T10:00:00Z', expires_at: '2099-01-01T00:00:00Z' }),
      token(2, { expires_at: '2020-01-01T00:00:00Z', expired: true, account_ids: [], groups: [] }),
    ])
    renderSignedIn(<ApiTokens />, ALICE)
    const [one, two] = within(await screen.findByRole('list', { name: 'API tokens' })).getAllByRole('listitem')
    if (one === undefined || two === undefined) {
      throw new Error('two tokens expected')
    }
    expect(within(one).getByText(/^mrt_0a1b2c3d… · created .* · last used .* · expires /)).toBeInTheDocument()
    expect(within(one).getByText('Accounts: Home')).toBeInTheDocument()
    expect(within(one).getByText(/^Sending, at most: /)).toBeInTheDocument()
    expect(within(two).getByText(/never used · expired /)).toBeInTheDocument()
    expect(within(two).getByText('No accounts: those it had were removed.')).toBeInTheDocument()
    expect(within(two).getByText('None of its tools are offered here.')).toBeInTheDocument()
    await expectNoAxeViolations()
  })

  it('makes one with what is chosen, and nothing chosen beforehand', async () => {
    const fetchMock = stubApi([])
    renderSignedIn(<ApiTokens />, ALICE)
    const form = await openForm()
    const create = within(form).getByRole('button', { name: 'Create token' })
    expect(within(form).getByLabelText('Name')).toHaveFocus()
    for (const box of within(form).getAllByRole('checkbox')) {
      expect(box).not.toBeChecked()
    }
    expect(create).toBeDisabled()
    await userEvent.type(within(form).getByLabelText('Name'), ' Backup ')
    expect(create).toBeDisabled()
    await userEvent.click(within(form).getByRole('checkbox', { name: 'Read mail' }))
    await userEvent.click(within(form).getByRole('checkbox', { name: 'Send mail' }))
    await userEvent.selectOptions(within(form).getByLabelText('Sending, at most'), 'drafts')
    await userEvent.click(within(form).getByRole('radio', { name: 'Only these' }))
    expect(create).toBeDisabled()
    await userEvent.click(within(form).getByRole('checkbox', { name: 'Home' }))
    await userEvent.selectOptions(within(form).getByLabelText('Expires'), 'Never')
    await expectNoAxeViolations()
    await userEvent.click(create)
    expect(await screen.findByDisplayValue(RAW)).toHaveFocus()
    expect(sent(fetchMock)).toEqual({
      name: 'Backup',
      groups: ['mail_read', 'mail_send'],
      account_ids: [2],
      send_ceiling: 'drafts',
      expires_in_days: null,
    })
  })

  it('expires in 90 days unless told otherwise, and sends no ceiling that limits nothing', async () => {
    const fetchMock = stubApi([])
    renderSignedIn(<ApiTokens />, ALICE)
    await makeOne()
    await screen.findByDisplayValue(RAW)
    expect(sent(fetchMock)).toMatchObject({ expires_in_days: 90, send_ceiling: null, account_ids: null })
  })

  it('shows the token once, to copy', async () => {
    stubApi([])
    renderSignedIn(<ApiTokens />, ALICE)
    await makeOne()
    const field = await screen.findByRole('textbox', { name: 'Your new token, Backup' })
    expect(field).toHaveAttribute('readonly')
    const writeText = vi.fn(() => Promise.resolve())
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    await userEvent.click(screen.getByRole('button', { name: 'Copy' }))
    expect(writeText).toHaveBeenCalledWith(RAW)
    expect(await screen.findByText('Copied.')).toBeInTheDocument()
    await expectNoAxeViolations()
    await userEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.queryByDisplayValue(RAW)).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'API tokens' })).toHaveFocus()
    expect(screen.getByText('Backup')).toBeInTheDocument()
  })

  it('leaves it selected where nothing can be copied', async () => {
    stubApi([])
    renderSignedIn(<ApiTokens />, ALICE)
    await makeOne()
    const field = await screen.findByDisplayValue<HTMLInputElement>(RAW)
    const writeText = (): Promise<void> => Promise.reject(new Error('denied'))
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    await userEvent.click(screen.getByRole('button', { name: 'Copy' }))
    expect(await screen.findByText('Could not copy it: it is selected, copy it yourself.')).toBeInTheDocument()
    expect(field).toHaveFocus()
    expect(field.selectionEnd).toBe(RAW.length)
  })

  it('says when there are too many, and keeps the form', async () => {
    stubApi([token(1)], { refusal: 'too_many_tokens' })
    renderSignedIn(<ApiTokens />, ALICE)
    await makeOne()
    expect(await screen.findByRole('alert')).toHaveTextContent('You have as many tokens as you can')
    expect(screen.getByLabelText('Name')).toHaveValue('Backup')
  })

  it('gives focus back when the form is cancelled', async () => {
    stubApi([])
    renderSignedIn(<ApiTokens />, ALICE)
    const form = await openForm()
    await userEvent.click(within(form).getByRole('button', { name: 'Cancel' }))
    await vi.waitFor(() => {
      expect(screen.getByRole('button', { name: 'New token' })).toHaveFocus()
    })
  })

  it('revokes one after a confirm', async () => {
    const fetchMock = stubApi([token(1), token(2)])
    renderSignedIn(<ApiTokens />, ALICE)
    await userEvent.click(await screen.findByRole('button', { name: 'Revoke Script 1' }))
    expect(screen.getByText(/^Revoke Script 1\? It stops working at once/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await vi.waitFor(() => {
      expect(screen.getByRole('button', { name: 'Revoke Script 1' })).toHaveFocus()
    })
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'DELETE')).toEqual([])
    await userEvent.click(screen.getByRole('button', { name: 'Revoke Script 1' }))
    await userEvent.click(screen.getByRole('button', { name: 'Revoke' }))
    await vi.waitFor(() => {
      expect(screen.getAllByRole('listitem')).toHaveLength(1)
    })
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'DELETE').map(([path]) => path)).toEqual([`${PATH}/1`])
    expect(screen.getByRole('heading', { name: 'API tokens' })).toHaveFocus()
  })

  it('takes one already gone as revoked', async () => {
    stubApi([token(1)], { revokeStatus: 404 })
    renderSignedIn(<ApiTokens />, ALICE)
    await userEvent.click(await screen.findByRole('button', { name: 'Revoke Script 1' }))
    await userEvent.click(screen.getByRole('button', { name: 'Revoke' }))
    await vi.waitFor(() => {
      expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
    })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('says when revoking fails', async () => {
    stubApi([token(1)], { revokeStatus: 500 })
    renderSignedIn(<ApiTokens />, ALICE)
    await userEvent.click(await screen.findByRole('button', { name: 'Revoke Script 1' }))
    await userEvent.click(screen.getByRole('button', { name: 'Revoke' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Server error')
    expect(screen.getByText('Script 1')).toBeInTheDocument()
  })

  it('shows nothing where no token could do anything', async () => {
    stubApi([], { groups: [] })
    renderSignedIn(<ApiTokens />, ALICE)
    expect(screen.getByRole('heading', { name: 'API tokens' })).toBeInTheDocument()
    await vi.waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'API tokens' })).not.toBeInTheDocument()
    })
  })

  it('still lists those left where no new one can be made', async () => {
    stubApi([token(1)], { groups: [] })
    renderSignedIn(<ApiTokens />, ALICE)
    expect(await screen.findByRole('button', { name: 'Revoke Script 1' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'New token' })).not.toBeInTheDocument()
  })

  it('offers to try again when they do not load', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))))
    renderSignedIn(<ApiTokens />, ALICE)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    stubApi([token(1)])
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Script 1')).toBeInTheDocument()
  })
})

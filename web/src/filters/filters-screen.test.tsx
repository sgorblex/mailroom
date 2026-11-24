import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { expectNoAxeViolations } from '@/test/axe.ts'
import { type FakeState, HOME, sentBody, stubFiltersApi, WORK } from '@/test/fake-filters-api.ts'
import { renderFiltersApp } from '@/test/filters-app.tsx'

const MUTED = { id: 4, account_id: WORK, ids: 1, gmail: false, run_filters: true, created_at: '2026-11-19T09:00:00Z' }

function state(overrides: Partial<FakeState> = {}): FakeState {
  return {
    accounts: [
      { id: WORK, run_filters: true, name: 'Work mail', colour: 'teal' },
      { id: HOME, run_filters: false },
    ],
    folders: [['INBOX'], ['Archive'], ['Lists', 'Nerds']],
    ruleSets: [
      {
        id: 1,
        account_id: WORK,
        folder: ['INBOX'],
        name: 'Newsletters',
        trigger: 'scheduled',
        interval_minutes: 15,
        marker: 'none',
        enabled: true,
      },
    ],
    filters: [],
    runs: [],
    messages: [],
    ...overrides,
  }
}

/** Each request sent, as `"<METHOD> <path>"`. */
function sentRequests(fetchMock: ReturnType<typeof stubFiltersApi>): string[] {
  return fetchMock.mock.calls.map((call) => `${(call[1] as RequestInit | undefined)?.method ?? 'GET'} ${String(call[0])}`)
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('FiltersScreen', () => {
  it('passes axe', async () => {
    stubFiltersApi(state())
    renderFiltersApp('/filters')
    await screen.findByRole('list', { name: 'Rule sets of Work mail' })
    await expectNoAxeViolations()
  })

  it('lists the rule sets of each account and whether Run filters is on', async () => {
    stubFiltersApi(state())
    renderFiltersApp('/filters')

    const work = await screen.findByRole('list', { name: 'Rule sets of Work mail' })
    expect(within(work).getByRole('link', { name: 'Newsletters' })).toHaveAttribute('href', '/filters/1')
    expect(within(work).getByText('INBOX · every 15 minutes')).toBeInTheDocument()
    const home = screen.getByRole('region', { name: 'home' })
    expect(within(home).getByText(/Run filters is off/)).toBeInTheDocument()
    expect(await within(home).findByText('No rule sets yet.')).toBeInTheDocument()
  })

  it('points to Accounts when there is no account', async () => {
    stubFiltersApi(state({ accounts: [] }))
    renderFiltersApp('/filters')

    await userEvent.click(await screen.findByRole('link', { name: 'Go to Accounts' }))
    expect(await screen.findByText('Accounts page')).toBeInTheDocument()
  })

  it('adds a rule set with a folder from the account and opens it', async () => {
    const fake = state()
    const fetchMock = stubFiltersApi(fake)
    renderFiltersApp('/filters')

    await userEvent.click(await screen.findByRole('button', { name: 'Add a rule set to home' }))
    const form = screen.getByRole('form', { name: 'Add a rule set' })
    await userEvent.type(within(form).getByLabelText('Name'), 'Old mail')
    const folder = within(form).getByLabelText('Folder')
    await vi.waitFor(() => {
      expect(folder).toBeEnabled()
    })
    expect(folder).toBeRequired()
    await userEvent.selectOptions(folder, 'Lists / Nerds')
    expect(within(form).queryByLabelText('Every (minutes)')).not.toBeInTheDocument()
    await userEvent.click(within(form).getByLabelText('On a schedule'))
    const interval = within(form).getByLabelText('Every (minutes)')
    await userEvent.clear(interval)
    await userEvent.type(interval, '30')
    await userEvent.click(within(form).getByLabelText('Only marked messages'))
    // Not known to keep keywords: the star.
    expect(within(form).getByLabelText('Star')).toBeChecked()
    await userEvent.click(within(form).getByRole('button', { name: 'Add rule set' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Old mail' })).toBeInTheDocument()
    expect(sentBody(fetchMock, 'POST', '/api/v1/accounts/2/rule-sets')).toEqual({
      name: 'Old mail',
      folder: ['Lists', 'Nerds'],
      trigger: 'scheduled',
      interval_minutes: 30,
      marker: 'star',
      enabled: false,
    })
  })

  it('marks a new rule set To sort where the server keeps keywords', async () => {
    const fake = state()
    fake.accounts[1] = { id: HOME, run_filters: false, keywords: true }
    const fetchMock = stubFiltersApi(fake)
    renderFiltersApp('/filters')

    await userEvent.click(await screen.findByRole('button', { name: 'Add a rule set to home' }))
    const form = screen.getByRole('form', { name: 'Add a rule set' })
    await userEvent.type(within(form).getByLabelText('Name'), 'Sorting')
    const folder = within(form).getByLabelText('Folder')
    await vi.waitFor(() => {
      expect(folder).toBeEnabled()
    })
    await userEvent.selectOptions(folder, 'INBOX')
    expect(within(form).queryByLabelText('To sort')).not.toBeInTheDocument()
    await userEvent.click(within(form).getByLabelText('Only marked messages'))
    expect(within(form).getByLabelText('To sort')).toBeChecked()
    await userEvent.click(within(form).getByRole('button', { name: 'Add rule set' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Sorting' })).toBeInTheDocument()
    expect(sentBody(fetchMock, 'POST', '/api/v1/accounts/2/rule-sets')).toMatchObject({ marker: 'to_sort' })
  })

  it('greys To sort out, saying why, where the server keeps no keywords', async () => {
    const fake = state()
    fake.accounts[1] = { id: HOME, run_filters: false, keywords: false }
    stubFiltersApi(fake)
    renderFiltersApp('/filters')

    await userEvent.click(await screen.findByRole('button', { name: 'Add a rule set to home' }))
    const form = screen.getByRole('form', { name: 'Add a rule set' })
    await userEvent.click(within(form).getByLabelText('Only marked messages'))
    expect(within(form).getByLabelText('Star')).toBeChecked()
    const toSort = within(form).getByLabelText('To sort')
    expect(toSort).toBeDisabled()
    expect(toSort).toHaveAccessibleDescription("This account's mail server cannot keep the To sort mark.")
  })

  it('turns a rule set off', async () => {
    const fetchMock = stubFiltersApi(state())
    renderFiltersApp('/filters')

    const toggle = await screen.findByRole('switch', { name: 'Enable Newsletters' })
    expect(toggle).toBeChecked()
    await userEvent.click(toggle)
    await vi.waitFor(() => {
      expect(toggle).not.toBeChecked()
    })
    expect(sentBody(fetchMock, 'PATCH', '/api/v1/rule-sets/1')).toEqual({ enabled: false })
  })

  it('removes a rule set only once confirmed', async () => {
    stubFiltersApi(state())
    renderFiltersApp('/filters')

    await userEvent.click(await screen.findByRole('button', { name: 'Remove Newsletters' }))
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('link', { name: 'Newsletters' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Remove Newsletters' }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove for good' }))
    const work = screen.getByRole('region', { name: 'Work mail' })
    expect(await within(work).findByText('No rule sets yet.')).toBeInTheDocument()
    await vi.waitFor(() => {
      expect(within(work).getByRole('button', { name: 'Add a rule set to Work mail' })).toHaveFocus()
    })
  })

  it('lists the muted conversations of each account, says muting needs Run filters, and unmutes one', async () => {
    const muted = [
      { id: 4, account_id: HOME, ids: 3, gmail: true, run_filters: false, created_at: '2026-11-18T09:00:00Z' },
      { id: 5, account_id: WORK, ids: 1, gmail: false, run_filters: true, created_at: '2026-11-19T09:00:00Z' },
    ]
    const fake = state({ muted })
    const fetchMock = stubFiltersApi(fake)
    renderFiltersApp('/filters', { features: ['mail', 'filters'] })

    const home = await screen.findByRole('list', { name: 'Muted conversations of home' })
    expect(within(home).getByText('Known by 3 messages · and by its Gmail thread')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'home' })).toHaveTextContent(
      'Run filters is off for this account: muting has no effect until it is on.',
    )
    const work = screen.getByRole('list', { name: 'Muted conversations of Work mail' })
    expect(within(work).getByText('Known by 1 message')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Work mail' })).not.toHaveTextContent('muting has no effect')

    await userEvent.click(within(home).getByRole('button', { name: /^Unmute the conversation muted / }))
    await vi.waitFor(() => {
      expect(screen.queryByRole('list', { name: 'Muted conversations of home' })).not.toBeInTheDocument()
    })
    expect(sentRequests(fetchMock)).toContain('DELETE /api/v1/mail/muted/4')
    expect(fake.muted).toEqual([muted[1]])
    // The list goes with its last conversation: focus stays in the account's section.
    await vi.waitFor(() => {
      expect(screen.getByRole('button', { name: 'Add a rule set to home' })).toHaveFocus()
    })
  })

  it('says when the muted conversations cannot be listed, and lists them on retry', async () => {
    const fake = state({
      muted: [MUTED],
      refuse: { 'GET /api/v1/mail/muted': { status: 500, body: { detail: 'Server error' } } },
    })
    stubFiltersApi(fake)
    renderFiltersApp('/filters', { features: ['mail', 'filters'] })

    expect(await screen.findByRole('alert')).toHaveTextContent('Server error')
    fake.refuse = {}
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('list', { name: 'Muted conversations of Work mail' })).toBeInTheDocument()
  })

  it('keeps a conversation the server would not unmute, saying so', async () => {
    const fake = state({
      muted: [MUTED],
      refuse: { 'DELETE /api/v1/mail/muted/4': { status: 500, body: { detail: 'Server error' } } },
    })
    stubFiltersApi(fake)
    renderFiltersApp('/filters', { features: ['mail', 'filters'] })

    const work = await screen.findByRole('list', { name: 'Muted conversations of Work mail' })
    await userEvent.click(within(work).getByRole('button', { name: /^Unmute the conversation muted / }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Server error')
    expect(within(work).getAllByRole('listitem')).toHaveLength(1)
  })

  it('takes a conversation unmuted elsewhere off the list, with no error', async () => {
    const fake = state({ muted: [MUTED] })
    stubFiltersApi(fake)
    renderFiltersApp('/filters', { features: ['mail', 'filters'] })

    const work = await screen.findByRole('list', { name: 'Muted conversations of Work mail' })
    fake.muted = []
    await userEvent.click(within(work).getByRole('button', { name: /^Unmute the conversation muted / }))
    await vi.waitFor(() => {
      expect(screen.queryByRole('list', { name: 'Muted conversations of Work mail' })).not.toBeInTheDocument()
    })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('lists no muted conversations where the instance runs no mail client', async () => {
    const fetchMock = stubFiltersApi(state({ muted: [MUTED] }))
    renderFiltersApp('/filters', { features: ['filters'] })

    await screen.findByRole('list', { name: 'Rule sets of Work mail' })
    expect(screen.queryByRole('heading', { name: 'Muted conversations' })).not.toBeInTheDocument()
    expect(sentRequests(fetchMock)).not.toContain('GET /api/v1/mail/muted')
  })
})

import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { type FakeState, HOME, sentBody, stubFiltersApi, WORK } from '@/test/fake-filters-api.ts'
import { renderFiltersApp } from '@/test/filters-app.tsx'

const ALL = 'all'

function state(overrides: Partial<FakeState> = {}): FakeState {
  return {
    accounts: [
      { id: WORK, run_filters: true, name: 'Work mail', colour: 'teal' },
      { id: HOME, run_filters: false },
    ],
    folders: [['INBOX'], ['Archive']],
    ruleSets: [
      {
        id: 1,
        account_id: WORK,
        folder: ['INBOX'],
        name: 'Work sorting',
        trigger: 'new_mail',
        interval_minutes: null,
        marker: 'none',
        enabled: true,
      },
      {
        id: 2,
        account_id: ALL,
        folder: ['INBOX'],
        name: 'Everywhere',
        trigger: 'new_mail',
        interval_minutes: null,
        marker: 'none',
        enabled: true,
      },
    ],
    filters: [
      {
        id: 20,
        rule_set_id: 2,
        position: 0,
        name: 'Newsletters',
        enabled: true,
        spec: {
          criterion: { type: 'from_contains', args: { addr: 'news@' } },
          action: { type: 'move_to', args: { folder: ['Archive'] } },
        },
      },
    ],
    runs: [],
    messages: [{ uid: 7, from: 'news@example.org', subject: 'Weekly digest', date: null }],
    ...overrides,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('rule sets for all accounts on the Filters screen', () => {
  it('lists them apart from each account’s own', async () => {
    stubFiltersApi(state())
    renderFiltersApp('/filters')

    const all = await screen.findByRole('list', { name: 'Rule sets for all accounts' })
    expect(within(all).getByRole('link', { name: 'Everywhere' })).toHaveAttribute('href', '/filters/2')
    expect(within(all).getByText('INBOX · on new mail')).toBeInTheDocument()
    const work = await screen.findByRole('list', { name: 'Rule sets of Work mail' })
    expect(within(work).queryByText('Everywhere')).not.toBeInTheDocument()
    const region = screen.getByRole('region', { name: 'All accounts' })
    expect(within(region).getByText(/every account whose Run filters is on/)).toBeInTheDocument()
  })

  it('says nothing runs while Run filters is off for every account', async () => {
    stubFiltersApi(state({ accounts: [{ id: WORK, run_filters: false }] }))
    renderFiltersApp('/filters')

    const region = await screen.findByRole('region', { name: 'All accounts' })
    expect(within(region).getByText(/it is off for every account/)).toBeInTheDocument()
    expect(within(region).getByRole('link', { name: 'Go to Accounts' })).toHaveAttribute('href', '/accounts')
  })

  it('adds one on INBOX, with the star only, and opens it', async () => {
    const fetchMock = stubFiltersApi(state())
    renderFiltersApp('/filters')

    await userEvent.click(await screen.findByRole('button', { name: 'Add a rule set for all accounts' }))
    const form = screen.getByRole('form', { name: 'Add a rule set' })
    await userEvent.type(within(form).getByLabelText('Name'), 'Spam everywhere')
    const folder = within(form).getByLabelText('Folder')
    expect(folder).toHaveValue('INBOX')
    expect(folder).toHaveAttribute('readonly')
    expect(folder).toHaveAccessibleDescription("A rule set for all accounts sorts each account's inbox.")
    await userEvent.click(within(form).getByLabelText('Only marked messages'))
    expect(within(form).getByLabelText('To sort')).toBeDisabled()
    expect(within(form).getByLabelText('Star')).toBeChecked()
    await userEvent.click(within(form).getByRole('button', { name: 'Add rule set' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Spam everywhere' })).toBeInTheDocument()
    expect(sentBody(fetchMock, 'POST', '/api/v1/accounts/all/rule-sets')).toEqual({
      name: 'Spam everywhere',
      folder: ['INBOX'],
      trigger: 'new_mail',
      interval_minutes: null,
      marker: 'star',
      enabled: false,
    })
    expect(fetchMock.mock.calls.some(([path]) => path === '/api/v1/accounts/all/folders')).toBe(false)
  })
})

describe('the editor of a rule set for all accounts', () => {
  it('names no one account', async () => {
    stubFiltersApi(state())
    renderFiltersApp('/filters/2')

    expect(await screen.findByRole('heading', { level: 1, name: 'Everywhere' })).toBeInTheDocument()
    expect(screen.getByText('All accounts · INBOX · on new mail · enabled')).toBeInTheDocument()
  })

  it('takes a move’s folder as a path, warning that an account without it fails', async () => {
    const fetchMock = stubFiltersApi(state({ filters: [] }))
    renderFiltersApp('/filters/2')

    await userEvent.click(await screen.findByRole('button', { name: 'Add a filter' }))
    const form = screen.getByRole('form', { name: 'Add a filter' })
    await userEvent.type(within(form).getByLabelText('Name'), 'Receipts')
    await userEvent.type(within(form).getByLabelText('Address'), 'shop@example.com')
    const action = within(form).getByRole('group', { name: 'Action 1' })
    await userEvent.selectOptions(within(action).getByLabelText('Type of Action 1'), 'Move to')
    const folder = within(action).getByLabelText('Folder')
    expect(folder).toBeRequired()
    expect(folder).toHaveAccessibleDescription(/on one without it the move fails/)
    await userEvent.type(folder, 'Receipts/2026')
    await userEvent.click(within(form).getByRole('button', { name: 'Add filter' }))

    await vi.waitFor(() => {
      expect(sentBody(fetchMock, 'POST', '/api/v1/rule-sets/2/filters')).toMatchObject({
        spec: { action: { type: 'move_to', args: { folder: ['Receipts', '2026'] } } },
      })
    })
  })

  it('moves to each account’s own trash unless a path is chosen', async () => {
    const fetchMock = stubFiltersApi(state({ filters: [] }))
    renderFiltersApp('/filters/2')

    await userEvent.click(await screen.findByRole('button', { name: 'Add a filter' }))
    const form = screen.getByRole('form', { name: 'Add a filter' })
    await userEvent.type(within(form).getByLabelText('Name'), 'Old')
    await userEvent.type(within(form).getByLabelText('Address'), 'old@example.com')
    const action = within(form).getByRole('group', { name: 'Action 1' })
    await userEvent.selectOptions(within(action).getByLabelText('Type of Action 1'), 'Move to the trash')
    const way = within(action).getByLabelText('Folder')
    expect(way).toHaveValue('role')
    expect(within(action).queryByLabelText('Folder path')).not.toBeInTheDocument()

    await userEvent.selectOptions(way, 'A folder by its path')
    const path = within(action).getByLabelText('Folder path')
    expect(path).toHaveAccessibleDescription(/no folder is created/)
    await userEvent.type(path, 'Bin')
    await userEvent.selectOptions(way, "Each account's own trash")
    await userEvent.click(within(form).getByRole('button', { name: 'Add filter' }))

    await vi.waitFor(() => {
      expect(sentBody(fetchMock, 'POST', '/api/v1/rule-sets/2/filters')).toMatchObject({
        spec: { action: { type: 'trash' } },
      })
    })
  })

  it('greys out the To sort actions, kept per account', async () => {
    stubFiltersApi(state({ accounts: [{ id: WORK, run_filters: true, keywords: true }] }))
    renderFiltersApp('/filters/2')

    await userEvent.click(await screen.findByRole('button', { name: 'Add a filter' }))
    const form = screen.getByRole('form', { name: 'Add a filter' })
    const type = within(within(form).getByRole('group', { name: 'Action 1' })).getByLabelText('Type of Action 1')
    expect(within(type).getByRole('option', { name: 'Mark to sort (kept per account, not for all accounts)' })).toBeDisabled()
    expect(
      within(type).getByRole('option', {
        name: 'Move to another account (a rule set for all accounts also runs on the other account: put the move in a rule set of each account)',
      }),
    ).toBeDisabled()
    expect(within(type).getByRole('option', { name: 'Star' })).toBeEnabled()
  })

  it('dry-runs on the account the user picks', async () => {
    const fetchMock = stubFiltersApi(state())
    renderFiltersApp('/filters/2')

    const tryOn = await screen.findByLabelText('Try on')
    expect(tryOn).toHaveValue(String(WORK))
    await userEvent.selectOptions(tryOn, 'home')
    await userEvent.click(screen.getByRole('button', { name: 'Dry run' }))

    const result = await screen.findByRole('region', { name: 'Dry run on home' })
    expect(within(result).getByText('Weekly digest')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([path]) => path === `/api/v1/rule-sets/2/dry-run?account_id=${String(HOME)}`)).toBe(
      true,
    )
    const history = await screen.findByRole('list', { name: 'Run history' })
    expect(within(history).getByText('home')).toBeInTheDocument()
  })

  it('runs now on each account whose Run filters is on, naming each run’s account', async () => {
    const fake = state({
      accounts: [
        { id: WORK, run_filters: true, name: 'Work mail' },
        { id: HOME, run_filters: true },
      ],
    })
    stubFiltersApi(fake)
    renderFiltersApp('/filters/2')

    const run = await screen.findByRole('button', { name: 'Run now' })
    expect(run).toHaveAccessibleDescription(/inboxes of the 2 accounts whose Run filters is on/)
    await userEvent.click(run)

    expect(await screen.findByText('Runs queued on 2 accounts: follow them in the run history.')).toBeInTheDocument()
    const history = await screen.findByRole('list', { name: 'Run history' })
    await vi.waitFor(() => {
      expect(within(history).getAllByRole('listitem')).toHaveLength(2)
    })
    expect(within(history).getByText('Work mail')).toBeInTheDocument()
    expect(within(history).getByText('home')).toBeInTheDocument()
  })

  it('cannot run while Run filters is off for every account', async () => {
    stubFiltersApi(state({ accounts: [{ id: WORK, run_filters: false }] }))
    renderFiltersApp('/filters/2')

    const run = await screen.findByRole('button', { name: 'Run now' })
    expect(run).toBeDisabled()
    expect(run).toHaveAccessibleDescription(
      'Run filters is off for every account: turn it on for one in Accounts to run this rule set.',
    )
  })
})

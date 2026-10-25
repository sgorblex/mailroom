import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { sentBody, stubFiltersApi, type FakeState } from '@/test/fake-filters-api.ts'
import { renderFiltersApp } from '@/test/filters-app.tsx'

function state(overrides: Partial<FakeState> = {}): FakeState {
  return {
    accounts: [
      { id: 'work', run_filters: true },
      { id: 'home', run_filters: false },
    ],
    folders: [['INBOX'], ['Archive'], ['Lists', 'Nerds']],
    ruleSets: [
      {
        id: 1,
        account_id: 'work',
        folder: ['INBOX'],
        name: 'Newsletters',
        trigger: 'scheduled',
        interval_minutes: 15,
        starred_only: false,
        enabled: true,
      },
    ],
    filters: [],
    runs: [],
    messages: [],
    ...overrides,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('FiltersScreen', () => {
  it('lists the rule sets of each account and whether Run filters is on', async () => {
    stubFiltersApi(state())
    renderFiltersApp('/filters')

    const work = await screen.findByRole('list', { name: 'Rule sets of work' })
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
    await userEvent.click(within(form).getByLabelText('Starred only'))
    await userEvent.click(within(form).getByRole('button', { name: 'Add rule set' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Old mail' })).toBeInTheDocument()
    expect(sentBody(fetchMock, 'POST', '/api/v1/accounts/home/rule-sets')).toEqual({
      name: 'Old mail',
      folder: ['Lists', 'Nerds'],
      trigger: 'scheduled',
      interval_minutes: 30,
      starred_only: true,
      enabled: false,
    })
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
    expect(screen.getByRole('button', { name: 'Remove for good' })).toHaveFocus()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('link', { name: 'Newsletters' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Remove Newsletters' }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove for good' }))
    const work = screen.getByRole('region', { name: 'work' })
    expect(await within(work).findByText('No rule sets yet.')).toBeInTheDocument()
    await vi.waitFor(() => {
      expect(within(work).getByRole('button', { name: 'Add a rule set to work' })).toHaveFocus()
    })
  })
})

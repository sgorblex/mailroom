import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { type FakeState, HOME, sentBody, stubFiltersApi, WORK } from '@/test/fake-filters-api.ts'
import { renderFiltersApp } from '@/test/filters-app.tsx'

const REF = 'ref-1-INBOX-7'
const LIST = { type: 'list_is', args: { list_id: 'weekly.news.example' } }
const SENDER = { type: 'from_is', args: { addr: 'news@news.example' } }
const DOMAIN = { type: 'from_domain', args: { domain: 'news.example' } }
const NO_OP = { type: 'no_op' }

function state(overrides: Partial<FakeState> = {}): FakeState {
  return {
    accounts: [
      { id: WORK, run_filters: true },
      { id: HOME, run_filters: false },
    ],
    folders: [['INBOX'], ['Archive']],
    ruleSets: [
      {
        id: 1,
        account_id: WORK,
        folder: ['INBOX'],
        name: 'Inbox rules',
        trigger: 'new_mail',
        interval_minutes: null,
        marker: 'none',
        enabled: true,
      },
      {
        id: 2,
        account_id: WORK,
        folder: ['Archive'],
        name: 'Old mail',
        trigger: 'manual',
        interval_minutes: null,
        marker: 'none',
        enabled: false,
      },
    ],
    filters: [],
    runs: [],
    messages: [],
    messageDrafts: {
      [REF]: { spec: { criterion: LIST, action: NO_OP }, criteria: [LIST, SENDER, DOMAIN], name: 'Weekly news' },
    },
    ...overrides,
  }
}

const fromMessage = (account = WORK): string => `/filters/new?account=${String(account)}&message=${REF}`
const fromSearch = (query: Record<string, unknown>): string =>
  `/filters/new?q=${encodeURIComponent(JSON.stringify(query))}`

/** The editor, once its rule sets are listed. */
async function editor(): Promise<HTMLElement> {
  const form = await screen.findByRole('form', { name: 'Add a filter' })
  await vi.waitFor(() => {
    expect(within(form).queryByText('Loading the rule sets…')).not.toBeInTheDocument()
  })
  return form
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('a filter drafted from a message', () => {
  it('adds the narrowest criterion to an inbox rule set of the account, and opens it', async () => {
    const fake = state()
    const fetchMock = stubFiltersApi(fake)
    renderFiltersApp(fromMessage())

    expect(await screen.findByRole('heading', { level: 1, name: 'Filter messages like this' })).toBeInTheDocument()
    const form = await editor()
    expect(within(form).getByLabelText('Name')).toHaveValue('Weekly news')
    const ruleSets = within(form).getByRole('group', { name: 'Add it to a rule set of the inbox' })
    // The archive's rule set is not offered.
    expect(within(ruleSets).getAllByRole('radio')).toHaveLength(2)
    expect(within(ruleSets).getByRole('radio', { name: /Inbox rules/ })).toBeChecked()
    expect(within(ruleSets).getByText(/the filter acts on new mail as soon as you add it/)).toBeInTheDocument()
    await userEvent.click(within(form).getByRole('button', { name: 'Add filter' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Inbox rules' })).toBeInTheDocument()
    expect(sentBody(fetchMock, 'POST', '/api/v1/rule-sets/1/filters')).toEqual({
      name: 'Weekly news',
      enabled: true,
      spec: { criterion: LIST, action: NO_OP },
    })
    expect(fake.ruleSets).toHaveLength(2)
  })

  it('matches on the criterion chosen', async () => {
    const fetchMock = stubFiltersApi(state())
    renderFiltersApp(fromMessage())

    const choice = await screen.findByRole('group', { name: 'Match on' })
    expect(within(choice).getAllByRole('radio').map((radio) => radio.closest('label')?.textContent)).toEqual([
      'the mailing list is “weekly.news.example”',
      'the sender is “news@news.example”',
      "the sender's domain is “news.example”",
    ])
    await userEvent.click(within(choice).getByRole('radio', { name: /the sender is/ }))
    await userEvent.click(within(await editor()).getByRole('button', { name: 'Add filter' }))

    await screen.findByRole('heading', { level: 1, name: 'Inbox rules' })
    expect(sentBody(fetchMock, 'POST', '/api/v1/rule-sets/1/filters')).toMatchObject({
      spec: { criterion: SENDER, action: NO_OP },
    })
  })

  it('keeps the name and the actions when another criterion is chosen', async () => {
    const fetchMock = stubFiltersApi(state())
    renderFiltersApp(fromMessage())

    const form = await editor()
    await userEvent.clear(within(form).getByLabelText('Name'))
    await userEvent.type(within(form).getByLabelText('Name'), 'Mine')
    const action = within(within(form).getByRole('group', { name: 'Action 1' })).getByLabelText('Type of Action 1')
    await userEvent.selectOptions(action, 'leaf:mark_as_read')
    await userEvent.click(within(screen.getByRole('group', { name: 'Match on' })).getByRole('radio', { name: /the sender is/ }))

    expect(within(form).getByLabelText('Name')).toHaveValue('Mine')
    expect(action).toHaveValue('leaf:mark_as_read')
    await userEvent.click(within(form).getByRole('button', { name: 'Add filter' }))
    await screen.findByRole('heading', { level: 1, name: 'Inbox rules' })
    expect(sentBody(fetchMock, 'POST', '/api/v1/rule-sets/1/filters')).toMatchObject({
      name: 'Mine',
      spec: { criterion: SENDER, action: { type: 'mark_as_read' } },
    })
  })

  it("offers the user's other accounts, not this one, to move to", async () => {
    stubFiltersApi(state())
    renderFiltersApp(fromMessage())

    const action = within(await editor()).getByRole('group', { name: 'Action 1' })
    await userEvent.selectOptions(within(action).getByLabelText('Type of Action 1'), 'Move to another account')
    const account = within(action).getByLabelText('Account')
    expect(within(account).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Choose an account',
      'home',
    ])
  })

  it('makes a new inbox rule set, disabled, where the account has none', async () => {
    const fake = state()
    const fetchMock = stubFiltersApi(fake)
    renderFiltersApp(fromMessage(HOME))

    const form = await editor()
    const ruleSets = within(form).getByRole('group', { name: 'Add it to a rule set of the inbox' })
    expect(within(ruleSets).getByRole('radio', { name: 'A new rule set' })).toBeChecked()
    expect(within(ruleSets).getByText(/A new rule set starts disabled/)).toBeInTheDocument()
    expect(within(ruleSets).getByText(/Run filters is off/)).toBeInTheDocument()
    const name = within(ruleSets).getByLabelText('Name of the new rule set')
    await userEvent.clear(name)
    await userEvent.type(name, 'Lists')
    await userEvent.click(within(form).getByRole('button', { name: 'Add filter' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Lists' })).toBeInTheDocument()
    expect(sentBody(fetchMock, 'POST', `/api/v1/accounts/${String(HOME)}/rule-sets`)).toEqual({
      name: 'Lists',
      folder: ['INBOX'],
      trigger: 'new_mail',
      interval_minutes: null,
      marker: 'none',
      enabled: false,
    })
    const created = fake.ruleSets.find((ruleSet) => ruleSet.name === 'Lists')
    expect(fake.filters.map((filter) => filter.rule_set_id)).toEqual([created?.id])
  })

  it('adds to the rule set it made when adding the filter failed, never making another', async () => {
    const fake = state({ ruleSets: [] })
    stubFiltersApi(fake)
    renderFiltersApp(fromMessage())

    const form = await editor()
    fake.refuse = { 'POST /api/v1/rule-sets/1001/filters': { status: 502, body: { detail: 'Bad gateway' } } }
    await userEvent.click(within(form).getByRole('button', { name: 'Add filter' }))
    expect(await within(form).findByRole('alert')).toBeInTheDocument()
    expect(within(form).getByRole('radio', { name: /Inbox/ })).toBeChecked()

    fake.refuse = {}
    await userEvent.click(within(form).getByRole('button', { name: 'Add filter' }))
    await screen.findByRole('heading', { level: 1, name: 'Inbox' })
    expect(fake.ruleSets).toHaveLength(1)
  })

  it("words the server's refusal of a public provider's domain", async () => {
    stubFiltersApi(
      state({
        refuse: {
          'POST /api/v1/rule-sets/1/filters': {
            status: 400,
            body: { detail: { code: 'public_provider_domain', message: 'public' } },
          },
        },
      }),
    )
    renderFiltersApp(fromMessage())

    const form = await editor()
    await userEvent.click(within(form).getByRole('button', { name: 'Add filter' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent(/public mail provider/)
  })

  it('says when the message has nothing to filter on', async () => {
    stubFiltersApi(
      state({
        refuse: {
          [`GET /api/v1/mail/messages/${REF}/filter-draft`]: {
            status: 400,
            body: { detail: { code: 'nothing_to_filter', message: 'Nothing' } },
          },
        },
      }),
    )
    renderFiltersApp(fromMessage())

    expect(await screen.findByRole('alert')).toHaveTextContent(/no mailing list and no sender address/)
  })

  it('goes back to the message on cancel', async () => {
    stubFiltersApi(state())
    const history = renderFiltersApp(fromMessage())

    await userEvent.click(within(await editor()).getByRole('button', { name: 'Cancel' }))
    expect(await screen.findByText('Mail page')).toBeInTheDocument()
    const search = new URLSearchParams(history.location.search)
    expect(search.get('message')).toBe(REF)
    expect(search.get('account')).toBe(String(WORK))
  })
})

describe('a filter drafted from a search', () => {
  const spec = {
    criterion: { and: [{ type: 'from_contains', args: { addr: 'news' } }, { type: 'is_unread' }] },
    action: NO_OP,
  }

  it('lists the fields left out and sends the search as the search route takes it', async () => {
    const fetchMock = stubFiltersApi(state({ searchDraft: { spec, dropped: ['text', 'since'] } }))
    renderFiltersApp(fromSearch({ account: String(WORK), folder: 'INBOX', from: 'news', text: 'weekly', since: '2026-01-01', unread: true }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Create a filter from a search' })).toBeInTheDocument()
    expect(await screen.findByText('Left out, as no filter condition matches them as the search does: Words, Since.')).toBeInTheDocument()
    expect(sentBody(fetchMock, 'POST', '/api/v1/filters/draft-from-search')).toEqual({
      account: WORK,
      folder: 'INBOX',
      from: 'news',
      text: 'weekly',
      since: '2026-01-01',
      unread: true,
    })
    const form = await editor()
    expect(within(form).getByLabelText('Name')).toHaveValue('news')
    await userEvent.click(within(form).getByRole('button', { name: 'Add filter' }))
    await screen.findByRole('heading', { level: 1, name: 'Inbox rules' })
    expect(sentBody(fetchMock, 'POST', '/api/v1/rule-sets/1/filters')).toMatchObject({ spec })
  })

  it('says a sender on a public mail provider was left out', async () => {
    const unread = { criterion: { type: 'is_unread' }, action: NO_OP }
    stubFiltersApi(state({ searchDraft: { spec: unread, dropped: ['from'] } }))
    renderFiltersApp(fromSearch({ account: String(WORK), folder: 'INBOX', from: '@gmail.com', unread: true }))

    expect(
      await screen.findByText(
        "Left out, as no filter condition matches them as the search does: From (a public mail provider's whole domain).",
      ),
    ).toBeInTheDocument()
  })

  it('asks for the account of a search over all of them', async () => {
    const fetchMock = stubFiltersApi(state({ searchDraft: { spec, dropped: [] } }))
    renderFiltersApp(fromSearch({ account: '*', folder: '*', from: 'news' }))

    const account = await screen.findByLabelText('Account')
    await userEvent.clear(within(await editor()).getByLabelText('Name'))
    await userEvent.type(within(await editor()).getByLabelText('Name'), 'Mine')
    await userEvent.selectOptions(account, 'home')
    const form = await editor()
    expect(within(form).getByLabelText('Name')).toHaveValue('Mine')
    expect(within(form).getByRole('radio', { name: 'A new rule set' })).toBeChecked()
    await userEvent.click(within(form).getByRole('button', { name: 'Add filter' }))
    await screen.findByRole('heading', { level: 1, name: 'Inbox' })
    expect(sentBody(fetchMock, 'POST', `/api/v1/accounts/${String(HOME)}/rule-sets`)).toMatchObject({ enabled: false })
  })

  it('names the fields it could not use when no criterion is left', async () => {
    stubFiltersApi(
      state({
        refuse: {
          'POST /api/v1/filters/draft-from-search': {
            status: 400,
            body: { detail: { code: 'nothing_to_filter', message: 'Nothing', dropped: ['text', 'has_attachment'] } },
          },
        },
      }),
    )
    renderFiltersApp(fromSearch({ text: 'weekly', has_attachment: true }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      "None of this search's fields can be a filter condition (Words, With attachments match differently in a filter).",
    )
  })
})

it('says there is nothing to draft from without a message or a search', async () => {
  stubFiltersApi(state())
  renderFiltersApp('/filters/new')

  expect(await screen.findByRole('alert')).toHaveTextContent('There is no message or search here to draft a filter from.')
})

import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Feature, UserJson } from '@/api/auth.ts'
import type { RuleSet } from '@/api/filters.ts'
import { bodiesTo, type FakeMailState, messageItem, messageView, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const ALICE: UserJson = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }
const withFilters: { user: UserJson; features: Feature[] } = { user: ALICE, features: ['mail', 'filters'] }

const inbox = Array.from({ length: 3 }, (_, index) => messageItem(WORK, 'INBOX', 10 - index))
const [, spam] = inbox
const SENDER = 'sender9@example.org'
const SPEC = { criterion: { type: 'from_is', args: { addr: SENDER } }, action: { type: 'junk' } }

const inboxRules: RuleSet = {
  id: 7,
  account_id: WORK,
  folder: ['INBOX'],
  name: 'Inbox rules',
  trigger: 'new_mail',
  interval_minutes: null,
  marker: 'none',
  enabled: true,
}

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 3 },
        { path: 'Junk', role: 'junk', unread: 0, total: 0 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
      ],
    },
    messages: { '1/INBOX': inbox },
    views: {},
    ...overrides,
  }
}

const messagesList = (): Promise<HTMLElement> => screen.findByRole('list', { name: 'Messages' })
const actions = (): Promise<HTMLElement> => screen.findByRole('group', { name: 'Message actions' })
const selection = (): Promise<HTMLElement> => screen.findByRole('group', { name: 'Selected messages' })
const offer = (): Promise<HTMLElement> => screen.findByRole('region', { name: 'Filter for spam' })

function toast(): HTMLElement {
  const region = document.querySelector<HTMLElement>('#toast-status')
  if (region === null) {
    throw new Error('no toast region')
  }
  return region
}

async function select(list: HTMLElement, subject: string): Promise<void> {
  await userEvent.click(within(list).getByRole('checkbox', { name: `Select ${subject}` }))
}

/** The paths of the requests sent to the filter routes. */
function filterRequests(fetchMock: ReturnType<typeof stubMailApi>): string[] {
  return fetchMock.mock.calls
    .map(([path, init]) => `${(init as RequestInit | undefined)?.method ?? 'GET'} ${new URL(path as string, 'http://mailroom.test').pathname}`)
    .filter((request) => /rule-sets|filter-settings|\/filters/.test(request))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('report spam', () => {
  it('moves the selection to Junk with its key, and Undo moves it back', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    const list = await messagesList()
    await select(list, 'Message 9')
    expect(within(await selection()).getByRole('button', { name: 'Report spam' })).toHaveAttribute('aria-keyshortcuts', '!')
    await userEvent.keyboard('!')
    await waitFor(() => {
      expect(toast()).toHaveTextContent('Reported 1 message as spam.')
    })
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/junk')).toEqual([{ refs: [spam?.ref], source_folder: 'INBOX' }])
    expect(within(list).queryByRole('link', { name: /Message 9Preview/ })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => {
      expect(toast()).toHaveTextContent('Undone.')
    })
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/move')).toEqual([{ refs: ['ref-1-Junk-1'], folder: 'INBOX' }])
  })

  it('is not offered where the account has no Junk folder', async () => {
    stubMailApi(state({ folders: { [WORK]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 3 }] } }))
    renderMailApp('/mail')

    await select(await messagesList(), 'Message 9')
    expect(within(await selection()).getByRole('button', { name: 'Move to Trash' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Report spam' })).not.toBeInTheDocument()
  })
})

describe('the filter offered after a report of spam', () => {
  it('adds nothing unasked, then makes the filter and asks before turning anything on', async () => {
    const fake = state()
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail', withFilters)

    await select(await messagesList(), 'Message 9')
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Report spam' }))
    const region = await offer()
    expect(region).toHaveTextContent(`Send future mail from ${SENDER} to Junk?`)
    expect(region).toHaveTextContent(/only while that rule set and Run filters for this account are on/)
    expect(filterRequests(fetchMock)).toEqual([])

    await userEvent.click(within(region).getByRole('button', { name: 'Add the filter' }))
    // No inbox rule set: a new one, disabled as any new rule set.
    expect(await within(region).findByText(/Filter added to the rule set “Inbox”/)).toHaveFocus()
    // Listed, then made.
    expect(bodiesTo(fetchMock, `/api/v1/accounts/${String(WORK)}/rule-sets`)).toEqual([
      null,
      { name: 'Inbox', folder: ['INBOX'], trigger: 'new_mail', interval_minutes: null, marker: 'none', enabled: false },
    ])
    expect(fake.filters).toMatchObject([{ rule_set_id: fake.ruleSets?.[0]?.id, enabled: true, spec: SPEC }])
    expect(region).toHaveTextContent('The rule set “Inbox” is disabled')
    expect(region).toHaveTextContent('Run filters is off for work')
    expect(fake.ruleSets?.[0]?.enabled).toBe(false)
    expect(fake.runFilters?.[WORK]).toBeUndefined()

    await userEvent.click(within(region).getByRole('button', { name: 'Enable the rule set' }))
    await waitFor(() => {
      expect(fake.ruleSets?.[0]?.enabled).toBe(true)
    })
    expect(fake.runFilters?.[WORK]).toBeUndefined()
    await userEvent.click(within(region).getByRole('button', { name: 'Turn on Run filters' }))
    expect(await within(region).findByText(`New mail from ${SENDER} now goes to Junk.`)).toBeInTheDocument()
    expect(fake.runFilters?.[WORK]).toBe(true)

    await userEvent.click(within(region).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('region', { name: 'Filter for spam' })).not.toBeInTheDocument()
  })

  it("adds to the inbox's enabled rule set, which acts at once where Run filters is on", async () => {
    const fake = state({
      runFilters: { [WORK]: true },
      ruleSets: [{ ...inboxRules, id: 3, trigger: 'manual' }, { ...inboxRules, id: 5, enabled: false }, inboxRules],
      views: spam === undefined ? {} : { [spam.ref]: messageView(spam) },
    })
    stubMailApi(fake)
    renderMailApp(`/mail?message=${spam?.ref ?? ''}`, withFilters)

    await userEvent.click(within(await actions()).getByRole('button', { name: 'Report spam' }))
    const region = await offer()
    await userEvent.click(within(region).getByRole('button', { name: 'Add the filter' }))
    expect(await within(region).findByText(`New mail from ${SENDER} now goes to Junk.`)).toBeInTheDocument()
    expect(fake.filters).toMatchObject([{ rule_set_id: inboxRules.id, spec: SPEC }])
    expect(fake.ruleSets).toHaveLength(3)
    expect(within(region).queryByRole('button', { name: /Enable|Turn on/ })).not.toBeInTheDocument()
  })

  it('adds to the rule set it made when adding the filter failed, never making another', async () => {
    const fake = state({ refuse: { 'POST /api/v1/rule-sets/1000/filters': { status: 502, body: { detail: 'Bad gateway' } } } })
    stubMailApi(fake)
    renderMailApp('/mail', withFilters)

    await select(await messagesList(), 'Message 9')
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Report spam' }))
    const region = await offer()
    await userEvent.click(within(region).getByRole('button', { name: 'Add the filter' }))
    expect(await within(region).findByRole('alert')).toHaveTextContent('Could not add the filter.')

    fake.refuse = {}
    await userEvent.click(within(region).getByRole('button', { name: 'Add the filter' }))
    await within(region).findByText(/Filter added to the rule set “Inbox”/)
    expect(fake.ruleSets).toHaveLength(1)
    expect(fake.filters).toMatchObject([{ rule_set_id: 1000 }])
  })

  it('names a filter for a long sender within the 200 characters a name may have', async () => {
    const long = `${'x'.repeat(240)}@example.org`
    const sender = messageItem(WORK, 'INBOX', 9, { from: { name: '', address: long } })
    const fake = state({ messages: { '1/INBOX': [sender] } })
    stubMailApi(fake)
    renderMailApp('/mail', withFilters)

    await select(await messagesList(), 'Message 9')
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Report spam' }))
    const region = await offer()
    await userEvent.click(within(region).getByRole('button', { name: 'Add the filter' }))
    await within(region).findByText(/Filter added to the rule set “Inbox”/)
    const [filter] = fake.filters ?? []
    expect(filter?.name).toHaveLength(200)
    expect(filter?.name.endsWith('…')).toBe(true)
    expect(filter?.spec).toEqual({ criterion: { type: 'from_is', args: { addr: long } }, action: { type: 'junk' } })
  })

  it.each([
    ['Enable the rule set', 'PATCH /api/v1/rule-sets/1000'],
    ['Turn on Run filters', `PUT /api/v1/accounts/${String(WORK)}/filter-settings`],
  ])('says so when %s is refused, and keeps the button', async (button, request) => {
    const fake = state({ ruleSets: button === 'Turn on Run filters' ? [{ ...inboxRules, id: 1000 }] : [] })
    stubMailApi(fake)
    renderMailApp('/mail', withFilters)

    await select(await messagesList(), 'Message 9')
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Report spam' }))
    const region = await offer()
    await userEvent.click(within(region).getByRole('button', { name: 'Add the filter' }))
    await within(region).findByText(/Filter added to the rule set/)
    fake.refuse = { [request]: { status: 502, body: { detail: 'Bad gateway' } } }
    await userEvent.click(within(region).getByRole('button', { name: button }))
    expect(await within(region).findByRole('alert')).toHaveTextContent('Could not turn it on.')
    expect(within(region).getByRole('button', { name: button })).toBeEnabled()
  })

  it('is taken away by another report of spam, even one that is refused', async () => {
    const fake = state()
    stubMailApi(fake)
    renderMailApp('/mail', withFilters)

    const list = await messagesList()
    await select(list, 'Message 9')
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Report spam' }))
    await offer()
    fake.refuse = { 'POST /api/v1/mail/messages/junk': { status: 502, body: { detail: 'Bad gateway' } } }
    await select(list, 'Message 8')
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Report spam' }))
    await waitFor(() => {
      expect(screen.queryByRole('region', { name: 'Filter for spam' })).not.toBeInTheDocument()
    })
  })

  it('is gone once the report is undone, or declined', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail', withFilters)

    const list = await messagesList()
    await select(list, 'Message 9')
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Report spam' }))
    await offer()
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => {
      expect(screen.queryByRole('region', { name: 'Filter for spam' })).not.toBeInTheDocument()
    })

    await select(list, 'Message 8')
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Report spam' }))
    await userEvent.click(within(await offer()).getByRole('button', { name: 'No thanks' }))
    expect(screen.queryByRole('region', { name: 'Filter for spam' })).not.toBeInTheDocument()
    expect(filterRequests(fetchMock)).toEqual([])
  })

  it('is not offered for several senders', async () => {
    stubMailApi(state())
    renderMailApp('/mail', withFilters)

    const list = await messagesList()
    await select(list, 'Message 9')
    await select(list, 'Message 8')
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Report spam' }))
    await waitFor(() => {
      expect(toast()).toHaveTextContent('Reported 2 messages as spam.')
    })
    expect(screen.queryByRole('region', { name: 'Filter for spam' })).not.toBeInTheDocument()
  })

  it('is not offered where the instance runs no filters', async () => {
    stubMailApi(state())
    renderMailApp('/mail', { user: ALICE })

    await select(await messagesList(), 'Message 9')
    await userEvent.click(within(await selection()).getByRole('button', { name: 'Report spam' }))
    await waitFor(() => {
      expect(toast()).toHaveTextContent('Reported 1 message as spam.')
    })
    expect(screen.queryByRole('region', { name: 'Filter for spam' })).not.toBeInTheDocument()
  })
})

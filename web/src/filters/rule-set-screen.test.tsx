import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { sentBody, stubFiltersApi, type FakeState } from '@/test/fake-filters-api.ts'
import { renderFiltersApp } from '@/test/filters-app.tsx'

const NEWS_SPEC = {
  criterion: { type: 'from_contains', args: { addr: 'news@' } },
  action: { type: 'move_to', args: { folder: ['Archive'] } },
}
const OLD_SPEC = {
  criterion: { and: [{ type: 'older_than_days', args: { days: 30 } }, { not: { type: 'is_starred' } }] },
  action: { type: 'trash' },
}

function state(overrides: Partial<FakeState> = {}): FakeState {
  return {
    accounts: [{ id: 'work', run_filters: true }],
    folders: [['INBOX'], ['Archive'], ['Lists', 'Nerds']],
    ruleSets: [
      {
        id: 1,
        account_id: 'work',
        folder: ['INBOX'],
        name: 'Inbox sorting',
        trigger: 'new_mail',
        interval_minutes: null,
        starred_only: false,
        enabled: true,
      },
    ],
    filters: [
      { id: 10, rule_set_id: 1, position: 0, name: 'Newsletters', enabled: true, spec: NEWS_SPEC },
      { id: 11, rule_set_id: 1, position: 1, name: 'Old mail', enabled: true, spec: OLD_SPEC },
    ],
    runs: [],
    messages: [
      { uid: 7, from: 'News <news@example.org>', subject: 'Weekly digest', date: '2026-10-19T08:30:00' },
      { uid: 9, from: 'news@example.org', subject: null, date: null },
    ],
    ...overrides,
  }
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function filterList(): Promise<HTMLElement> {
  return screen.findByRole('list', { name: 'Filters' })
}

describe('RuleSetScreen', () => {
  it('shows the rule set and its filters in order, each summed up', async () => {
    stubFiltersApi(state())
    renderFiltersApp('/filters/1')

    expect(await screen.findByRole('heading', { level: 1, name: 'Inbox sorting' })).toBeInTheDocument()
    expect(screen.getByText('work · INBOX · on new mail · enabled')).toBeInTheDocument()
    const items = within(await filterList()).getAllByRole('listitem')
    expect(items.map((item) => within(item).getByText(/^If/).textContent)).toEqual([
      'If the sender contains “news@”, move to Archive',
      'If older than 30 days and not starred, move to the trash',
    ])
  })

  it('moves a filter down, keeping focus on it, and saves the order', async () => {
    const fetchMock = stubFiltersApi(state())
    renderFiltersApp('/filters/1')

    const down = within(await filterList()).getByRole('button', { name: 'Move Newsletters down' })
    expect(within(await filterList()).getByRole('button', { name: 'Move Newsletters up' })).toBeDisabled()
    await userEvent.click(down)

    const names = within(await filterList())
      .getAllByRole('listitem')
      .map((item) => item.querySelector('.font-medium')?.textContent)
    expect(names).toEqual(['Old mail', 'Newsletters'])
    await vi.waitFor(() => {
      expect(screen.getByRole('button', { name: 'Move Newsletters up' })).toHaveFocus()
    })
    expect(sentBody(fetchMock, 'PUT', '/api/v1/rule-sets/1/order')).toEqual({ filter_ids: [11, 10] })
  })

  it('turns a filter off and removes another once confirmed', async () => {
    const fetchMock = stubFiltersApi(state())
    renderFiltersApp('/filters/1')

    const toggle = within(await filterList()).getByRole('switch', { name: 'Enable Old mail' })
    await userEvent.click(toggle)
    await vi.waitFor(() => {
      expect(toggle).not.toBeChecked()
    })
    expect(sentBody(fetchMock, 'PATCH', '/api/v1/filters/11')).toEqual({ enabled: false })

    await userEvent.click(screen.getByRole('button', { name: 'Remove Newsletters' }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove for good' }))
    await vi.waitFor(() => {
      expect(screen.queryByText('Newsletters')).not.toBeInTheDocument()
    })
  })

  it('stops adding filters at the most a rule set holds, saying why', async () => {
    const filters = Array.from({ length: 100 }, (_, n) => ({
      id: 100 + n,
      rule_set_id: 1,
      position: n,
      name: `Filter ${String(n)}`,
      enabled: true,
      spec: NEWS_SPEC,
    }))
    stubFiltersApi(state({ filters }))
    renderFiltersApp('/filters/1')

    await filterList()
    const add = screen.getByRole('button', { name: 'Add a filter' })
    expect(add).toBeDisabled()
    expect(add).toHaveAccessibleDescription(
      'This rule set holds 100 filters, the most it can: remove one to add another.',
    )
  })

  it('adds a filter built in the tree editor', async () => {
    const fetchMock = stubFiltersApi(state({ filters: [] }))
    renderFiltersApp('/filters/1')

    expect(await screen.findByText(/No filters yet/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Add a filter' }))
    const form = screen.getByRole('form', { name: 'Add a filter' })
    await userEvent.type(within(form).getByLabelText('Name'), 'Nerds')

    // The criterion: from is someone, and unread.
    const root = within(form).getByRole('group', { name: 'Condition' })
    await userEvent.selectOptions(within(root).getByLabelText('Type of Condition'), 'From is')
    await userEvent.type(within(root).getByLabelText('Address'), 'list@nerds.edu')
    await userEvent.selectOptions(within(root).getByLabelText('Type of Condition'), 'All of these')
    await userEvent.click(within(root).getByRole('button', { name: 'Add a condition to Condition' }))
    const second = within(root).getByRole('group', { name: 'Condition 2' })
    await userEvent.selectOptions(within(second).getByLabelText('Type of Condition 2'), 'Is unread')

    // The actions: mark as read, then move to a folder.
    const first = within(form).getByRole('group', { name: 'Action 1' })
    await vi.waitFor(() => {
      expect(within(first).getByLabelText('Folder')).toBeEnabled()
    })
    await userEvent.click(within(form).getByRole('button', { name: 'Add an action' }))
    await userEvent.click(within(form).getByRole('button', { name: 'Move Action 2 up' }))
    expect(within(within(form).getByRole('group', { name: 'Action 1' })).getByLabelText('Type of Action 1')).toHaveValue(
      'leaf:mark_as_read',
    )
    const move = within(form).getByRole('group', { name: 'Action 2' })
    expect(within(move).getByLabelText('Folder')).toBeRequired()
    await userEvent.selectOptions(within(move).getByLabelText('Folder'), 'Lists / Nerds')
    await userEvent.click(within(form).getByRole('button', { name: 'Add filter' }))

    const list = await filterList()
    expect(within(list).getByText('Nerds')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add a filter' })).toBeInTheDocument()
    expect(sentBody(fetchMock, 'POST', '/api/v1/rule-sets/1/filters')).toEqual({
      name: 'Nerds',
      enabled: true,
      spec: {
        criterion: { and: [{ type: 'from_is', args: { addr: 'list@nerds.edu' } }, { type: 'is_unread' }] },
        action: { seq: [{ type: 'mark_as_read' }, { type: 'move_to', args: { folder: ['Lists', 'Nerds'] } }] },
      },
    })
  })

  it('edits a filter as JSON, showing what the server refused', async () => {
    const fetchMock = stubFiltersApi(state())
    renderFiltersApp('/filters/1')

    await userEvent.click(within(await filterList()).getByRole('button', { name: 'Edit Old mail' }))
    const form = screen.getByRole('form', { name: 'Edit Old mail' })
    const tree = within(form).getByRole('tab', { name: 'Conditions and actions' })
    expect(tree).toHaveAttribute('aria-selected', 'true')
    const not = within(form).getByRole('group', { name: 'Condition 2' })
    expect(within(not).getByLabelText('Type of Condition 2')).toHaveValue('not')

    await userEvent.click(within(form).getByRole('tab', { name: 'JSON' }))
    const json = within(form).getByLabelText('Filter spec (JSON)')
    expect(JSON.parse((json as HTMLTextAreaElement).value)).toEqual(OLD_SPEC)

    await userEvent.clear(json)
    await userEvent.type(json, '{{"criterion": {{"type": "bogus"}, "action": {{"type": "star"}}')
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent(
      "The filter is not valid: unknown criterion type 'bogus'",
    )

    await userEvent.clear(json)
    await userEvent.type(json, '{{"criterion": {{"nope": 1}, "action": {{"type": "star"}}')
    await userEvent.click(tree)
    expect(within(form).getByRole('alert')).toHaveTextContent('cannot be shown as conditions and actions')
    expect(within(form).getByRole('tab', { name: 'JSON' })).toHaveAttribute('aria-selected', 'true')

    await userEvent.clear(json)
    await userEvent.type(json, '{{"criterion": {{"type": "is_starred"}, "action": {{"type": "unstar"}}')
    await userEvent.click(tree)
    expect(within(form).getByLabelText('Type of Condition')).toHaveValue('leaf:is_starred')
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('button', { name: 'Edit Old mail' })).toBeInTheDocument()
    expect(sentBody(fetchMock, 'PATCH', '/api/v1/filters/11')).toEqual({
      name: 'Old mail',
      enabled: true,
      spec: { criterion: { type: 'is_starred' }, action: { type: 'unstar' } },
    })
  })

  it('dry-runs the rule set and lists what each filter matched', async () => {
    stubFiltersApi(state())
    renderFiltersApp('/filters/1')

    await userEvent.click(await screen.findByRole('button', { name: 'Dry run' }))
    const result = await screen.findByRole('region', { name: 'Dry run' })
    const news = within(result).getByRole('region', { name: 'Newsletters' })
    expect(within(news).getByText('Would move to Archive')).toBeInTheDocument()
    expect(within(news).getByText('2 messages matched')).toBeInTheDocument()
    const rows = within(news).getAllByRole('row')
    expect(rows).toHaveLength(3)
    expect(rows[1]).toHaveTextContent('Weekly digest')
    expect(rows[2]).toHaveTextContent('(no subject)')
    expect(within(result).getByRole('region', { name: 'Old mail' })).toBeInTheDocument()

    // Dry runs are recorded in the history too.
    const history = await screen.findByRole('list', { name: 'Run history' })
    expect(within(history).getByText('Dry run')).toBeInTheDocument()
  })

  it('runs now, and refuses while Run filters is off', async () => {
    const fake = state({ accounts: [{ id: 'work', run_filters: false }] })
    const fetchMock = stubFiltersApi(fake)
    renderFiltersApp('/filters/1')

    const run = await screen.findByRole('button', { name: 'Run now' })
    expect(run).toBeDisabled()
    expect(run).toHaveAccessibleDescription(
      'Run filters is off for work: turn it on in Accounts to run this rule set.',
    )
    expect(await screen.findByText('No runs yet.')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([path]) => path === '/api/v1/rule-sets/1/run')).toBe(false)
  })

  it('words the refusal when Run filters was turned off meanwhile', async () => {
    const fake = state()
    stubFiltersApi(fake)
    renderFiltersApp('/filters/1')

    const run = await screen.findByRole('button', { name: 'Run now' })
    fake.accounts = [{ id: 'work', run_filters: false }]
    await userEvent.click(run)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Run filters is off for this account: turn it on in Accounts first.',
    )
  })

  it('queues a run and shows it in the history', async () => {
    stubFiltersApi(
      state({
        runs: [
          {
            id: 50,
            rule_set_id: 1,
            trigger: 'new_mail',
            created_at: '2026-10-19T09:00:00Z',
            started_at: '2026-10-19T09:00:00Z',
            finished_at: '2026-10-19T09:00:02Z',
            dry_run: false,
            result: {
              account: 'work',
              folder: ['INBOX'],
              dry_run: false,
              filters: [
                {
                  filter_id: 10,
                  spec: NEWS_SPEC,
                  action: 'move_to',
                  matched: [{ uid: 3, from: 'news@example.org', subject: 'Hello', date: null }],
                  matched_total: 250,
                  executed: true,
                },
                {
                  filter_id: 11,
                  spec: OLD_SPEC,
                  action: 'trash',
                  matched: [],
                  executed: false,
                },
              ],
              error: null,
            },
            error: null,
          },
          {
            id: 49,
            rule_set_id: 1,
            trigger: 'scheduled',
            created_at: '2026-10-19T08:00:00Z',
            started_at: '2026-10-19T08:00:00Z',
            finished_at: '2026-10-19T08:00:01Z',
            dry_run: false,
            result: null,
            error: 'login failed',
          },
        ],
      }),
    )
    renderFiltersApp('/filters/1')

    const history = await screen.findByRole('list', { name: 'Run history' })
    const [newest, older] = within(history)
      .getAllByRole('listitem')
      .map((item) => within(item).getByText(/Oct/).parentElement)
    expect(newest).toHaveTextContent('New mail')
    // A run's report keeps the first matches only, with the total.
    expect(newest).toHaveTextContent('250 messages matched')
    expect(older).toHaveTextContent('Scheduled')
    expect(older).toHaveTextContent('Failed')
    await userEvent.click(within(history).getByText('New mail'))
    expect(within(history).getByText('Did: move to Archive')).toBeVisible()
    expect(within(history).getByText('…and 249 more messages.')).toBeVisible()
    expect(within(history).getByText('Nothing to do.')).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: 'Run now' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Run queued')
    await vi.waitFor(() => {
      expect(within(screen.getByRole('list', { name: 'Run history' })).getByText('Queued')).toBeInTheDocument()
    })
  })

  it('follows a queued run until it finishes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const fake = state({
      runs: [
        {
          id: 60,
          rule_set_id: 1,
          trigger: 'manual',
          created_at: '2026-10-19T09:00:00Z',
          started_at: null,
          finished_at: null,
          dry_run: false,
          result: null,
          error: null,
        },
      ],
    })
    stubFiltersApi(fake)
    renderFiltersApp('/filters/1')

    const history = await screen.findByRole('list', { name: 'Run history' })
    expect(within(history).getByText('Queued')).toBeInTheDocument()
    const [queued] = fake.runs
    if (queued === undefined) {
      throw new Error('no queued run')
    }
    fake.runs[0] = {
      ...queued,
      started_at: '2026-10-19T09:00:01Z',
      finished_at: '2026-10-19T09:00:02Z',
      result: { account: 'work', folder: ['INBOX'], dry_run: false, filters: [], error: null },
    }
    await vi.advanceTimersByTimeAsync(2000)
    await vi.waitFor(() => {
      expect(within(history).getByText('0 messages matched')).toBeInTheDocument()
    })
  })

  it('edits the rule set settings', async () => {
    const fetchMock = stubFiltersApi(state())
    renderFiltersApp('/filters/1')

    await userEvent.click(await screen.findByRole('button', { name: 'Edit settings' }))
    const form = screen.getByRole('form', { name: 'Edit Inbox sorting' })
    await userEvent.click(within(form).getByLabelText('Only on Run now'))
    await userEvent.click(within(form).getByLabelText('Enabled'))
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('work · INBOX · only on Run now · disabled')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run now' })).toHaveAccessibleDescription(
      'This rule set is disabled: enable it to run it.',
    )
    expect(sentBody(fetchMock, 'PATCH', '/api/v1/rule-sets/1')).toMatchObject({
      trigger: 'manual',
      interval_minutes: null,
      enabled: false,
    })
  })

  it('shows mail content as text, never as markup', async () => {
    stubFiltersApi(
      state({
        messages: [{ uid: 1, from: '<b>Eve</b>', subject: '<img src=x onerror=alert(1)>', date: null }],
        limitedToNewest: 500,
      }),
    )
    renderFiltersApp('/filters/1')

    await userEvent.click(await screen.findByRole('button', { name: 'Dry run' }))
    const result = await screen.findByRole('region', { name: 'Dry run' })
    expect(within(result).getAllByText('<img src=x onerror=alert(1)>')).not.toHaveLength(0)
    expect(within(result).getAllByText('<b>Eve</b>')).not.toHaveLength(0)
    expect(result.querySelector('img, b')).toBeNull()
    expect(within(result).getByText('Only the newest 500 messages were looked at.')).toBeInTheDocument()
  })

  it('puts the order back when the server refuses it', async () => {
    stubFiltersApi(
      state({
        refuse: {
          'PUT /api/v1/rule-sets/1/order': {
            status: 400,
            body: { detail: { code: 'invalid_order', message: 'The filters changed.' } },
          },
        },
      }),
    )
    renderFiltersApp('/filters/1')

    await userEvent.click(within(await filterList()).getByRole('button', { name: 'Move Newsletters down' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The filters changed meanwhile.')
    const names = within(await filterList())
      .getAllByRole('listitem')
      .map((item) => item.querySelector('.font-medium')?.textContent)
    expect(names).toEqual(['Newsletters', 'Old mail'])
  })

  it('keeps both of two changes that overlap', async () => {
    let release = (): void => undefined
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    stubFiltersApi(state({ gates: { 'PATCH /api/v1/filters/10': gate } }))
    renderFiltersApp('/filters/1')

    const list = await filterList()
    const news = within(list).getByRole('switch', { name: 'Enable Newsletters' })
    const old = within(list).getByRole('switch', { name: 'Enable Old mail' })
    await userEvent.click(news)
    await userEvent.click(old)
    await vi.waitFor(() => {
      expect(old).not.toBeChecked()
    })
    release()
    await vi.waitFor(() => {
      expect(news).not.toBeChecked()
    })
    expect(old).not.toBeChecked()
  })

  it('says why the folders cannot be listed when a certificate did not verify', async () => {
    stubFiltersApi(
      state({
        refuse: {
          'GET /api/v1/accounts/work/folders': {
            status: 502,
            body: {
              detail: {
                code: 'unreachable',
                message: 'The mail server for this account could not be reached. Its TLS certificate did not verify.',
              },
            },
          },
        },
      }),
    )
    renderFiltersApp('/filters/1')

    await userEvent.click(await screen.findByRole('button', { name: 'Edit settings' }))
    const form = screen.getByRole('form', { name: 'Edit Inbox sorting' })
    expect(within(form).getByLabelText('Folder')).toHaveAccessibleDescription(
      /could not be reached\. Its TLS certificate did not verify\./,
    )
  })

  it('takes a typed folder path when the folders cannot be listed', async () => {
    const fetchMock = stubFiltersApi(
      state({
        refuse: {
          'GET /api/v1/accounts/work/folders': {
            status: 502,
            body: { detail: { code: 'unreachable', message: 'The mail server for this account could not be reached.' } },
          },
        },
      }),
    )
    renderFiltersApp('/filters/1')

    await userEvent.click(await screen.findByRole('button', { name: 'Edit settings' }))
    const form = screen.getByRole('form', { name: 'Edit Inbox sorting' })
    const folder = within(form).getByLabelText('Folder')
    expect(folder).toHaveAccessibleDescription(/The mail server for this account could not be reached\./)
    await userEvent.clear(folder)
    await userEvent.type(folder, 'Archive/2026')
    expect(folder).toHaveValue('Archive/2026')
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))

    await vi.waitFor(() => {
      expect(sentBody(fetchMock, 'PATCH', '/api/v1/rule-sets/1')).toMatchObject({ folder: ['Archive', '2026'] })
    })
  })

  it('shows the error of a run stopped part-way once, in the reader’s words', async () => {
    stubFiltersApi(
      state({
        runs: [
          {
            id: 70,
            rule_set_id: 1,
            trigger: 'scheduled',
            created_at: '2026-10-19T09:00:00Z',
            started_at: '2026-10-19T09:00:00Z',
            finished_at: '2026-10-19T09:00:02Z',
            dry_run: false,
            result: {
              account: 'work',
              folder: ['INBOX'],
              dry_run: false,
              filters: [],
              error: 'auth_failed: The mail server rejected the login for this account.',
            },
            error: 'auth_failed: The mail server rejected the login for this account.',
          },
        ],
      }),
    )
    renderFiltersApp('/filters/1')

    const history = await screen.findByRole('list', { name: 'Run history' })
    await userEvent.click(within(history).getByText('Scheduled'))
    expect(
      within(history).getAllByText(
        'Stopped by an error: The mail server rejected the login for this account. Check its password in Accounts.',
      ),
    ).toHaveLength(1)
  })

  it('says so when the rule set does not exist', async () => {
    stubFiltersApi(state())
    renderFiltersApp('/filters/abc')
    expect(await screen.findByRole('alert')).toHaveTextContent('There is no such rule set')
  })
})

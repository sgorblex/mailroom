import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { bodiesTo, type FakeMailState, messageItem, refusal, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 1 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
        { path: 'Lists', role: null, unread: 0, total: 0 },
        { path: 'Lists/Rust', role: null, unread: 0, total: 0 },
        { path: 'Projects', role: null, unread: 0, total: 3 },
      ],
    },
    messages: {
      '1/INBOX': [messageItem(WORK, 'INBOX', 1)],
      '1/Projects': [messageItem(WORK, 'Projects', 3), messageItem(WORK, 'Projects', 2), messageItem(WORK, 'Projects', 1)],
      '1/Lists': [],
    },
    views: {},
    ...overrides,
  }
}

const tree = (): Promise<HTMLElement> => screen.findByRole('tree', { name: 'Folders of work' })

/** Opens the actions menu of the folder named `name`, from the keyboard. */
async function menuOf(name: string | RegExp): Promise<HTMLElement> {
  within(await tree()).getByRole('treeitem', { name }).focus()
  await userEvent.keyboard('{Shift>}{F10}{/Shift}')
  return screen.getByRole('menu')
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('folder actions', () => {
  it('opens an accessible menu from the keyboard, and closes it back onto the folder', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const projects = within(await tree()).getByRole('treeitem', { name: 'Projects' })
    expect(projects).toHaveAttribute('aria-haspopup', 'menu')
    const menu = await menuOf('Projects')
    expect(menu).toHaveAccessibleName('Actions for Projects')
    const items = within(menu).getAllByRole('menuitem')
    expect(items.map((item) => item.textContent)).toEqual([
      'Mark all as readNo unread messages.',
      'New subfolder…',
      'Rename or move…',
      'Delete…',
    ])
    expect(items[0]).toHaveAttribute('aria-disabled', 'true')
    expect(items[1]).toHaveFocus()
    await userEvent.keyboard('{ArrowDown}')
    expect(items[2]).toHaveFocus()
    await userEvent.keyboard('{ArrowUp}{ArrowUp}{ArrowUp}')
    expect(items[3]).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(projects).toHaveFocus()
  })

  it('opens the same menu on a right-click, and leaves Shift+right-click to the browser', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const projects = within(await tree()).getByRole('treeitem', { name: 'Projects' })
    expect(fireEvent.contextMenu(projects, { shiftKey: true })).toBe(true)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(fireEvent.contextMenu(projects, { clientX: 30, clientY: 50 })).toBe(false)
    const menu = screen.getByRole('menu', { name: 'Actions for Projects' })
    expect(within(menu).getAllByRole('menuitem').map((item) => item.textContent)).toEqual([
      'Mark all as readNo unread messages.',
      'New subfolder…',
      'Rename or move…',
      'Delete…',
    ])
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(projects).toHaveFocus()
  })

  it('disables what the server would refuse, saying why', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    const inbox = await menuOf(/^Inbox/)
    for (const name of [/^Rename or move…/, /^Delete…/]) {
      const item = within(inbox).getByRole('menuitem', { name })
      expect(item).toHaveAttribute('aria-disabled', 'true')
      expect(item).toHaveTextContent("The inbox and folders with a role can't be renamed or deleted.")
    }
    await userEvent.click(within(inbox).getByRole('menuitem', { name: /^Delete…/ }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await userEvent.keyboard('{Escape}')

    const lists = await menuOf('Lists')
    expect(within(lists).getByRole('menuitem', { name: /^Delete…/ })).toHaveTextContent('Delete or move its subfolders first.')
    expect(within(lists).getByRole('menuitem', { name: 'Rename or move…' })).not.toHaveAttribute('aria-disabled')
  })

  it('creates a folder at the top level and a subfolder, checking the name first', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    await userEvent.click(await screen.findByRole('button', { name: 'New folder in work' }))
    const dialog = screen.getByRole('dialog', { name: 'New folder' })
    const name = within(dialog).getByRole('textbox', { name: 'Name' })
    expect(name).toHaveFocus()
    await userEvent.type(name, 'a/b')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create' }))
    expect(within(dialog).getByText("A name can't contain “/”, “*”, “%” or control characters.")).toBeInTheDocument()
    await userEvent.clear(name)
    await userEvent.type(name, 'Receipts')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create' }))
    expect(await within(await tree()).findByRole('treeitem', { name: 'Receipts' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText('Created Receipts.')).toHaveAttribute('role', 'status')

    await userEvent.click(within(await menuOf('Projects')).getByRole('menuitem', { name: 'New subfolder…' }))
    const sub = screen.getByRole('dialog', { name: 'New folder' })
    expect(sub).toHaveTextContent('Inside Projects.')
    await userEvent.type(within(sub).getByRole('textbox', { name: 'Name' }), '2026')
    await userEvent.click(within(sub).getByRole('button', { name: 'Create' }))
    // The parent opens on the new folder.
    expect(await within(await tree()).findByRole('treeitem', { name: '2026' })).toHaveAttribute('aria-level', '2')
    // The listings are GETs to the same path, with no body.
    expect(bodiesTo(fetchMock, '/api/v1/mail/accounts/1/folders').filter((body) => body !== null)).toEqual([
      { parent: null, name: 'Receipts' },
      { parent: 'Projects', name: '2026' },
    ])
  })

  it('refuses a name that exists, keeping the dialog', async () => {
    stubMailApi(state())
    renderMailApp('/mail')

    await userEvent.click(await screen.findByRole('button', { name: 'New folder in work' }))
    const dialog = screen.getByRole('dialog', { name: 'New folder' })
    await userEvent.type(within(dialog).getByRole('textbox', { name: 'Name' }), 'Projects')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Create' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('A folder with that name already exists there.')
  })

  it('renames and moves the folder on screen, following it to its new path', async () => {
    const fetchMock = stubMailApi(state())
    const history = renderMailApp('/mail?account=1&folder=Projects')

    await userEvent.click(within(await menuOf('Projects')).getByRole('menuitem', { name: 'Rename or move…' }))
    const dialog = screen.getByRole('dialog', { name: 'Rename or move “Projects”' })
    expect(dialog).toHaveTextContent('Rule sets that use it, or a folder inside it, follow it to the new name.')
    const name = within(dialog).getByRole('textbox', { name: 'Name' })
    expect(name).toHaveValue('Projects')
    const parent = within(dialog).getByRole('combobox', { name: 'Inside' })
    expect(within(parent).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Top level',
      'INBOX',
      'Lists',
      'Lists/Rust',
      'Trash',
    ])
    await userEvent.selectOptions(parent, 'Lists')
    await userEvent.clear(name)
    await userEvent.type(name, 'Work')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Rename' }))

    await waitFor(() => {
      expect(history.location.search).toContain('folder=Lists%2FWork')
    })
    expect(await within(await tree()).findByRole('treeitem', { name: 'Work' })).toHaveAttribute('aria-current', 'page')
    expect(bodiesTo(fetchMock, '/api/v1/mail/accounts/1/folders/rename')).toEqual([
      { folder: 'Projects', parent: 'Lists', name: 'Work' },
    ])
    expect(screen.getByText('Renamed to Lists/Work.')).toBeInTheDocument()
  })

  it('says when the rule sets could not follow a rename', async () => {
    stubMailApi(
      state({
        refuse: {
          'POST /api/v1/mail/accounts/1/folders/rename': {
            status: 200,
            body: {
              account_id: WORK,
              folder: 'Projects',
              path: 'Work',
              rule_sets: [],
              filters: [],
              rule_sets_updated: false,
              all_accounts_rule_sets: [],
            },
          },
        },
      }),
    )
    renderMailApp('/mail')

    await userEvent.click(within(await menuOf('Projects')).getByRole('menuitem', { name: 'Rename or move…' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.clear(within(dialog).getByRole('textbox', { name: 'Name' }))
    await userEvent.type(within(dialog).getByRole('textbox', { name: 'Name' }), 'Work')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Rename' }))
    expect((await screen.findByText(/they still name the old folder/)).closest('[role="alert"]')).not.toBeNull()
  })

  it('says when rule sets for all accounts still name the old path', async () => {
    stubMailApi(
      state({
        refuse: {
          'POST /api/v1/mail/accounts/1/folders/rename': {
            status: 200,
            body: {
              account_id: WORK,
              folder: 'Projects',
              path: 'Work',
              rule_sets: [],
              filters: [],
              rule_sets_updated: true,
              all_accounts_rule_sets: [7, 8],
            },
          },
        },
      }),
    )
    renderMailApp('/mail')

    await userEvent.click(within(await menuOf('Projects')).getByRole('menuitem', { name: 'Rename or move…' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.clear(within(dialog).getByRole('textbox', { name: 'Name' }))
    await userEvent.type(within(dialog).getByRole('textbox', { name: 'Name' }), 'Work')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Rename' }))
    const notice = await screen.findByText(/2 rule sets for all accounts still name the old path/)
    expect(notice).toHaveTextContent('Renamed to Work.')
    expect(notice.closest('[role="alert"]')).not.toBeNull()
  })

  it('deletes after confirming the count, and asks again when it changed', async () => {
    const fake = state()
    const fetchMock = stubMailApi(fake)
    const history = renderMailApp('/mail?account=1&folder=Projects')

    await userEvent.click(within(await menuOf('Projects')).getByRole('menuitem', { name: 'Delete…' }))
    const dialog = screen.getByRole('dialog', { name: 'Delete “Projects”' })
    expect(
      await within(dialog).findByText(
        'Delete “Projects” and its 3 messages for good? They do not go to Trash and cannot be recovered.',
      ),
    ).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus()

    // Mail arrives while the user reads.
    const projects = fake.folders[WORK]?.find((folder) => folder.path === 'Projects')
    if (projects !== undefined) {
      projects.total = 4
    }
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete for good' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('It now holds 4 messages.')
    // A new question: Enter again does not answer it unread.
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus()
    expect(within(dialog).getByText(/and its 4 messages for good\?/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete for good' }))

    await waitFor(() => {
      expect(history.location.search).not.toContain('folder=Projects')
    })
    expect(within(await tree()).queryByRole('treeitem', { name: 'Projects' })).not.toBeInTheDocument()
    expect(bodiesTo(fetchMock, '/api/v1/mail/accounts/1/folders/delete')).toEqual([
      { folder: 'Projects', messages: 3 },
      { folder: 'Projects', messages: 4 },
    ])
  })

  it('lists the rule sets that keep a folder from being deleted', async () => {
    stubMailApi(
      state({
        refuse: {
          'POST /api/v1/mail/accounts/1/folders/delete': {
            status: 409,
            body: { detail: { code: 'folder_in_use', message: 'Rule sets use this folder.', rule_sets: [{ id: 7, name: 'Newsletters' }] } },
          },
        },
      }),
    )
    renderMailApp('/mail')

    await userEvent.click(within(await menuOf('Projects')).getByRole('menuitem', { name: 'Delete…' }))
    const dialog = screen.getByRole('dialog')
    await userEvent.click(await within(dialog).findByRole('button', { name: 'Delete for good' }))
    const alert = await within(dialog).findByRole('alert')
    expect(alert).toHaveTextContent('Rule sets use this folder or one inside it.')
    expect(within(alert).getByRole('link', { name: 'Newsletters' })).toHaveAttribute('href', '/filters/7')
    expect(within(dialog).queryByRole('button', { name: 'Delete for good' })).not.toBeInTheDocument()
  })

  describe('mark all as read', () => {
    const unseen = { flags: { seen: false, flagged: false, answered: false, forwarded: false, draft: false, to_sort: false } }
    /** News: `unread` as listed, its three messages unread, UIDNEXT 3 when listed (so 3 came after). */
    function news(unread: number): FakeMailState {
      const fake = state()
      fake.folders[WORK]?.push({ path: 'News', role: null, unread, total: 3, uid_validity: 7, uid_next: 3 })
      fake.messages['1/News'] = [3, 2, 1].map((uid) => messageItem(WORK, 'News', uid, unseen))
      return fake
    }

    it('marks the folder read up to the UIDNEXT listed, then reads its counts and list again', async () => {
      const fetchMock = stubMailApi(news(3))
      renderMailApp('/mail?account=1&folder=News')

      const list = await screen.findByRole('list', { name: 'Messages' })
      expect(await within(list).findAllByRole('link', { name: /Unread:/ })).toHaveLength(3)
      await userEvent.click(within(await menuOf('News, 3 unread')).getByRole('menuitem', { name: 'Mark all as read' }))

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(await screen.findByText('Marked 2 messages read in News.')).toHaveAttribute('role', 'status')
      expect(bodiesTo(fetchMock, '/api/v1/mail/accounts/1/folders/mark-read')).toEqual([{ folder: 'News', uid_validity: 7, uid_next: 3 }])
      expect(await within(await tree()).findByRole('treeitem', { name: 'News, 1 unread' })).toBeInTheDocument()
      await waitFor(() => {
        expect(within(list).getAllByRole('link', { name: /Unread:/ })).toHaveLength(1)
      })
      expect(within(list).getByRole('link', { name: /Unread:\s*Message 3/ })).toBeInTheDocument()
    })

    it('asks first above 50 unread, stating the count', async () => {
      const fetchMock = stubMailApi(news(51))
      renderMailApp('/mail')

      await userEvent.click(within(await menuOf('News, 51 unread')).getByRole('menuitem', { name: 'Mark all as read' }))
      let dialog = screen.getByRole('dialog', { name: 'Mark “News” as read' })
      expect(dialog).toHaveTextContent('Mark the 51 unread messages in “News” as read? Mail that arrives meanwhile stays unread.')
      await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
      expect(bodiesTo(fetchMock, '/api/v1/mail/accounts/1/folders/mark-read')).toEqual([])

      await userEvent.click(within(await menuOf('News, 51 unread')).getByRole('menuitem', { name: 'Mark all as read' }))
      dialog = screen.getByRole('dialog', { name: 'Mark “News” as read' })
      await userEvent.click(within(dialog).getByRole('button', { name: 'Mark as read' }))
      expect(await screen.findByText('Marked 2 messages read in News.')).toBeInTheDocument()
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(bodiesTo(fetchMock, '/api/v1/mail/accounts/1/folders/mark-read')).toEqual([{ folder: 'News', uid_validity: 7, uid_next: 3 }])
    })

    it('says when the folder changed since it was listed, and lists it again', async () => {
      const fetchMock = stubMailApi({
        ...news(51),
        refuse: { 'POST /api/v1/mail/accounts/1/folders/mark-read': refusal(409, 'stale_reference') },
      })
      renderMailApp('/mail')

      await userEvent.click(within(await menuOf('News, 51 unread')).getByRole('menuitem', { name: 'Mark all as read' }))
      const dialog = screen.getByRole('dialog')
      const listings = fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/folders?all=true')).length
      await userEvent.click(within(dialog).getByRole('button', { name: 'Mark as read' }))
      expect(await within(dialog).findByRole('alert')).toHaveTextContent('The folder changed since it was listed')
      expect(within(dialog).queryByRole('button', { name: 'Mark as read' })).not.toBeInTheDocument()
      await waitFor(() => {
        expect(fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/folders?all=true')).length).toBe(listings + 1)
      })
    })

    it('says so under the tree when a mark-read without a question is refused, and lists the folders again', async () => {
      const fetchMock = stubMailApi({
        ...news(3),
        refuse: { 'POST /api/v1/mail/accounts/1/folders/mark-read': refusal(409, 'stale_reference') },
      })
      renderMailApp('/mail')

      const listings = (): number => fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/folders?all=true')).length
      const menu = await menuOf('News, 3 unread')
      const before = listings()
      await userEvent.click(within(menu).getByRole('menuitem', { name: 'Mark all as read' }))
      const alert = (await screen.findByText(/^The folder changed since it was listed/)).closest('[role="alert"]')
      if (!(alert instanceof HTMLElement)) {
        throw new Error('no alert')
      }
      expect(within(alert).getByRole('button', { name: 'Dismiss' })).toBeInTheDocument()
      await waitFor(() => {
        expect(listings()).toBe(before + 1)
      })
    })

    it('sends one mark-read for a second click while the first is under way', async () => {
      let release = (): void => undefined
      const held = new Promise<void>((resolve) => {
        release = resolve
      })
      const fetchMock = stubMailApi({
        ...news(3),
        hold: (request) => (request === 'POST /api/v1/mail/accounts/1/folders/mark-read' ? held : undefined),
      })
      renderMailApp('/mail')

      await userEvent.click(within(await menuOf('News, 3 unread')).getByRole('menuitem', { name: 'Mark all as read' }))
      await userEvent.click(within(await menuOf('News, 3 unread')).getByRole('menuitem', { name: 'Mark all as read' }))
      release()
      expect(await screen.findByText('Marked 2 messages read in News.')).toBeInTheDocument()
      expect(bodiesTo(fetchMock, '/api/v1/mail/accounts/1/folders/mark-read')).toHaveLength(1)
    })

    /** Lists (2 unread) with Lists/Rust (1), Lists/Quiet (none) and Lists/Junk (a role kept apart, 5). */
    function lists(listsUnread = 2): FakeMailState {
      const fake = state()
      fake.folders[WORK] = [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 1 },
        { path: 'Lists', role: null, unread: listsUnread, total: 2, uid_validity: 7, uid_next: 3 },
        { path: 'Lists/Rust', role: null, unread: 1, total: 1, uid_validity: 8, uid_next: 2 },
        { path: 'Lists/Quiet', role: null, unread: 0, total: 1, uid_validity: 9, uid_next: 2 },
        { path: 'Lists/Junk', role: 'junk', unread: 5, total: 5, uid_validity: 10, uid_next: 6 },
      ]
      fake.messages['1/Lists'] = [2, 1].map((uid) => messageItem(WORK, 'Lists', uid, unseen))
      fake.messages['1/Lists/Rust'] = [messageItem(WORK, 'Lists/Rust', 1, unseen)]
      fake.messages['1/Lists/Junk'] = [5, 4, 3, 2, 1].map((uid) => messageItem(WORK, 'Lists/Junk', uid, unseen))
      return fake
    }
    const ALICE = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true }
    const markReads = (fetchMock: ReturnType<typeof stubMailApi>): unknown[] => bodiesTo(fetchMock, '/api/v1/mail/accounts/1/folders/mark-read')

    it('offers a folder with subfolders both ways, with them first, and marks each listed one up to its own UIDNEXT', async () => {
      const fetchMock = stubMailApi(lists())
      renderMailApp('/mail')

      const menu = await menuOf(/^Lists/)
      expect(within(menu).getAllByRole('menuitem').map((item) => item.textContent).slice(0, 2)).toEqual([
        'Mark all as read, with subfolders',
        'Mark all as read, this folder only',
      ])
      await userEvent.click(within(menu).getByRole('menuitem', { name: 'Mark all as read, with subfolders' }))

      expect(await screen.findByText('Marked 3 messages read in Lists and its subfolders.')).toHaveAttribute('role', 'status')
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      // Not Lists/Quiet, listed with none unread, nor Lists/Junk, a role kept apart.
      expect(markReads(fetchMock)).toEqual([
        { folder: 'Lists', uid_validity: 7, uid_next: 3 },
        { folder: 'Lists/Rust', uid_validity: 8, uid_next: 2 },
      ])
    })

    it('marks the folder alone from its second item', async () => {
      const fetchMock = stubMailApi(lists())
      renderMailApp('/mail')

      await userEvent.click(within(await menuOf(/^Lists/)).getByRole('menuitem', { name: 'Mark all as read, this folder only' }))
      expect(await screen.findByText('Marked 2 messages read in Lists.')).toBeInTheDocument()
      expect(markReads(fetchMock)).toEqual([{ folder: 'Lists', uid_validity: 7, uid_next: 3 }])
    })

    it('offers the folder alone first when the user chose so', async () => {
      stubMailApi(lists())
      renderMailApp('/mail', { user: { ...ALICE, mark_read_subfolders: false } })

      const items = within(await menuOf(/^Lists/)).getAllByRole('menuitem')
      expect(items.map((item) => item.textContent).slice(0, 2)).toEqual([
        'Mark all as read, this folder only',
        'Mark all as read, with subfolders',
      ])
    })

    it('asks first above 50 unread in all, naming each folder and the sum', async () => {
      const fetchMock = stubMailApi(lists(50))
      renderMailApp('/mail', { user: ALICE })

      await userEvent.click(within(await menuOf(/^Lists/)).getByRole('menuitem', { name: 'Mark all as read, with subfolders' }))
      const dialog = screen.getByRole('dialog', { name: 'Mark “Lists” and its subfolders as read' })
      expect(dialog).toHaveTextContent('Mark the 51 unread messages in these folders as read? Mail that arrives meanwhile stays unread.')
      expect(within(within(dialog).getByRole('list', { name: 'Folders to mark read' })).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
        'Lists: 50 unread',
        'Lists/Rust: 1 unread',
      ])
      expect(markReads(fetchMock)).toEqual([])

      await userEvent.click(within(dialog).getByRole('button', { name: 'Mark as read' }))
      expect(await screen.findByText('Marked 3 messages read in Lists and its subfolders.')).toBeInTheDocument()
      expect(markReads(fetchMock)).toHaveLength(2)
    })

    it('marks the other folders when one changed since it was listed, saying which was not', async () => {
      const fake = lists()
      const fetchMock = stubMailApi(fake)
      renderMailApp('/mail')

      const menu = await menuOf(/^Lists/)
      const rust = fake.folders[WORK]?.find((folder) => folder.path === 'Lists/Rust')
      if (rust === undefined) {
        throw new Error('no Lists/Rust')
      }
      rust.uid_validity = 80
      await userEvent.click(within(menu).getByRole('menuitem', { name: 'Mark all as read, with subfolders' }))

      expect(await screen.findByText(/^Marked 2 messages read, but not in Lists\/Rust: The folder changed since it was listed/)).toBeInTheDocument()
      expect(markReads(fetchMock)).toHaveLength(2)
    })

    it('offers a level holding only folders its subfolders', async () => {
      const fake = lists()
      fake.folders[WORK]?.push(
        { path: 'Box', role: null, unread: 0, total: 0, selectable: false, uid_validity: null, uid_next: null },
        { path: 'Box/Leaf', role: null, unread: 1, total: 1, uid_validity: 11, uid_next: 2 },
      )
      fake.messages['1/Box/Leaf'] = [messageItem(WORK, 'Box/Leaf', 1, unseen)]
      const fetchMock = stubMailApi(fake)
      renderMailApp('/mail')

      const menu = await menuOf(/^Box/)
      expect(within(menu).getAllByRole('menuitem', { name: /Mark all as read/ }).map((item) => item.textContent)).toEqual([
        'Mark all as read, with subfolders',
      ])
      await userEvent.click(within(menu).getByRole('menuitem', { name: 'Mark all as read, with subfolders' }))
      expect(await screen.findByText('Marked 1 message read in Box and its subfolders.')).toBeInTheDocument()
      expect(markReads(fetchMock)).toEqual([{ folder: 'Box/Leaf', uid_validity: 11, uid_next: 2 }])
    })

    it('marks a folder its listing gave no UIDs, sending none', async () => {
      const fake = state()
      fake.folders[WORK]?.push({ path: 'News', role: null, unread: 1, total: 1, uid_validity: null, uid_next: null })
      fake.messages['1/News'] = [messageItem(WORK, 'News', 1, unseen)]
      const fetchMock = stubMailApi(fake)
      renderMailApp('/mail')

      await userEvent.click(within(await menuOf('News, 1 unread')).getByRole('menuitem', { name: 'Mark all as read' }))
      expect(await screen.findByText('Marked 1 message read in News.')).toBeInTheDocument()
      expect(markReads(fetchMock)).toEqual([{ folder: 'News', uid_validity: null, uid_next: null }])
    })
  })
})

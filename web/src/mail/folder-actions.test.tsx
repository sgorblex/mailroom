import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { bodiesTo, messageItem, stubMailApi, type FakeMailState } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: ['work'],
    folders: {
      work: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 1 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
        { path: 'Lists', role: null, unread: 0, total: 0 },
        { path: 'Lists/Rust', role: null, unread: 0, total: 0 },
        { path: 'Projects', role: null, unread: 0, total: 3 },
      ],
    },
    messages: {
      'work/INBOX': [messageItem('work', 'INBOX', 1)],
      'work/Projects': [messageItem('work', 'Projects', 3), messageItem('work', 'Projects', 2), messageItem('work', 'Projects', 1)],
      'work/Lists': [],
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
    expect(items.map((item) => item.textContent)).toEqual(['New subfolder…', 'Rename or move…', 'Delete…'])
    expect(items[0]).toHaveFocus()
    await userEvent.keyboard('{ArrowDown}')
    expect(items[1]).toHaveFocus()
    await userEvent.keyboard('{ArrowUp}{ArrowUp}')
    expect(items[2]).toHaveFocus()
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
    expect(bodiesTo(fetchMock, '/api/v1/mail/accounts/work/folders').filter((body) => body !== null)).toEqual([
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
    const history = renderMailApp('/mail?account=work&folder=Projects')

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
    expect(bodiesTo(fetchMock, '/api/v1/mail/accounts/work/folders/rename')).toEqual([
      { folder: 'Projects', parent: 'Lists', name: 'Work' },
    ])
    expect(screen.getByText('Renamed to Lists/Work.')).toBeInTheDocument()
  })

  it('says when the rule sets could not follow a rename', async () => {
    stubMailApi(
      state({
        refuse: {
          'POST /api/v1/mail/accounts/work/folders/rename': {
            status: 200,
            body: { account_id: 'work', folder: 'Projects', path: 'Work', rule_sets: [], filters: [], rule_sets_updated: false },
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

  it('deletes after confirming the count, and asks again when it changed', async () => {
    const fake = state()
    const fetchMock = stubMailApi(fake)
    const history = renderMailApp('/mail?account=work&folder=Projects')

    await userEvent.click(within(await menuOf('Projects')).getByRole('menuitem', { name: 'Delete…' }))
    const dialog = screen.getByRole('dialog', { name: 'Delete “Projects”' })
    expect(
      await within(dialog).findByText(
        'Delete “Projects” and its 3 messages for good? They do not go to Trash and cannot be recovered.',
      ),
    ).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus()

    // Mail arrives while the user reads.
    const projects = fake.folders['work']?.find((folder) => folder.path === 'Projects')
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
    expect(bodiesTo(fetchMock, '/api/v1/mail/accounts/work/folders/delete')).toEqual([
      { folder: 'Projects', messages: 3 },
      { folder: 'Projects', messages: 4 },
    ])
  })

  it('lists the rule sets that keep a folder from being deleted', async () => {
    stubMailApi(
      state({
        refuse: {
          'POST /api/v1/mail/accounts/work/folders/delete': {
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
})

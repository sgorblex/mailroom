import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { bodiesTo, type FakeMailState, HOME, messageItem, messageView, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const inbox = Array.from({ length: 3 }, (_, index) => messageItem(WORK, 'INBOX', 10 - index))
const [first] = inbox

function state(): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 3 },
        { path: 'Archive', role: 'archive', unread: 0, total: 0 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
        { path: 'Projects/2025', role: null, unread: 0, total: 0 },
        { path: 'Lists', role: null, unread: 0, total: 0, selectable: false },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 1 }],
    },
    messages: {
      '1/INBOX': inbox,
      '1/Projects/2025': [messageItem(WORK, 'Projects/2025', 1, { subject: 'Plans' })],
      '2/INBOX': [messageItem(HOME, 'INBOX', 1, { subject: 'From home' })],
    },
    views: Object.fromEntries(inbox.map((item) => [item.ref, messageView(item)])),
  }
}

const palette = (): HTMLElement => screen.getByRole('dialog', { name: 'Command palette' })
const options = (): string[] =>
  within(palette())
    .queryAllByRole('option')
    .map((option) => option.textContent)

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the row actions in the command palette', () => {
  const alice = {
    username: 'alice',
    is_admin: false,
    time_zone: null,
    mark_read_subfolders: true,
    index_max_age_days: null,
    conversations: true,
    undo_send_seconds: 10,
  }
  const APPEARANCE = '/api/v1/auth/me/appearance'

  /** The mail API, with the appearance saved as `answer` says. */
  function stub(answer: (change: object) => Response): ReturnType<typeof vi.fn> {
    const mail = stubMailApi(state())
    const saves = vi.fn()
    vi.stubGlobal('fetch', (path: string, init?: RequestInit) => {
      if (path !== APPEARANCE) {
        return mail(path, init) as Promise<Response>
      }
      const change = JSON.parse(init?.body as string) as object
      saves(change)
      return Promise.resolve(answer(change))
    })
    return saves
  }

  async function search(query: string): Promise<ReturnType<typeof userEvent.setup>> {
    const user = userEvent.setup()
    await screen.findByRole('list', { name: 'Messages' })
    await user.keyboard('{Control>}k{/Control}')
    await user.type(within(palette()).getByRole('combobox'), query)
    return user
  }

  it('shows and hides one, saying so, with nothing more to show once there are three', async () => {
    const saves = stub((change) => new Response(JSON.stringify({ ...alice, appearance: change }), { status: 200 }))
    renderMailApp('/mail', { user: alice })
    let user = await search('message rows trash')
    expect(options()).toEqual(['Show on message rows: Move to Trash'])
    await user.keyboard('{Enter}')
    expect(saves).toHaveBeenLastCalledWith({ row_actions: ['star', 'handled', 'trash'] })
    await waitFor(() => {
      expect(document.getElementById('toast-status')).toHaveTextContent('Message rows now show: Move to Trash.')
    })

    user = await search('message rows')
    expect(options().filter((name) => name.startsWith('Show on'))).toEqual([])
    await user.clear(within(palette()).getByRole('combobox'))
    await user.type(within(palette()).getByRole('combobox'), 'hide message rows star')
    await user.keyboard('{Enter}')
    expect(saves).toHaveBeenLastCalledWith({ row_actions: ['handled', 'trash'] })
    await waitFor(() => {
      expect(document.getElementById('toast-status')).toHaveTextContent('Message rows no longer show: Star.')
    })
  })

  it('says when it could not be saved', async () => {
    stub(() => new Response('{"detail":"Server error"}', { status: 500 }))
    renderMailApp('/mail', { user: alice })
    const user = await search('message rows trash')
    await user.keyboard('{Enter}')
    await waitFor(() => {
      expect(screen.getAllByRole('alert').map((alert) => alert.textContent)).toContain('Server error')
    })
  })

  it('are not offered signed out', async () => {
    stubMailApi(state())
    renderMailApp('/mail')
    await search('message rows')
    expect(options()).toEqual([])
  })
})

describe('the command palette', () => {
  it('opens on Ctrl+K and goes to a folder of any account, by the keyboard', async () => {
    const user = userEvent.setup()
    stubMailApi(state())
    const history = renderMailApp('/mail')
    await screen.findByRole('list', { name: 'Messages' })

    await user.keyboard('{Control>}k{/Control}')
    const input = within(palette()).getByRole('combobox', { name: 'Go to or run' })
    expect(input).toHaveFocus()
    expect(input).toHaveAttribute('aria-expanded', 'true')
    // Not offered for autofill by password managers.
    expect(input).toHaveAttribute('type', 'search')
    expect(input).toHaveAttribute('autocomplete', 'off')
    for (const optOut of ['data-1p-ignore', 'data-bwignore']) {
      expect(input).toHaveAttribute(optOut)
    }
    expect(input).toHaveAttribute('data-lpignore', 'true')
    expect(input).toHaveAttribute('data-form-type', 'other')
    // Every account's folders, \Noselect ones left out, and the pages.
    await waitFor(() => {
      expect(options()).toContain('Inboxhome')
    })
    expect(options()).toContain('Projects/2025work')
    expect(options()).not.toContain('Listswork')
    expect(options()).toContain('Accounts')

    await user.type(input, 'proj 25')
    expect(options()).toEqual(['Projects/2025work'])
    const [only] = within(palette()).getAllByRole('option')
    expect(only).toHaveAttribute('aria-selected', 'true')
    expect(input).toHaveAttribute('aria-activedescendant', only?.id)
    await user.keyboard('{Enter}')

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(await screen.findByText('Plans')).toBeInTheDocument()
    expect(history.location.search).toContain('Projects')
  })

  it('moves through the options with the arrows, and says when nothing matches', async () => {
    const user = userEvent.setup()
    stubMailApi(state())
    renderMailApp('/mail')
    await screen.findByRole('list', { name: 'Messages' })

    await user.keyboard('{Meta>}k{/Meta}')
    const input = within(palette()).getByRole('combobox')
    await user.type(input, 'inbox')
    await waitFor(() => {
      expect(options()).toEqual(['Inboxwork', 'Inboxhome'])
    })
    await user.keyboard('{ArrowDown}')
    expect(within(palette()).getByRole('option', { selected: true })).toHaveTextContent('Inboxhome')
    await user.keyboard('{ArrowDown}')
    expect(within(palette()).getByRole('option', { selected: true })).toHaveTextContent('Inboxwork')
    await user.keyboard('{ArrowUp}')
    expect(within(palette()).getByRole('option', { selected: true })).toHaveTextContent('Inboxhome')

    await user.type(input, 'zzz')
    expect(within(palette()).getByRole('status')).toHaveTextContent('Nothing matches.')
    expect(input).toHaveAttribute('aria-expanded', 'false')
    // Ctrl+K again closes it.
    await user.keyboard('{Control>}k{/Control}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('jumps to the first and the last option by page, and closes on Escape', async () => {
    const user = userEvent.setup()
    stubMailApi(state())
    renderMailApp('/mail')
    await screen.findByRole('list', { name: 'Messages' })

    await user.keyboard('{Control>}k{/Control}')
    const input = within(palette()).getByRole('combobox')
    await user.type(input, 'inbox')
    await waitFor(() => {
      expect(options()).toEqual(['Inboxwork', 'Inboxhome'])
    })
    await user.keyboard('{PageDown}')
    expect(within(palette()).getByRole('option', { selected: true })).toHaveTextContent('Inboxhome')
    await user.keyboard('{PageUp}')
    expect(within(palette()).getByRole('option', { selected: true })).toHaveTextContent('Inboxwork')

    // Home and End move the caret in the query, not the highlighted option.
    await user.keyboard('{End}')
    expect(within(palette()).getByRole('option', { selected: true })).toHaveTextContent('Inboxwork')
    await user.keyboard('{PageDown}{Home}')
    expect(within(palette()).getByRole('option', { selected: true })).toHaveTextContent('Inboxhome')
    expect(input).toHaveProperty('selectionStart', 0)

    // With a query typed, Escape closes the palette rather than clearing it.
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('takes the wide dialog, its content setting no width of its own', async () => {
    const user = userEvent.setup()
    stubMailApi(state())
    renderMailApp('/mail')
    await screen.findByRole('list', { name: 'Messages' })

    await user.keyboard('{Control>}k{/Control}')
    expect(palette()).toHaveAttribute('data-size', 'lg')
    const content = within(palette()).getByRole('combobox').parentElement
    expect(content).toHaveClass('w-full')
    expect(content?.className).not.toMatch(/\bw-\[/)
  })

  it("runs the registry's actions on the open message, and lists the recent choices first", async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(state())
    renderMailApp(`/mail?message=${first?.ref ?? ''}`)
    await screen.findByRole('heading', { name: 'Message 10' })

    await user.keyboard('{Control>}k{/Control}')
    const input = within(palette()).getByRole('combobox')
    await user.type(input, 'archive')
    // The action, with its key, and the folder of that name.
    expect(options()).toEqual(['Archivee', 'Archivework'])
    await user.keyboard('{Enter}')

    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/messages/archive')).toEqual([{ refs: [first?.ref], source_folder: 'INBOX' }])
    })

    await user.keyboard('{Control>}k{/Control}')
    await user.type(within(palette()).getByRole('combobox'), 'home')
    await user.click(within(palette()).getByRole('option', { name: 'home' }))
    await screen.findByText('From home')

    await user.keyboard('{Control>}k{/Control}')
    expect(within(within(palette()).getByRole('group', { name: 'Recent' })).getAllByRole('option')[0]).toHaveTextContent('home')
  })

  it('is listed in the shortcut sheet, and does not open while typing in the composer, where Ctrl+K is a link', async () => {
    const user = userEvent.setup()
    stubMailApi(state())
    renderMailApp('/mail')
    await screen.findByRole('list', { name: 'Messages' })

    await user.keyboard('?')
    const sheet = screen.getByRole('dialog', { name: 'Keyboard shortcuts' })
    expect(within(sheet).getByText('Open the command palette').previousSibling).toHaveTextContent('Ctrl/⌘ K')
    // Not over another dialog.
    await user.keyboard('{Control>}k{/Control}')
    expect(screen.queryByRole('dialog', { name: 'Command palette' })).not.toBeInTheDocument()
    await user.click(within(sheet).getByRole('button', { name: 'Close' }))

    await user.click(screen.getByRole('button', { name: 'Compose' }))
    const form = await screen.findByRole('form', { name: 'New message' })
    // The rich-text editor loads on its own, the first time at some length.
    await user.click(await within(form).findByRole('textbox', { name: 'Message' }, { timeout: 10_000 }))
    await user.keyboard('{Control>}k{/Control}')
    // There it is the editor's own: a link.
    expect(await screen.findByRole('dialog', { name: 'Link' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Command palette' })).not.toBeInTheDocument()
  })
})

import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { LG_QUERY } from '@/lib/media-query.ts'
import { expectNoAxeViolations } from '@/test/axe.ts'
import { fakeDraft, type FakeMailState, HOME, messageItem, messageView, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const inbox = Array.from({ length: 3 }, (_, index) => messageItem(WORK, 'INBOX', 10 - index))
const opened = inbox[1] ?? messageItem(WORK, 'INBOX', 0)

function state(fields: Partial<FakeMailState> = {}): FakeMailState {
  return {
    ...fields,
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 3 },
        { path: 'Sent', role: 'sent', unread: 0, total: 0 },
        { path: 'Drafts', role: 'drafts', unread: 0, total: 0 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 0 }],
    },
    messages: { '1/INBOX': inbox, '1/Sent': [], '1/Drafts': [], '2/INBOX': [] },
    views: { [opened.ref]: messageView(opened) },
  }
}

/** A phone's window: narrower than `lg`. */
beforeEach(() => {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query !== LG_QUERY && !query.includes('min-width'),
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

const foldersButton = (): HTMLElement => screen.getByRole('button', { name: 'Folders' })
const drawer = (): HTMLElement | null => screen.queryByRole('dialog', { name: 'Folders' })
const header = async (): Promise<HTMLElement> => (await screen.findByRole('button', { name: 'Folders' })).closest('header') ?? document.body

describe('Mail on a phone', () => {
  it('passes axe on the list, the folders drawer and a message', async () => {
    stubMailApi(state())
    renderMailApp('/mail')
    await header()
    const list = await screen.findByRole('list', { name: 'Messages' })
    await expectNoAxeViolations()

    await userEvent.click(foldersButton())
    const open = drawer()
    expect(open).not.toBeNull()
    await within(open ?? document.body).findByRole('treeitem', { name: 'Sent' })
    await expectNoAxeViolations()
    fireEvent(open ?? document.body, new Event('cancel', { cancelable: true }))
    await waitFor(() => {
      expect(drawer()).toBeNull()
    })

    await userEvent.click(within(list).getByRole('link', { name: /Message 9/ }))
    await screen.findByRole('heading', { name: 'Message 9' })
    await expectNoAxeViolations()
  })

  it('has a header naming the folder, with the folders, search and compose buttons, and no folder pane', async () => {
    stubMailApi(state())
    renderMailApp('/mail')
    const top = await header()

    expect(await within(top).findByRole('heading', { name: 'Inbox' })).toBeInTheDocument()
    expect(within(top).getByRole('button', { name: 'Folders' })).toHaveAttribute('aria-expanded', 'false')
    expect(within(top).getByRole('button', { name: 'Search mail' })).toBeInTheDocument()
    expect(within(top).getByRole('button', { name: 'Compose' })).toBeInTheDocument()
    // The folder pane is in the drawer only, and Compose in the header only.
    expect(screen.queryByRole('navigation', { name: 'Accounts and folders' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Compose' })).toHaveLength(1)
    expect(await screen.findByRole('list', { name: 'Messages' })).toBeInTheDocument()
  })

  it('opens the folders drawer as a modal, closed by Escape or the scrim, focus going back', async () => {
    stubMailApi(state())
    renderMailApp('/mail')
    await header()

    await userEvent.click(foldersButton())
    const shown = drawer()
    expect(shown).toHaveAttribute('open')
    expect(foldersButton()).toHaveAttribute('aria-expanded', 'true')
    // Focus is taken into it, and its content is the folder pane: the account switcher, views and tree.
    expect(shown).toContainElement(document.activeElement as HTMLElement)
    const pane = within(shown ?? document.body).getByRole('navigation', { name: 'Accounts and folders' })
    expect(within(pane).getByRole('button', { name: /Switch account/ })).toBeInTheDocument()
    expect(await within(pane).findByRole('treeitem', { name: 'Sent' })).toBeInTheDocument()

    // Escape: the browser's cancel.
    fireEvent(shown ?? document.body, new Event('cancel', { cancelable: true }))
    expect(drawer()).not.toBeInTheDocument()
    expect(foldersButton()).toHaveFocus()

    await userEvent.click(foldersButton())
    await userEvent.click(drawer() ?? document.body)
    expect(drawer()).not.toBeInTheDocument()
    expect(foldersButton()).toHaveFocus()

    await userEvent.click(foldersButton())
    await userEvent.click(within(drawer() ?? document.body).getByRole('button', { name: 'Close the folders' }))
    expect(drawer()).not.toBeInTheDocument()
  })

  it('gives the focus back to Folders once the drawer is gone, the page behind out of reach till then', async () => {
    stubMailApi(state())
    renderMailApp('/mail')
    await header()
    await userEvent.click(foldersButton())
    const button = foldersButton()
    const focus = button.focus.bind(button)
    // Whether a modal was still open each time Folders took the focus: a browser would refuse it then.
    const drawerOpen: boolean[] = []
    button.focus = (options?: FocusOptions) => {
      drawerOpen.push(document.querySelector('dialog[open]') !== null)
      focus(options)
    }

    fireEvent(drawer() ?? document.body, new Event('cancel', { cancelable: true }))

    expect(drawer()).not.toBeInTheDocument()
    expect(drawerOpen).toEqual([false])
    expect(button).toHaveFocus()
  })

  it('closes the drawer once a folder is picked, the header naming it', async () => {
    stubMailApi(state())
    renderMailApp('/mail')
    const top = await header()

    await userEvent.click(foldersButton())
    await userEvent.click(await within(drawer() ?? document.body).findByRole('treeitem', { name: 'Sent' }))
    expect(drawer()).not.toBeInTheDocument()
    expect(await within(top).findByRole('heading', { name: 'Sent' })).toBeInTheDocument()
  })

  it('shows the search form on request and types into it', async () => {
    stubMailApi(state())
    renderMailApp('/mail')
    const button = within(await header()).getByRole('button', { name: 'Search mail' })
    const form = screen.getByRole('search', { name: 'Search mail' })
    const region = document.getElementById(button.getAttribute('aria-controls') ?? '')
    expect(region).toContainElement(form)
    expect(region).toHaveClass('hidden')

    await userEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(region).not.toHaveClass('hidden')
    expect(within(form).getByRole('searchbox', { name: 'Search terms' })).toHaveFocus()

    await userEvent.click(button)
    expect(region).toHaveClass('hidden')
  })

  it('starts a message from the header, the composer taking the screen', async () => {
    stubMailApi(state())
    renderMailApp('/mail')
    const top = await header()

    await userEvent.click(within(top).getByRole('button', { name: 'Compose' }))
    expect(await screen.findByRole('form', { name: 'New message' })).toBeInTheDocument()
    expect(top).not.toBeVisible()
  })

  it('opens a message in full view with Back, its action bar fixed at the foot', async () => {
    stubMailApi(state())
    renderMailApp('/mail')
    const top = await header()
    const list = await screen.findByRole('list', { name: 'Messages' })
    await userEvent.click(within(list).getByRole('link', { name: /Message 9/ }))
    expect(await screen.findByRole('heading', { name: 'Message 9' })).toBeInTheDocument()

    expect(top).not.toBeVisible()
    const bar = screen.getByRole('group', { name: 'Message actions' })
    expect(bar.parentElement).toHaveClass('fixed', 'bottom-(--tab-bar)')
    // Icons on a phone, their names kept.
    expect(within(bar).getByRole('button', { name: 'Reply' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('link', { name: 'Back to the list' }))
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Message 9' })).not.toBeInTheDocument()
    })
    expect(top).toBeVisible()
  })

  it('opens the account switcher menu within the drawer, out of reach of the page behind', async () => {
    stubMailApi(state())
    renderMailApp('/mail')
    await header()
    await userEvent.click(foldersButton())
    const shown = drawer() ?? document.body

    await userEvent.click(within(shown).getByRole('button', { name: /Switch account/ }))
    const menu = screen.getByRole('menu', { name: 'Accounts' })
    expect(shown).toContainElement(menu)
    await userEvent.click(within(menu).getByRole('menuitemcheckbox', { name: 'home' }))
    await waitFor(() => {
      expect(within(shown).getByRole('tree', { name: 'Folders of home' })).toBeInTheDocument()
    })
  })

  it('keeps the drawer open when Escape closes a dialog opened from it', async () => {
    stubMailApi(state())
    renderMailApp('/mail')
    await header()
    await userEvent.click(foldersButton())
    await userEvent.click(within(drawer() ?? document.body).getByRole('button', { name: /^Scheduled/ }))
    const list = await screen.findByRole('dialog', { name: 'Scheduled' })

    fireEvent(list, new Event('cancel', { cancelable: true }))
    expect(screen.queryByRole('dialog', { name: 'Scheduled' })).not.toBeInTheDocument()
    expect(drawer()).toHaveAttribute('open')
  })

  it('closes the drawer when Edit in its Scheduled list opens the composer', async () => {
    const draft = fakeDraft(WORK, 90, { subject: 'Later', text: 'Held' })
    const fake = state({
      drafts: [draft],
      scheduled: {
        '9': {
          scheduled_id: '9',
          account: WORK,
          ref: draft.ref,
          subject: 'Later',
          recipients: ['ada@example.org'],
          send_at: new Date(Date.now() + 86_400_000).toISOString(),
          remaining_ms: 86_400_000,
          state: 'held',
          code: null,
          by: 'user',
          app: null,
          polls: 0,
        },
      },
    })
    stubMailApi(fake)
    renderMailApp('/mail')
    await header()
    await userEvent.click(foldersButton())
    await userEvent.click(await within(drawer() ?? document.body).findByRole('button', { name: /^Scheduled.*1 scheduled/ }))
    const list = await screen.findByRole('dialog', { name: 'Scheduled' })

    await userEvent.click(within(list).getByRole('button', { name: 'Edit “Later”' }))

    expect(await screen.findByRole('form', { name: 'Draft' })).toBeInTheDocument()
    expect(drawer()).not.toBeInTheDocument()
  })

  it('offers Back to draft above a message opened in front of the composer', async () => {
    stubMailApi(state())
    const history = renderMailApp('/mail')
    const top = await header()
    await userEvent.click(within(top).getByRole('button', { name: 'Compose' }))
    await screen.findByRole('form', { name: 'New message' })

    const { pathname, search } = history.location
    history.push(`${pathname}${search === '' ? '?' : `${search}&`}message=${encodeURIComponent(opened.ref)}`)
    expect(await screen.findByRole('heading', { name: 'Message 9' })).toBeInTheDocument()
    expect(top).not.toBeVisible()

    const back = screen.getByRole('button', { name: 'Back to draft' })
    // Not within what the narrow screen hides (jsdom applies no Tailwind class).
    expect(back.closest('.hidden')).toBeNull()
    await userEvent.click(back)
    expect(await screen.findByRole('form', { name: 'New message' })).toBeInTheDocument()
  })

  it('puts the cursor in the search shown when Search mail is pressed', async () => {
    stubMailApi(state())
    renderMailApp('/mail?q=%7B%22text%22%3A%22hello%22%7D')
    const button = within(await header()).getByRole('button', { name: 'Search mail' })
    expect(button).toHaveAttribute('aria-expanded', 'true')

    await userEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('searchbox', { name: 'Search terms' })).toHaveFocus()
  })

  it('keeps the docked action bar to the screen, the actions that do not fit going to More', async () => {
    // jsdom lays nothing out: every button 40 px wide, the bar 280 px, its observer fired on observing.
    vi.stubGlobal(
      'ResizeObserver',
      class {
        private readonly fit: () => void
        constructor(fit: () => void) {
          this.fit = fit
        }
        observe(): void {
          this.fit()
        }
        disconnect(): void {
          // Nothing to stop: it never fires again.
        }
      },
    )
    const offsetWidth = vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(40)
    const clientWidth = vi.spyOn(Element.prototype, 'clientWidth', 'get').mockImplementation(function (this: Element) {
      return this.getAttribute('aria-label') === 'Message actions' ? 280 : 0
    })
    try {
      stubMailApi(state())
      renderMailApp(`/mail?message=${encodeURIComponent(opened.ref)}`)
      const bar = await screen.findByRole('group', { name: 'Message actions' })
      expect(bar.parentElement).toHaveClass('fixed')
      expect(bar).not.toHaveClass('overflow-x-auto')

      await waitFor(() => {
        expect(within(bar).getAllByRole('button').map((button) => button.getAttribute('aria-label') ?? button.textContent)).toEqual([
          'Reply',
          'Forward',
          'Move to Trash',
          'Star',
          'Mark as unread',
          'More actions',
        ])
      })
      await userEvent.click(within(bar).getByRole('button', { name: 'More actions' }))
      const menu = screen.getByRole('menu', { name: 'More actions' })
      expect(within(menu).getByRole('menuitem', { name: /Reply all/ })).toBeInTheDocument()
      expect(within(menu).getByRole('menuitem', { name: /Mark To sort/ })).toBeInTheDocument()
    } finally {
      offsetWidth.mockRestore()
      clientWidth.mockRestore()
    }
  })

  it('leaves the docked action bar height free for the toasts', async () => {
    stubMailApi(state())
    renderMailApp(`/mail?message=${encodeURIComponent(opened.ref)}`)
    await screen.findByRole('heading', { name: 'Message 9' })
    expect(document.documentElement.style.getPropertyValue('--action-bar')).toMatch(/px$/)
  })
})

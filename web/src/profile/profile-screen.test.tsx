import { act, fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { expectNoAxeViolations } from '@/test/axe.ts'
import { renderSignedIn } from '@/test/signed-in.tsx'

import { ProfileScreen } from './profile-screen.tsx'

const ALICE = { username: 'alice', is_admin: false, time_zone: null, mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }
const CURRENT = 'correct horse battery staple'
const NEXT = 'a brand new passphrase'

const PASSWORD_PATH = '/api/v1/auth/password'
const LIMITS_PATH = '/api/v1/me/limits'
const REMOTE_SENDERS_PATH = '/api/v1/mail/remote-senders'
const SESSIONS_PATH = '/api/v1/auth/sessions'
const TEXT_SENDERS_PATH = '/api/v1/mail/sender-views'

/**
 * Anything but the password change: the default limits, no connected apps (the MCP server off), no API tokens to make,
 * this browser's session alone, no remote content senders, no accounts to index or notify on, and nothing else found (the linked sign-ins section's requests).
 */
function otherSections(path: string): Promise<Response> {
  if (path === LIMITS_PATH) {
    const limits = { send_per_hour: 20, max_recipients: 10, body_chars: 6000, attachment_max_mb: 10, move_max_mb: 25 }
    return Promise.resolve(new Response(JSON.stringify(limits), { status: 200 }))
  }
  if (path === REMOTE_SENDERS_PATH || path === TEXT_SENDERS_PATH) {
    return Promise.resolve(new Response('{"senders":[]}', { status: 200 }))
  }
  if (path === SESSIONS_PATH) {
    const session = {
      id: 1, current: true, device: 'Firefox · Linux', ip: null,
      created_at: '2026-11-20T10:00:00Z', last_seen_at: '2026-11-24T10:00:00Z', expires_at: '2026-12-04T10:00:00Z',
    }
    return Promise.resolve(new Response(JSON.stringify([session]), { status: 200 }))
  }
  if (path === '/api/v1/accounts') {
    return Promise.resolve(new Response('[]', { status: 200 }))
  }
  if (path === '/api/v1/mail/notifications') {
    return Promise.resolve(new Response('{"accounts":[]}', { status: 200 }))
  }
  if (path === '/api/v1/oauth/grants') {
    return Promise.resolve(new Response('{"auth":"off","grants":[],"groups":[]}', { status: 200 }))
  }
  if (path === '/api/v1/me/api-tokens') {
    return Promise.resolve(new Response('{"tokens":[],"groups":[],"accounts":[]}', { status: 200 }))
  }
  return Promise.resolve(new Response('{"detail":"Not Found"}', { status: 404 }))
}

function stubApi(): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    if (path !== PASSWORD_PATH) {
      return otherSections(path)
    }
    const { current_password } = JSON.parse(init?.body as string) as { current_password: string }
    if (current_password !== CURRENT) {
      return Promise.resolve(
        new Response('{"detail":{"code":"wrong_password","message":"Wrong password"}}', {
          status: 400,
        }),
      )
    }
    return Promise.resolve(new Response(null, { status: 204 }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function fill(current: string, next: string, repeated: string): Promise<void> {
  for (const [label, value] of [
    ['Current password', current],
    ['New password', next],
    ['New password again', repeated],
  ] as const) {
    const field = await screen.findByLabelText(label)
    await userEvent.clear(field)
    await userEvent.type(field, value)
  }
  await userEvent.click(screen.getByRole('button', { name: 'Change password' }))
}

/** jsdom has no `scrollIntoView`; the nav tests set one and this puts back what was there. */
const ORIGINAL_SCROLL = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollIntoView')

/** jsdom has no `IntersectionObserver`: this one is driven by hand, as a scroll would. */
class FakeObserver {
  static last: FakeObserver | undefined
  readonly targets = new Map<string, Element>()
  private readonly callback: IntersectionObserverCallback
  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback
    FakeObserver.last = this
  }
  observe(element: Element): void {
    this.targets.set(element.id, element)
  }
  unobserve(element: Element): void {
    this.targets.delete(element.id)
  }
  disconnect(): void {
    this.targets.clear()
  }
  takeRecords(): IntersectionObserverEntry[] {
    return []
  }
  /** The groups `into` the top third of the window and those `out` of it. */
  cross(into: string[], out: string[] = []): void {
    const entries = [
      ...into.map((id) => ({ target: this.targets.get(id), isIntersecting: true })),
      ...out.map((id) => ({ target: this.targets.get(id), isIntersecting: false })),
    ] as unknown as IntersectionObserverEntry[]
    act(() => {
      this.callback(entries, this as unknown as IntersectionObserver)
    })
  }
}

function observer(): FakeObserver {
  if (FakeObserver.last === undefined) {
    throw new Error('no IntersectionObserver was made')
  }
  return FakeObserver.last
}

/** The page scrolled to its end: 1000px down a page 768px taller than jsdom's window. */
function scrollToEnd(): void {
  Object.defineProperty(window, 'scrollY', { configurable: true, value: 1000 })
  Object.defineProperty(document.documentElement, 'scrollHeight', { configurable: true, value: 1000 + window.innerHeight })
}

const ORIGINAL_SCROLL_Y = Object.getOwnPropertyDescriptor(window, 'scrollY')

/** The Apps group's cards that render something, and its empty-group line. */
function appsGroup(): { filled: number; empty: HTMLElement } {
  const region = screen.getByRole('region', { name: 'Apps' })
  return {
    filled: region.querySelectorAll('.profile-card:not(:empty)').length,
    empty: within(region).getByText('Nothing to set here on this server.'),
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  if (ORIGINAL_SCROLL === undefined) {
    Reflect.deleteProperty(Element.prototype, 'scrollIntoView')
  } else {
    Object.defineProperty(Element.prototype, 'scrollIntoView', ORIGINAL_SCROLL)
  }
  window.history.replaceState(null, '', '/')
  FakeObserver.last = undefined
  if (ORIGINAL_SCROLL_Y === undefined) {
    Reflect.deleteProperty(window, 'scrollY')
  } else {
    Object.defineProperty(window, 'scrollY', ORIGINAL_SCROLL_Y)
  }
  Reflect.deleteProperty(document.documentElement, 'scrollHeight')
})

describe('ProfileScreen', () => {
  it('passes axe', async () => {
    stubApi()
    renderSignedIn(<ProfileScreen />, ALICE)
    await screen.findByLabelText('Current password')
    await expectNoAxeViolations()
  })

  it('hides the password change while passwords are off', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        path === '/api/v1/auth/oidc/config'
          ? Promise.resolve(
              new Response('{"enabled":true,"name":"Authentik","password_sign_in":false}'),
            )
          : otherSections(path),
      ),
    )
    renderSignedIn(<ProfileScreen />, ALICE)
    expect(await screen.findByRole('link', { name: 'Link Authentik' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Current password')).not.toBeInTheDocument()
  })

  it('says when passwords were turned off meanwhile', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        path === PASSWORD_PATH
          ? Promise.resolve(
              new Response('{"detail":{"code":"password_sign_in_off","message":"Off"}}', {
                status: 403,
              }),
            )
          : otherSections(path),
      ),
    )
    renderSignedIn(<ProfileScreen />, ALICE)
    await fill(CURRENT, NEXT, NEXT)
    expect(await screen.findByRole('alert')).toHaveTextContent('Passwords are off here')
  })

  it('asks to wait after too many wrong passwords', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        path === PASSWORD_PATH
          ? Promise.resolve(new Response('{"detail":"Too many failed attempts"}', { status: 429 }))
          : otherSections(path),
      ),
    )
    renderSignedIn(<ProfileScreen />, ALICE)
    await fill(CURRENT, NEXT, NEXT)
    expect(await screen.findByRole('alert')).toHaveTextContent('Too many failed attempts.')
  })

  it('refuses new passwords that differ without asking the API', async () => {
    const fetchMock = stubApi()
    renderSignedIn(<ProfileScreen />, ALICE)
    await fill(CURRENT, NEXT, `${NEXT}!`)
    expect(screen.getByRole('alert')).toHaveTextContent('The new passwords differ.')
    expect(fetchMock).not.toHaveBeenCalledWith(PASSWORD_PATH, expect.anything())
  })

  it('says when the current password is wrong, then changes it', async () => {
    const fetchMock = stubApi()
    renderSignedIn(<ProfileScreen />, ALICE)
    await fill('not my password', NEXT, NEXT)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Your current password is wrong.',
    )
    expect(screen.getByLabelText('Current password')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('New password again')).toHaveAttribute('aria-invalid', 'false')

    await fill(CURRENT, NEXT, NEXT)
    expect(await screen.findByRole('status')).toHaveTextContent('Password changed.')
    expect(screen.getByLabelText('Current password')).toHaveValue('')
    expect(fetchMock).toHaveBeenCalledWith(
      PASSWORD_PATH,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          current_password: CURRENT,
          new_password: NEXT,
          revoke_access: false,
        }),
      }),
    )
    // The other sessions were signed out: list what is left.
    await vi.waitFor(() => {
      expect(fetchMock.mock.calls.filter(([path]) => path === SESSIONS_PATH)).toHaveLength(2)
    })
  })

  it('revokes API tokens and connected apps too when asked, and lists them again', async () => {
    const fetchMock = stubApi()
    renderSignedIn(<ProfileScreen />, ALICE)
    const listed = (): number =>
      fetchMock.mock.calls.filter(([path]) => path === '/api/v1/me/api-tokens').length
    await vi.waitFor(() => {
      expect(listed()).toBe(1)
    })
    const revoke = await screen.findByRole('checkbox', {
      name: 'Revoke API tokens and connected apps too',
    })
    expect(revoke).not.toBeChecked()
    await userEvent.click(revoke)
    await fill(CURRENT, NEXT, NEXT)
    expect(await screen.findByRole('status')).toHaveTextContent('Password changed.')
    expect(fetchMock).toHaveBeenCalledWith(
      PASSWORD_PATH,
      expect.objectContaining({
        body: JSON.stringify({
          current_password: CURRENT,
          new_password: NEXT,
          revoke_access: true,
        }),
      }),
    )
    expect(revoke).not.toBeChecked()
    await vi.waitFor(() => {
      expect(listed()).toBe(2)
    })
  })

  it.each([
    { features: ['mcp' as const], asked: true },
    { features: ['mail' as const, 'filters' as const], asked: false },
  ])('asks for connected apps only with mcp ($features)', async ({ features, asked }) => {
    const fetchMock = stubApi()
    renderSignedIn(<ProfileScreen />, ALICE, features)
    expect(await screen.findByRole('heading', { name: 'Change your password' })).toBeInTheDocument()
    await new Promise((resolve) => setTimeout(resolve, 0))
    const grants = fetchMock.mock.calls.some(([path]) => path === '/api/v1/oauth/grants')
    expect(grants).toBe(asked)
  })

  it.each([
    { features: ['mail' as const], shown: true },
    { features: ['mcp' as const], shown: true },
    { features: ['filters' as const], shown: false },
  ])('shows the mail limits only where the instance sends ($features)', async ({ features, shown }) => {
    const fetchMock = stubApi()
    renderSignedIn(<ProfileScreen />, ALICE, features)
    expect(await screen.findByRole('heading', { name: 'Change your password' })).toBeInTheDocument()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.queryByRole('heading', { name: 'Mail limits' }) !== null).toBe(shown)
    expect(fetchMock.mock.calls.some(([path]) => path === LIMITS_PATH)).toBe(shown)
  })

  it.each([
    { features: ['mail' as const], shown: true },
    { features: ['mcp' as const], shown: false },
  ])('lists remembered senders only with the mail client ($features)', async ({ features, shown }) => {
    const fetchMock = stubApi()
    renderSignedIn(<ProfileScreen />, ALICE, features)
    expect(await screen.findByRole('heading', { name: 'Change your password' })).toBeInTheDocument()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(screen.queryByRole('heading', { name: 'Remote images' }) !== null).toBe(shown)
    expect(screen.queryByRole('heading', { name: 'Read as text' }) !== null).toBe(shown)
    expect(fetchMock.mock.calls.some(([path]) => path === REMOTE_SENDERS_PATH)).toBe(shown)
    expect(fetchMock.mock.calls.some(([path]) => path === TEXT_SENDERS_PATH)).toBe(shown)
  })

  it.each([
    { features: ['mail' as const, 'mcp' as const], groups: ['Account', 'Appearance', 'Mail', 'Sending', 'Apps'] },
    { features: ['mcp' as const], groups: ['Account', 'Appearance', 'Sending', 'Apps'] },
    { features: ['filters' as const], groups: ['Account', 'Appearance', 'Apps'] },
  ])('lists the groups the instance offers in the section nav ($features)', async ({ features, groups }) => {
    stubApi()
    renderSignedIn(<ProfileScreen />, ALICE, features)
    const nav = screen.getByRole('navigation', { name: 'Profile sections' })
    const links = within(nav).getAllByRole('link')
    expect(links.map((link) => link.textContent)).toEqual(groups)
    expect(links[0]).toHaveAttribute('aria-current', 'location')
    expect(links[0]).toHaveAttribute('href', '#account')
    for (const group of groups) {
      expect(screen.getByRole('region', { name: group })).toBeInTheDocument()
    }
    expect(await screen.findByRole('heading', { name: 'Change your password' })).toBeInTheDocument()
  })

  it('scrolls to a group from the nav and keeps it in the link', async () => {
    stubApi()
    const scrolled = vi.fn()
    Element.prototype.scrollIntoView = scrolled
    renderSignedIn(<ProfileScreen />, ALICE, ['mail'])
    const nav = screen.getByRole('navigation', { name: 'Profile sections' })
    await userEvent.click(within(nav).getByRole('link', { name: 'Sending' }))
    expect(within(nav).getByRole('link', { name: 'Sending' })).toHaveAttribute('aria-current', 'location')
    expect(within(nav).getByRole('link', { name: 'Account' })).not.toHaveAttribute('aria-current')
    expect(window.location.hash).toBe('#sending')
    expect(scrolled).toHaveBeenCalled()
    expect(screen.getByRole('region', { name: 'Sending' })).toHaveFocus()
  })

  it('opens on the group a link names', () => {
    stubApi()
    const scrolled = vi.fn()
    Element.prototype.scrollIntoView = scrolled
    window.history.replaceState(null, '', '/profile#mail')
    renderSignedIn(<ProfileScreen />, ALICE, ['mail'])
    const nav = screen.getByRole('navigation', { name: 'Profile sections' })
    expect(within(nav).getByRole('link', { name: 'Mail' })).toHaveAttribute('aria-current', 'location')
    expect(scrolled).toHaveBeenCalledTimes(1)
  })

  it('marks the group crossing the top of the window, and holds a clicked one until its scroll ends', async () => {
    stubApi()
    vi.stubGlobal('IntersectionObserver', FakeObserver)
    Element.prototype.scrollIntoView = vi.fn()
    renderSignedIn(<ProfileScreen />, ALICE, ['mail', 'mcp'])
    const nav = screen.getByRole('navigation', { name: 'Profile sections' })
    const marked = (): string | null | undefined =>
      within(nav)
        .getAllByRole('link')
        .find((link) => link.getAttribute('aria-current') === 'location')?.textContent

    observer().cross(['account', 'appearance'])
    expect(marked()).toBe('Account')
    observer().cross(['mail'], ['account', 'appearance'])
    expect(marked()).toBe('Mail')

    await userEvent.click(within(nav).getByRole('link', { name: 'Apps' }))
    // The groups passed on the way there do not take the mark from the one clicked.
    observer().cross(['sending'], ['mail'])
    expect(marked()).toBe('Apps')

    act(() => {
      fireEvent(window, new Event('scrollend'))
    })
    observer().cross([], [])
    expect(marked()).toBe('Sending')
  })

  it('marks the last group once the page is scrolled to its end', () => {
    stubApi()
    vi.stubGlobal('IntersectionObserver', FakeObserver)
    Element.prototype.scrollIntoView = vi.fn()
    renderSignedIn(<ProfileScreen />, ALICE, ['mail', 'mcp'])
    scrollToEnd()
    observer().cross(['sending'])
    const nav = screen.getByRole('navigation', { name: 'Profile sections' })
    expect(within(nav).getByRole('link', { name: 'Apps' })).toHaveAttribute('aria-current', 'location')
  })

  it('says a group has nothing to set only once its sections have loaded with nothing', async () => {
    stubApi()
    renderSignedIn(<ProfileScreen />, ALICE, ['mcp'])
    // Both are still asking: their cards are not empty, so neither is the group.
    expect(screen.getByText('Loading your connected apps…')).toBeInTheDocument()
    expect(screen.getByText('Loading your API tokens…')).toBeInTheDocument()
    expect(appsGroup().filled).toBe(2)
    expect(appsGroup().empty).toBeInTheDocument()
    await vi.waitFor(() => {
      expect(screen.queryByText('Loading your connected apps…')).not.toBeInTheDocument()
      expect(screen.queryByText('Loading your API tokens…')).not.toBeInTheDocument()
    })
    // The MCP server off and nothing connected: the cards render nothing and the line stands alone.
    expect(appsGroup().filled).toBe(0)
    expect(appsGroup().empty.matches('.profile-card:not(:empty) ~ p')).toBe(false)
  })

  it('keeps the empty-group line hidden behind a card that renders something', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        path === '/api/v1/oauth/grants'
          ? Promise.resolve(new Response('{"auth":"builtin","grants":[],"groups":[]}', { status: 200 }))
          : otherSections(path),
      ),
    )
    renderSignedIn(<ProfileScreen />, ALICE, ['mcp'])
    expect(await screen.findByText(/None yet/)).toBeInTheDocument()
    expect(appsGroup().filled).toBe(1)
    expect(appsGroup().empty.matches('.profile-card:not(:empty) ~ p')).toBe(true)
  })
})

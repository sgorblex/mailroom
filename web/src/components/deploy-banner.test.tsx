import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { bannerEnv } from '@/lib/deploy-env.ts'

import { DeployBanner } from './deploy-banner.tsx'

function serve(deployEnv: string): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({ version: '1.0.0', git_sha: 'abc', git_branch: 'main', deploy_env: deployEnv }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    ),
  )
}

function renderBanner(): void {
  render(<DeployBanner />)
}

// index.html's head, production's.
const PRODUCTION_HEAD = `
  <link rel="manifest" href="/manifest.webmanifest" />
  <link rel="icon" href="/icon.svg" type="image/svg+xml" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
  <meta name="apple-mobile-web-app-title" content="Mailroom" />`

/** The head's manifest, favicon, apple-touch-icon and home-screen title. */
function head(): string[] {
  return [
    document.querySelector('link[rel="manifest"]')?.getAttribute('href') ?? '',
    document.querySelector('link[rel="icon"]')?.getAttribute('href') ?? '',
    document.querySelector('link[rel="apple-touch-icon"]')?.getAttribute('href') ?? '',
    document.querySelector('meta[name="apple-mobile-web-app-title"]')?.getAttribute('content') ?? '',
  ]
}

const PRODUCTION = ['/manifest.webmanifest', '/icon.svg', '/apple-touch-icon.png', 'Mailroom']

let added: Element[] = []

beforeEach(() => {
  const before = new Set(document.head.children)
  document.head.insertAdjacentHTML('beforeend', PRODUCTION_HEAD)
  added = [...document.head.children].filter((element) => !before.has(element))
})

afterEach(() => {
  vi.unstubAllGlobals()
  document.title = 'Mailroom'
  for (const element of added) element.remove()
})

describe('bannerEnv', () => {
  it('is quiet for production, development and nothing', () => {
    expect(bannerEnv('production')).toBeNull()
    expect(bannerEnv('development')).toBeNull()
    expect(bannerEnv('')).toBeNull()
    expect(bannerEnv(undefined)).toBeNull()
  })

  it('names any other environment', () => {
    expect(bannerEnv(' Staging ')).toBe('staging')
  })
})

describe('DeployBanner', () => {
  it('shows staging over the page and in the title', async () => {
    serve('staging')
    renderBanner()
    expect(await screen.findByRole('note')).toHaveTextContent('STAGING')
    await waitFor(() => { expect(document.title).toBe('[STAGING] Mailroom'); })
  })

  it('sticks to the top of the window over the page, its height left to what sticks under it', async () => {
    serve('staging')
    const { unmount } = render(<DeployBanner />)
    const banner = await screen.findByRole('note')
    // jsdom lays nothing out: the classes are what keep it there.
    expect(banner).toHaveClass('sticky', 'top-0', 'z-40', 'pt-[calc(0.125rem+env(safe-area-inset-top))]')
    expect(document.documentElement.style.getPropertyValue('--deploy-banner')).toMatch(/^\d+px$/)
    unmount()
    expect(document.documentElement.style.getPropertyValue('--deploy-banner')).toBe('')
  })

  it('shows nothing in production', async () => {
    serve('production')
    renderBanner()
    await waitFor(() => { expect(fetch).toHaveBeenCalled(); })
    expect(screen.queryByRole('note')).toBeNull()
    expect(document.title).toBe('Mailroom')
  })

  it('names staging in the head: its manifest, icons and app title, production\'s back on unmount', async () => {
    serve('staging')
    const { unmount } = render(<DeployBanner />)
    await screen.findByRole('note')
    // The head is swapped in an effect after the banner shows: wait for it, not for the banner.
    await waitFor(() => {
      expect(head()).toEqual([
        '/manifest-staging.webmanifest',
        '/icon-staging.svg',
        '/apple-touch-icon-staging.png',
        'Mailroom staging',
      ])
    })
    unmount()
    expect(head()).toEqual(PRODUCTION)
  })

  it('keeps production\'s head in production', async () => {
    serve('production')
    renderBanner()
    await waitFor(() => { expect(fetch).toHaveBeenCalled(); })
    expect(head()).toEqual(PRODUCTION)
  })

  it('keeps production\'s head while /version does not answer', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('offline'))))
    renderBanner()
    await waitFor(() => { expect(fetch).toHaveBeenCalled(); })
    expect(screen.queryByRole('note')).toBeNull()
    expect(head()).toEqual(PRODUCTION)
  })
})

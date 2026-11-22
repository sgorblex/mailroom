import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

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

afterEach(() => {
  vi.unstubAllGlobals()
  document.title = 'Mailroom'
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

  it('shows nothing in production', async () => {
    serve('production')
    renderBanner()
    await waitFor(() => { expect(fetch).toHaveBeenCalled(); })
    expect(screen.queryByRole('note')).toBeNull()
    expect(document.title).toBe('Mailroom')
  })
})

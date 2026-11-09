import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AuthContext } from '@/auth/auth-context.ts'

import { LoginScreen } from './login-screen.tsx'

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status })
}

function stubConfig(config: { enabled: boolean; name: string | null } | 'missing'): void {
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      if (path === '/api/v1/auth/oidc/config' && config !== 'missing') {
        return Promise.resolve(json(200, config))
      }
      return Promise.resolve(json(404, { detail: 'Not Found' }))
    }),
  )
}

function renderSignedOut(): void {
  const reject = (): Promise<void> => Promise.reject(new Error('not in this test'))
  render(
    <AuthContext
      value={{
        status: 'signed-out',
        features: new Set(),
        signIn: reject,
        signOut: reject,
        recheck: vi.fn(),
      }}
    >
      <LoginScreen />
    </AuthContext>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '/')
})

describe('LoginScreen and SSO', () => {
  it('offers the identity provider when SSO is on, coming back to this page', async () => {
    window.history.replaceState(null, '', '/filters/3?tab=runs')
    stubConfig({ enabled: true, name: 'Authentik' })
    renderSignedOut()
    const button = await screen.findByRole('link', { name: 'Sign in with Authentik' })
    const target = new URL(button.getAttribute('href') ?? '', 'https://mailroom.test')
    expect(target.pathname).toBe('/api/v1/auth/oidc/start')
    expect(target.searchParams.get('intent')).toBe('login')
    expect(target.searchParams.get('return_to')).toBe('/filters/3?tab=runs')
  })

  it.each([{ enabled: false, name: null }, 'missing' as const])(
    'shows only the password form when SSO is off (%o)',
    async (config) => {
      stubConfig(config)
      renderSignedOut()
      expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument()
      await new Promise((resolve) => setTimeout(resolve, 0))
      expect(screen.queryByRole('link')).not.toBeInTheDocument()
    },
  )

  it('says why an SSO sign-in came back refused, then drops it from the address', async () => {
    window.history.replaceState(null, '', '/accounts?sso_error=not_linked')
    stubConfig({ enabled: true, name: 'Authentik' })
    renderSignedOut()
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'That sign-in is not linked to a Mailroom user.',
    )
    expect(window.location.search).toBe('')
    const button = await screen.findByRole('link', { name: 'Sign in with Authentik' })
    const target = new URL(button.getAttribute('href') ?? '', 'https://mailroom.test')
    expect(target.searchParams.get('return_to')).toBe('/accounts')
  })
})

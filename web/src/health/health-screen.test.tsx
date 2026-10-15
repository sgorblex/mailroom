import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { HealthScreen } from './health-screen.tsx'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('HealthScreen', () => {
  it('reports a healthy API', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('{"status":"ok"}', { status: 200 }))),
    )
    render(<HealthScreen />)
    expect(await screen.findByText('The API is up.')).toBeInTheDocument()
  })

  it('reports a failure and checks again on request', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response('{"detail":"Service Unavailable"}', { status: 503 }))
      .mockResolvedValueOnce(new Response('{"status":"ok"}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    render(<HealthScreen />)
    expect(
      await screen.findByText('The API did not answer: Service Unavailable'),
    ).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Check again' }))
    expect(await screen.findByText('The API is up.')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})

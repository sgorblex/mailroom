import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderSignedIn } from '@/test/signed-in.tsx'

import { LimitsForm } from './limits-form.tsx'

const ALICE = { username: 'alice', is_admin: false }
const DEFAULTS = { send_per_hour: 20, max_recipients: 10, body_chars: 6000, attachment_max_mb: 10 }

function stubApi(put: (body: Record<string, number>) => Response): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    expect(path).toBe('/api/v1/me/limits')
    if (init?.method === 'PUT') {
      return Promise.resolve(put(JSON.parse(init.body as string) as Record<string, number>))
    }
    return Promise.resolve(new Response(JSON.stringify(DEFAULTS), { status: 200 }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function setField(label: string, value: string): Promise<void> {
  const field = await screen.findByLabelText(label)
  await userEvent.clear(field)
  await userEvent.type(field, value)
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('LimitsForm', () => {
  it('shows the limits and saves a change', async () => {
    const fetchMock = stubApi((body) => new Response(JSON.stringify(body), { status: 200 }))
    renderSignedIn(<LimitsForm />, ALICE)

    expect(await screen.findByLabelText('Messages per hour')).toHaveValue(20)
    expect(screen.getByLabelText('Attachments per message (MB)')).toHaveValue(10)
    await setField('Attachments per message (MB)', '25')
    await userEvent.click(screen.getByRole('button', { name: 'Save limits' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Limits saved.')
    const put = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'PUT')
    expect(JSON.parse((put?.[1] as RequestInit).body as string)).toEqual({ ...DEFAULTS, attachment_max_mb: 25 })
  })

  it('marks the field a refusal is about', async () => {
    stubApi(
      () =>
        new Response(
          JSON.stringify({
            detail: {
              code: 'invalid_limits',
              message: 'body_chars: Input should be greater than or equal to 500',
              issues: [{ path: ['body_chars'], message: 'Input should be greater than or equal to 500' }],
            },
          }),
          { status: 400 },
        ),
    )
    renderSignedIn(<LimitsForm />, ALICE)

    await setField("Characters of a message's text read at a time", '600')
    await userEvent.click(screen.getByRole('button', { name: 'Save limits' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('body_chars')
    expect(screen.getByLabelText("Characters of a message's text read at a time")).toHaveAttribute(
      'aria-invalid',
      'true',
    )
    expect(screen.getByLabelText('Messages per hour')).toHaveAttribute('aria-invalid', 'false')
  })
})

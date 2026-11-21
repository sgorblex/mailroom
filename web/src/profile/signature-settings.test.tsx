import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { User } from '@/api/auth.ts'
import { bodiesTo, HOME, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderSignedIn } from '@/test/signed-in.tsx'

import { SignatureSettings } from './signature-settings.tsx'

const ALICE: User = { username: 'alice', is_admin: false, time_zone: 'UTC', mark_read_subfolders: true, index_max_age_days: null, conversations: true }

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('SignatureSettings', () => {
  it('writes each identity\'s signature in the rich editor and saves it', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi({
      accounts: [WORK, HOME],
      folders: {},
      messages: {},
      views: {},
      identities: {
        [WORK]: [
          { address: 'me@work.example', name: 'Me', default: true },
          { address: 'alias@work.example', name: null },
        ],
        [HOME]: [{ address: 'me@home.example', name: 'Me' }],
      },
      signatures: { [WORK]: { 'me@work.example': { html: '<p>Me, <strong>at work</strong></p>', text: 'Me, at work' } } },
    })
    renderSignedIn(<SignatureSettings />, ALICE)

    const work = await screen.findByRole('region', { name: 'work' })
    const mine = await within(work).findByRole('textbox', { name: 'Signature of me@work.example' }, { timeout: 10_000 })
    expect(mine.querySelector('strong')).toHaveTextContent('at work')
    expect(within(work).queryByRole('button', { name: 'Plain text' })).not.toBeInTheDocument()
    const home = await screen.findByRole('region', { name: 'home' })
    expect(await within(home).findByRole('textbox', { name: 'Signature of me@home.example' })).toBeInTheDocument()

    const alias = within(work).getByRole('textbox', { name: 'Signature of alias@work.example' })
    await user.click(alias)
    await user.keyboard('The alias')
    const saveAlias = within(work).getAllByRole('button', { name: 'Save signature' }).at(1)
    if (saveAlias === undefined) {
      throw new Error('no Save button for the alias')
    }
    await user.click(saveAlias)
    expect(await within(work).findByRole('status')).toHaveTextContent('Saved.')
    await waitFor(() => {
      expect(bodiesTo(fetchMock, `/api/v1/accounts/${String(WORK)}/signatures`).filter((body) => body !== null)).toEqual([
        { address: 'alias@work.example', html: '<p>The alias</p>' },
      ])
    })
  })
})

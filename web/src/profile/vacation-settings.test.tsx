import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { User } from '@/api/auth.ts'
import { bodiesTo, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderSignedIn } from '@/test/signed-in.tsx'

import { VacationSettings } from './vacation-settings.tsx'

const ALICE: User = { username: 'alice', is_admin: false, time_zone: 'UTC', mark_read_subfolders: true, index_max_age_days: null, conversations: true, undo_send_seconds: 10 }

afterEach(() => {
  vi.unstubAllGlobals()
})

function stub(vacationIndexed = true): ReturnType<typeof stubMailApi> {
  return stubMailApi({
    vacationIndexed,
    accounts: [WORK],
    folders: {},
    messages: {},
    views: {},
    identities: {
      [WORK]: [
        { address: 'me@work.example', name: 'Me', default: true },
        { address: 'Alias@work.example', name: null },
      ],
    },
  })
}

describe('VacationSettings', () => {
  it('switches an account\'s reply on with its days, identity and rich text', async () => {
    const user = userEvent.setup()
    const fetchMock = stub()
    renderSignedIn(<VacationSettings />, ALICE)

    const work = await screen.findByRole('region', { name: 'work' })
    const body = await within(work).findByRole('textbox', { name: 'Reply' }, { timeout: 10_000 })
    await user.click(within(work).getByRole('checkbox', { name: 'Reply automatically' }))
    await user.type(within(work).getByLabelText('Last day'), '2026-12-31')
    await user.selectOptions(within(work).getByRole('combobox', { name: 'Reply from' }), 'Alias@work.example')
    await user.type(within(work).getByRole('textbox', { name: 'Subject' }), 'Away')
    await user.click(body)
    await user.keyboard('Back in January')
    await user.click(within(work).getByRole('button', { name: 'Save vacation reply' }))

    expect(await within(work).findByRole('status')).toHaveTextContent('Saved. Replying now.')
    await waitFor(() => {
      expect(bodiesTo(fetchMock, `/api/v1/accounts/${String(WORK)}/vacation`).filter((sent) => sent !== null)).toEqual([
        {
          enabled: true,
          starts_on: null,
          ends_on: '2026-12-31',
          subject: 'Away',
          html: '<p>Back in January</p>',
          identity: 'alias@work.example',
        },
      ])
    })
  })

  it('says it is not replying while INBOX is off the mail index', async () => {
    const user = userEvent.setup()
    stub(false)
    renderSignedIn(<VacationSettings />, ALICE)

    const work = await screen.findByRole('region', { name: 'work' })
    const body = await within(work).findByRole('textbox', { name: 'Reply' }, { timeout: 10_000 })
    await user.click(within(work).getByRole('checkbox', { name: 'Reply automatically' }))
    await user.type(within(work).getByRole('textbox', { name: 'Subject' }), 'Away')
    await user.click(body)
    await user.keyboard('Back soon')
    await user.click(within(work).getByRole('button', { name: 'Save vacation reply' }))

    expect(await within(work).findByRole('status')).toHaveTextContent('not replying yet')
    expect(within(work).getByText(/turn the index on for INBOX/)).toBeInTheDocument()
  })

  it('refuses a reply switched on without a subject or a text, or with its days reversed', async () => {
    const user = userEvent.setup()
    const fetchMock = stub()
    renderSignedIn(<VacationSettings />, ALICE)

    const work = await screen.findByRole('region', { name: 'work' })
    await within(work).findByRole('textbox', { name: 'Reply' }, { timeout: 10_000 })
    await user.click(within(work).getByRole('checkbox', { name: 'Reply automatically' }))
    await user.click(within(work).getByRole('button', { name: 'Save vacation reply' }))
    expect(await within(work).findByRole('alert')).toHaveTextContent('A vacation reply needs a subject and a text.')

    await user.click(within(work).getByRole('checkbox', { name: 'Reply automatically' }))
    await user.type(within(work).getByLabelText('First day'), '2026-12-10')
    await user.type(within(work).getByLabelText('Last day'), '2026-12-01')
    await user.click(within(work).getByRole('button', { name: 'Save vacation reply' }))
    expect(await within(work).findByRole('alert')).toHaveTextContent('The last day comes before the first.')
    expect(bodiesTo(fetchMock, `/api/v1/accounts/${String(WORK)}/vacation`).filter((sent) => sent !== null)).toEqual([])
  })
})

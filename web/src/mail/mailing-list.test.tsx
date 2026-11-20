import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { MessageView } from '@/api/mail.ts'
import { bodiesTo, type FakeMailState, messageItem, messageView, refusal, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

type MailingList = NonNullable<MessageView['headers']['mailing_list']>

const item = messageItem(WORK, 'INBOX', 1, { subject: 'Weekly' })
const unsubscribePath = `/api/v1/mail/messages/${item.ref}/unsubscribe`

function state(list: Partial<MailingList> | null, fields: Partial<MessageView> = {}, overrides: Partial<FakeMailState> = {}): FakeMailState {
  const plain = messageView(item)
  const mailingList =
    list === null
      ? null
      : {
          id: 'weekly.news.example',
          name: 'Weekly <b>news</b>',
          archive: null,
          post: null,
          help: null,
          unsubscribe_https: null,
          unsubscribe_mailto: null,
          one_click: false,
          unsubscribe_fields: 1,
          unsubscribe_post_fields: 0,
          ...list,
        }
  return {
    accounts: [WORK],
    folders: { [WORK]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 1 }] },
    messages: { '1/INBOX': [item] },
    views: { [item.ref]: { ...plain, headers: { ...plain.headers, mailing_list: mailingList }, ...fields } },
    ...overrides,
  }
}

const header = async (): Promise<HTMLElement> => {
  const heading = await screen.findByRole('heading', { name: 'Weekly' })
  const found = heading.closest('header')
  if (found === null) {
    throw new Error('no message header')
  }
  return found
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the mailing list row', () => {
  it('is not there for a message from no list', async () => {
    stubMailApi(state(null))
    renderMailApp(`/mail?message=${item.ref}`)
    await header()
    expect(screen.queryByText('Mailing list')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Unsubscribe/ })).not.toBeInTheDocument()
  })

  it('names the list as plain text and links an https archive in a new tab, without referrer', async () => {
    stubMailApi(state({ archive: 'https://news.example/archive', unsubscribe_https: 'https://news.example/u?t=1' }))
    renderMailApp(`/mail?message=${item.ref}`)
    const row = await header()
    expect(within(row).getByText('Weekly <b>news</b>')).toBeInTheDocument()
    const archive = within(row).getByRole('link', { name: 'Archive' })
    expect(archive).toHaveAttribute('href', 'https://news.example/archive')
    expect(archive).toHaveAttribute('target', '_blank')
    expect(archive).toHaveAttribute('rel', 'noopener noreferrer')
    // Not one click: the list's own page, in a new tab.
    const site = within(row).getByRole('link', { name: "Unsubscribe on the list's site" })
    expect(site).toHaveAttribute('href', 'https://news.example/u?t=1')
    expect(site).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('links no archive that is not https, and falls back to the id', async () => {
    stubMailApi(state({ name: null, archive: 'http://news.example/archive' }))
    renderMailApp(`/mail?message=${item.ref}`)
    const row = await header()
    expect(within(row).getByText('weekly.news.example')).toBeInTheDocument()
    expect(within(row).queryByRole('link', { name: 'Archive' })).not.toBeInTheDocument()
  })

  it('unsubscribes in one click only once the reader confirms', async () => {
    const fetchMock = stubMailApi(
      state({ unsubscribe_https: 'https://news.example/u?t=1', one_click: true, unsubscribe_post_fields: 1 }, { one_click_unsubscribe: true }),
    )
    renderMailApp(`/mail?message=${item.ref}`)
    await userEvent.click(within(await header()).getByRole('button', { name: 'Unsubscribe' }))
    const dialog = await screen.findByRole('dialog', { name: 'Unsubscribe from Weekly <b>news</b>?' })
    // Inside the row's definition, never a child of the list of headers itself.
    expect(dialog.parentElement?.tagName).toBe('DD')
    expect(within(dialog).getByText(/Mailroom will ask news\.example to unsubscribe you/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(bodiesTo(fetchMock, unsubscribePath)).toEqual([])

    await userEvent.click(within(await header()).getByRole('button', { name: 'Unsubscribe' }))
    const again = await screen.findByRole('dialog')
    await userEvent.click(within(again).getByRole('button', { name: 'Unsubscribe' }))
    expect(await within(again).findByText('news.example accepted the request: you should get no more mail from this list.')).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/unsubscribe'))).toHaveLength(1)
    await userEvent.click(within(again).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it.each([
    ['rejected', 410, 'news.example refused the request (status 410): you may still be subscribed.'],
    ['failed', null, 'news.example did not answer: you may still be subscribed.'],
  ])('says when the list %s the request', async (outcome, status, text) => {
    stubMailApi(
      state({ unsubscribe_https: 'https://news.example/u', one_click: true }, { one_click_unsubscribe: true }, {
        refuse: { [`POST ${unsubscribePath}`]: { status: 200, body: { outcome, status, list_id: null } } },
      }),
    )
    renderMailApp(`/mail?message=${item.ref}`)
    await userEvent.click(within(await header()).getByRole('button', { name: 'Unsubscribe' }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Unsubscribe' }))
    expect(await screen.findByText(text)).toHaveAttribute('role', 'alert')
  })

  it("words the server's refusal", async () => {
    stubMailApi(
      state({ unsubscribe_https: 'https://news.example/u', one_click: true }, { one_click_unsubscribe: true }, {
        refuse: { [`POST ${unsubscribePath}`]: refusal(400, 'unsubscribe_unverified') },
      }),
    )
    renderMailApp(`/mail?message=${item.ref}`)
    await userEvent.click(within(await header()).getByRole('button', { name: 'Unsubscribe' }))
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Unsubscribe' }))
    expect(
      await screen.findByText("Set the account's authentication server on the Accounts page to unsubscribe in one click."),
    ).toHaveAttribute('role', 'alert')
  })

  it('opens the composer on the unsubscribe mail, which it does not send', async () => {
    const fetchMock = stubMailApi(state({ unsubscribe_mailto: 'mailto:leave@news.example?subject=stop' }))
    renderMailApp(`/mail?message=${item.ref}`)
    await userEvent.click(within(await header()).getByRole('button', { name: 'Unsubscribe by mail' }))
    const form = await screen.findByRole('form', { name: 'Unsubscribe' })
    expect(within(form).getByText('leave@news.example')).toBeInTheDocument()
    expect(within(form).getByRole('textbox', { name: 'Subject' })).toHaveValue('stop')
    expect(bodiesTo(fetchMock, '/api/v1/mail/drafts/prefill')).toEqual([{ ref: item.ref, mode: 'unsubscribe' }])
    expect(fetchMock.mock.calls.filter(([path]) => String(path).includes('/send'))).toEqual([])
  })

  it('offers no mail to write when sending is off, but the link if there is one', async () => {
    stubMailApi(
      state({ unsubscribe_mailto: 'mailto:leave@news.example', unsubscribe_https: 'https://news.example/u' }, {}, { sendModes: { [WORK]: 'off' } }),
    )
    renderMailApp(`/mail?message=${item.ref}`)
    const row = await header()
    expect(within(row).queryByRole('button', { name: 'Unsubscribe by mail' })).not.toBeInTheDocument()
    expect(within(row).getByRole('link', { name: "Unsubscribe on the list's site" })).toBeInTheDocument()
  })

  it('says once why it cannot write the unsubscribe mail when sending is off', async () => {
    stubMailApi(state({ unsubscribe_mailto: 'mailto:leave@news.example' }, {}, { sendModes: { [WORK]: 'off' } }))
    renderMailApp(`/mail?message=${item.ref}`)
    const row = await header()
    const button = within(row).getByRole('button', { name: 'Unsubscribe by mail' })
    const hint = "Sending is off for this account, so it can't write mail. Change that in Accounts."
    expect(button).toHaveAttribute('aria-disabled', 'true')
    expect(button).not.toHaveAttribute('title')
    expect(button).toHaveAccessibleDescription(hint)
    expect(within(row).getAllByText(hint)).toHaveLength(1)
  })
})

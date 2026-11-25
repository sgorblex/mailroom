import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { bodiesTo, HOME, messageItem, stubMailApi, WORK, type FakeMailState } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

function state(): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 1 }],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 1 }],
    },
    messages: {
      '1/INBOX': [messageItem(WORK, 'INBOX', 1, { subject: 'From work' })],
      '2/INBOX': [messageItem(HOME, 'INBOX', 1, { subject: 'From home' })],
    },
    views: {},
    searchResult: { messages: [messageItem(WORK, 'INBOX', 1, { subject: 'Found' })], next_cursor: null, failures: [] },
  }
}

const box = (): HTMLElement => screen.getByRole('searchbox', { name: 'Search terms' })
const chips = (): string[] =>
  within(screen.getByRole('list', { name: 'Terms of this search' }))
    .getAllByRole('listitem')
    .map((item) => item.textContent)

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the search box', () => {
  it('shows the terms as they are typed, as chips that remove them, and the form keeps to the line', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')
    await screen.findByText('From work')

    await userEvent.type(box(), 'from:ada is:unread "Q1 report"')
    expect(chips()).toEqual(['From: ada', 'Unread', 'Q1 report'])
    await userEvent.click(screen.getByRole('button', { name: 'Remove Unread' }))
    expect(box()).toHaveValue('from:ada "Q1 report"')

    await userEvent.click(screen.getByRole('button', { name: 'More options' }))
    expect(screen.getByLabelText('From')).toHaveValue('ada')
    expect(screen.getByLabelText('Unread')).not.toBeChecked()
    // Unticking drops the term; it never turns into is:read.
    await userEvent.click(screen.getByRole('checkbox', { name: 'Unread' }))
    expect(box()).toHaveValue('from:ada "Q1 report" is:unread')
    await userEvent.click(screen.getByRole('checkbox', { name: 'Unread' }))
    expect(box()).toHaveValue('from:ada "Q1 report"')
    await userEvent.type(screen.getByLabelText('Subject'), 'board meeting')
    await userEvent.click(screen.getByRole('checkbox', { name: 'Handled' }))
    await userEvent.selectOptions(screen.getByLabelText('Account'), 'home')
    expect(box()).toHaveValue('from:ada "Q1 report" subject:"board meeting" is:handled account:home')
    expect(chips()).toEqual(['From: ada', 'Q1 report', 'Subject: board meeting', 'Handled', 'Account: home'])

    await userEvent.clear(screen.getByLabelText('From'))
    expect(box()).toHaveValue('"Q1 report" subject:"board meeting" is:handled account:home')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => {
      expect(bodiesTo(fetchMock, '/api/v1/mail/search')).toEqual([
        { account: HOME, folder: 'INBOX', text: 'Q1 report', subject: 'board meeting', handled: true },
      ])
    })
  })

  it('writes the search shown as a line, its account and folder only when not the ones shown', async () => {
    stubMailApi(state())
    const q = { account: String(HOME), folder: '*', from: 'Ada Lovelace', unread: false, text: 'invoice' }
    renderMailApp(`/mail?q=${encodeURIComponent(JSON.stringify(q))}`)

    expect(await screen.findByRole('searchbox', { name: 'Search terms' })).toHaveValue('invoice from:"Ada Lovelace" is:read in:* account:home')
    expect(chips()).toEqual(['invoice', 'From: Ada Lovelace', 'Read', 'Folder: All folders (not Junk or Trash)', 'Account: home'])
  })

  it('says what is wrong with a line, never searching it', async () => {
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')
    await screen.findByText('From work')

    await userEvent.type(box(), 'is:starred')
    expect(screen.getByText('is: takes unread, read, flagged, handled.')).toBeInTheDocument()
    expect(box()).toHaveAttribute('aria-invalid', 'true')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    await userEvent.clear(box())
    await userEvent.type(box(), 'account:nobody')
    expect(screen.getByText('No account of yours has that name.')).toBeInTheDocument()
    await userEvent.type(box(), ' "open')
    expect(screen.getByText('A quote is not closed.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'More options' }))
    expect(screen.getByLabelText('From')).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(bodiesTo(fetchMock, '/api/v1/mail/search')).toEqual([])
  })
})

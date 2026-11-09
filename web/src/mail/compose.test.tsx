import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SendResult } from '@/api/mail.ts'
import {
  bodiesTo,
  fakeDraft,
  messageItem,
  messageView,
  queriesTo,
  refusal,
  savesOf,
  stubMailApi,
  type FakeMailState,
} from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const inbox = [messageItem('work', 'INBOX', 2), messageItem('work', 'INBOX', 1)]
const [first] = inbox
const firstRef = first?.ref ?? ''
const firstLink = /Message 2/

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: ['work', 'home'],
    folders: {
      work: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 2 },
        { path: 'Sent', role: 'sent', unread: 0, total: 0 },
        { path: 'Drafts', role: 'drafts', unread: 0, total: 0 },
        { path: 'Trash', role: 'trash', unread: 0, total: 0 },
      ],
      home: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 0 }],
    },
    messages: { 'work/INBOX': inbox, 'work/Sent': [], 'work/Drafts': [], 'home/INBOX': [] },
    views: Object.fromEntries(inbox.map((item) => [item.ref, messageView(item)])),
    ...overrides,
  }
}

const composer = (name: string | RegExp = 'New message'): Promise<HTMLElement> => screen.findByRole('form', { name })
const compose = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(await screen.findByRole('button', { name: 'Compose' }))
  return composer()
}
/** The visible notice; the live region says the same. Three polls of a send take 4 s, so wait past them. */
const sentNotice = (text: string | RegExp = 'Sent.') => screen.findByText(text, { selector: 'p' }, { timeout: 6_000 })
const field = (form: HTMLElement, name: string) => within(form).getByRole('textbox', { name })

/** The compose live regions: the second of each kind, after the toolbar's. */
function live(kind: 'polite' | 'assertive'): HTMLElement | undefined {
  return [...document.querySelectorAll<HTMLElement>(`[aria-live="${kind}"]`)][1]
}

/** A `hold` for the first `target` request, and what lets it go. */
function holdFirst(target: string): { hold: NonNullable<FakeMailState['hold']>; release: () => void } {
  let release = (): void => undefined
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  let held = false
  return {
    hold: (request) => {
      if (request !== target || held) {
        return undefined
      }
      held = true
      return gate
    },
    release: () => {
      release()
    },
  }
}

const SAVE = 'POST /api/v1/mail/drafts'
/** The first autosave has gone out, unanswered or not. */
const firstSave = (fetchMock: ReturnType<typeof stubMailApi>) =>
  waitFor(
    () => {
      expect(savesOf(fetchMock)).toHaveLength(1)
    },
    { timeout: 4_000 },
  )
const sendsOf = (fetchMock: ReturnType<typeof stubMailApi>) =>
  fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/send'))

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('composing', () => {
  it('autosaves committed recipients only: a half-typed address cannot fail a save', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')
    const form = await compose(user)

    await user.type(field(form, 'To'), 'ada@example.org,hal')
    await user.type(field(form, 'Message'), 'Hello')
    // Leaving the field for the body has committed what it could, the rest stays typed.
    expect(field(form, 'To')).toHaveValue('hal')
    expect(field(form, 'To')).toHaveAttribute('aria-invalid', 'true')
    expect(field(form, 'To')).toHaveAccessibleDescription(/not an address/)

    await within(form).findByText('Saved', {}, { timeout: 5_000 })
    const [save] = savesOf(fetchMock)
    expect(save?.fields.to).toEqual([{ name: '', address: 'ada@example.org' }])
    expect(save?.fields.text).toBe('Hello')
    expect(save?.fields.ref).toBeNull()
  })

  it('replaces the draft from the latest ref, under one compose id', async () => {
    const user = userEvent.setup()
    const fake = state()
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail')
    const form = await compose(user)
    await user.type(field(form, 'Subject'), 'One')
    await within(form).findByText('Saved', {}, { timeout: 5_000 })
    await user.type(field(form, 'Subject'), ' two')
    await within(form).findByText('Saved', {}, { timeout: 5_000 })

    const [one, two] = savesOf(fetchMock)
    expect(two?.fields.compose_id).toBe(one?.fields.compose_id)
    expect(two?.fields.ref).not.toBeNull()
    expect(fake.drafts).toHaveLength(1)
    expect(fake.drafts?.[0]?.subject).toBe('One two')
  })

  it('keeps the editor through sidebar links', async () => {
    const user = userEvent.setup()
    stubMailApi(state())
    renderMailApp('/mail')
    const form = await compose(user)
    await user.type(field(form, 'Message'), 'Still here')

    await user.click(await screen.findByRole('link', { name: 'Sent' }))
    await screen.findByText('No messages in this folder.')
    expect(field(await composer(), 'Message')).toHaveValue('Still here')
    await user.click(screen.getByRole('link', { name: 'home' }))
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'home' })).toBeInTheDocument()
    })
    expect(field(await composer(), 'Message')).toHaveValue('Still here')
  })

  it('keeps the editor behind an opened message', async () => {
    const user = userEvent.setup()
    stubMailApi(state())
    renderMailApp('/mail')
    const form = await compose(user)
    await user.type(field(form, 'Message'), 'Back soon')

    await user.click(await screen.findByRole('link', { name: firstLink }))
    await screen.findByRole('heading', { name: 'Message 2' })
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Back to draft' }))
    expect(field(await composer(), 'Message')).toHaveValue('Back soon')
    expect(field(await composer(), 'Message')).toHaveFocus()
  })

  it('offers no compose action on an account in send mode off, and says why', async () => {
    const user = userEvent.setup()
    stubMailApi(state({ sendModes: { work: 'off' } }))
    renderMailApp(`/mail?message=${firstRef}`)
    const button = await screen.findByRole('button', { name: 'Compose' })
    expect(button).toHaveAttribute('aria-disabled', 'true')
    expect(button).toHaveAccessibleDescription(/Sending is off/)
    await user.click(button)
    const reply = await screen.findByRole('button', { name: 'Reply' })
    expect(reply).toHaveAttribute('aria-disabled', 'true')
    await user.click(reply)
    await user.keyboard('cr')
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
  })

  it('flushes unsaved text on closing, and leaves no draft of nothing', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')

    let form = await compose(user)
    await user.click(within(form).getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(screen.queryByRole('form')).not.toBeInTheDocument()
    })
    expect(savesOf(fetchMock)).toHaveLength(0)

    form = await compose(user)
    await user.type(field(form, 'Message'), 'Keep me')
    await user.click(within(form).getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(screen.queryByRole('form')).not.toBeInTheDocument()
    })
    expect(savesOf(fetchMock).map((save) => save.fields.text)).toEqual(['Keep me'])
  })

  it('still saves under Strict Mode, whose trial unmount leaves an empty composer', async () => {
    const user = userEvent.setup()
    const fake = state()
    stubMailApi(fake)
    renderMailApp('/mail', { strict: true })
    const form = await compose(user)
    await user.type(field(form, 'Message'), 'Hello')
    await within(form).findByText('Saved', {}, { timeout: 5_000 })
    expect(fake.drafts?.map((each) => each.text)).toEqual(['Hello'])
  })

  it('saves nothing later for a composer emptied and closed', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')
    const form = await compose(user)
    await user.type(field(form, 'Message'), 'x')
    await user.clear(field(form, 'Message'))
    await user.click(within(form).getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(screen.queryByRole('form')).not.toBeInTheDocument()
    })
    await new Promise((resolve) => setTimeout(resolve, 2_300))
    expect(savesOf(fetchMock)).toHaveLength(0)
  })

  it('takes back a first save still on its way when the composer is emptied and closed', async () => {
    const user = userEvent.setup()
    const saves = holdFirst(SAVE)
    const fake = state({ hold: saves.hold })
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail')
    const form = await compose(user)
    await user.type(field(form, 'Message'), 'x')
    await firstSave(fetchMock)
    await user.clear(field(form, 'Message'))
    await user.click(within(form).getByRole('button', { name: 'Close' }))
    saves.release()
    await waitFor(() => {
      expect(screen.queryByRole('form')).not.toBeInTheDocument()
    })
    await waitFor(() => {
      expect(fake.drafts).toEqual([])
    })
  })

  it('asks before closing what could not be saved', async () => {
    const user = userEvent.setup()
    stubMailApi(state({ refuse: { 'POST /api/v1/mail/drafts': refusal(429, 'busy') } }))
    renderMailApp('/mail')
    const form = await compose(user)
    await user.type(field(form, 'Message'), 'Unsaved')
    await user.click(within(form).getByRole('button', { name: 'Close' }))

    const dialog = await screen.findByRole('dialog', { name: 'Not saved.' })
    await user.click(within(dialog).getByRole('button', { name: 'Keep editing' }))
    expect(field(form, 'Message')).toHaveValue('Unsaved')
    expect(within(form).getByText(/Not saved\./)).toHaveTextContent('account is busy')
  })

  it('tells a screen reader of each failed save, not only the first', async () => {
    const user = userEvent.setup()
    const fake = state({ refuse: { [SAVE]: refusal(429, 'busy') } })
    stubMailApi(fake)
    renderMailApp('/mail')
    const form = await compose(user)
    await user.type(field(form, 'Message'), 'Unsaved')
    await within(form).findByText(/Not saved\./, {}, { timeout: 5_000 })
    await waitFor(() => {
      expect(live('assertive')).toHaveTextContent('Not saved.')
    })

    const retry = holdFirst(SAVE)
    fake.hold = retry.hold
    await user.click(within(form).getByRole('button', { name: 'Try again' }))
    await waitFor(() => {
      expect(live('assertive')).toBeEmptyDOMElement()
    })
    retry.release()
    await waitFor(() => {
      expect(live('assertive')).toHaveTextContent('Not saved.')
    })
  })

  it('closes a draft whose save it cannot find yet, without asking: the text is stored', async () => {
    const user = userEvent.setup()
    const fake = state({ saveWithoutRef: true })
    stubMailApi(fake)
    renderMailApp('/mail')
    const form = await compose(user)
    await user.type(field(form, 'Message'), 'Stored')
    await user.click(within(form).getByRole('button', { name: 'Close' }))
    await waitFor(
      () => {
        expect(screen.queryByRole('form')).not.toBeInTheDocument()
      },
      { timeout: 6_000 },
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(fake.drafts?.map((each) => each.text)).toEqual(['Stored'])
  })

  it('leaves triage alone behind one of its dialogs', async () => {
    const user = userEvent.setup()
    const fake = state()
    stubMailApi(fake)
    const history = renderMailApp(`/mail?message=${firstRef}`)
    await screen.findByRole('heading', { name: 'Message 2' })
    await user.keyboard('c')
    const form = await composer()
    await user.type(field(form, 'Message'), 'x')
    await user.click(within(form).getByRole('button', { name: 'Delete draft' }))
    const dialog = await screen.findByRole('dialog', { name: 'Delete draft' })

    await user.keyboard('#j')
    expect(fake.messages['work/Trash'] ?? []).toEqual([])
    expect(fake.messages['work/INBOX']).toHaveLength(2)
    expect(history.location.search).toContain(firstRef)
    expect(dialog).toBeVisible()
  })

  it('flushes text alone, with keepalive, when the page goes', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')
    const form = await compose(user)
    await user.upload(within(form).getByLabelText('Attach files'), new File(['x'], 'big.bin'))
    await user.type(field(form, 'Message'), 'Bye')

    fireEvent(window, new Event('pagehide'))
    await waitFor(() => {
      expect(savesOf(fetchMock)).toHaveLength(1)
    })
    expect(savesOf(fetchMock)[0]).toMatchObject({ files: [], keepalive: true })
    expect(savesOf(fetchMock)[0]?.fields.text).toBe('Bye')
    // Still open, so the file goes with the next save.
    await waitFor(
      () => {
        expect(savesOf(fetchMock)[1]?.files).toEqual(['big.bin'])
      },
      { timeout: 5_000 },
    )
  })

  it('brings the composer back after a reload', async () => {
    const draft = fakeDraft('work', 90, { text: 'Where I was' })
    stubMailApi(state({ drafts: [draft] }))
    renderMailApp(`/mail?compose=${draft.compose_id ?? ''}.work`)
    expect(field(await composer('Draft'), 'Message')).toHaveValue('Where I was')
  })

  it('names the open composer in the URL by its compose id, not by a ref', async () => {
    const user = userEvent.setup()
    stubMailApi(state())
    const history = renderMailApp('/mail')
    await compose(user)
    await waitFor(() => {
      expect(history.location.search).toMatch(/compose=[0-9a-f-]{36}\.work/)
    })
  })

  it('opens nothing for a composer that was never saved', async () => {
    stubMailApi(state())
    const history = renderMailApp('/mail?compose=00000000-0000-4000-8000-000000000001.work')
    await waitFor(() => {
      expect(history.location.search).not.toContain('compose')
    })
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
  })

  it('saves only on request when the server cannot replace drafts', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(state({ autosave: false }))
    renderMailApp('/mail')
    const form = await compose(user)
    await user.type(field(form, 'Message'), 'a')
    await within(form).findByText(/saved only when you ask/, {}, { timeout: 5_000 })
    await user.type(field(form, 'Message'), 'b')
    const save = await within(form).findByRole('button', { name: 'Save draft' })
    await new Promise((resolve) => setTimeout(resolve, 2_500))
    expect(savesOf(fetchMock)).toHaveLength(1)
    await user.click(save)
    await waitFor(() => {
      expect(savesOf(fetchMock)).toHaveLength(2)
    })
  })
})

describe('replying and forwarding', () => {
  it('prefills a reply and writes nothing until it changes', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(state())
    renderMailApp(`/mail?message=${firstRef}`)
    await user.click(await screen.findByRole('button', { name: 'Reply' }))

    const form = await composer('Reply')
    expect(field(form, 'Subject')).toHaveValue('Re: Message 2')
    expect(field(form, 'Message')).toHaveFocus()
    expect(within(form).getByRole('list', { name: 'To recipients' })).toHaveTextContent('sender2@example.org')
    expect(bodiesTo(fetchMock, '/api/v1/mail/drafts/prefill')).toEqual([{ ref: firstRef, mode: 'reply' }])

    await user.click(within(form).getByRole('button', { name: 'Close' }))
    await screen.findByRole('heading', { name: 'Message 2' })
    expect(savesOf(fetchMock)).toHaveLength(0)
  })

  it('reply all opens Cc, and a save carries what it answers', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(state())
    renderMailApp(`/mail?message=${firstRef}`)
    await user.click(await screen.findByRole('button', { name: 'Reply all' }))
    const form = await composer('Reply all')
    expect(within(form).getByRole('list', { name: 'Cc recipients' })).toHaveTextContent('me@example.org')
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await sentNotice()
    expect(savesOf(fetchMock)[0]?.fields.answers).toBe(firstRef)
  })

  it('offers a forward\'s attachments as keep entries the user can drop', async () => {
    const user = userEvent.setup()
    const part = (number: string, name: string) => ({ part: number, name, type: 'text/plain', size: 10 })
    const fake = state()
    if (first !== undefined) {
      fake.views[firstRef] = messageView(first, { attachments: [part('2', 'a.txt'), part('3', 'b.txt')] })
    }
    const fetchMock = stubMailApi(fake)
    renderMailApp(`/mail?message=${firstRef}`)
    await user.click(await screen.findByRole('button', { name: 'Forward' }))
    const form = await composer('Forward')
    await user.click(within(form).getByRole('button', { name: 'Remove a.txt' }))
    await user.type(field(form, 'To'), 'ada@example.org{Enter}')
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await sentNotice()
    expect(savesOf(fetchMock)[0]?.fields.keep).toEqual([{ ref: firstRef, part: '3' }])
    expect(savesOf(fetchMock)[0]?.fields.answers).toBeNull()
  })

  it('has shortcuts for each, none while a composer is open', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(state())
    renderMailApp(`/mail?message=${firstRef}`)
    await screen.findByRole('heading', { name: 'Message 2' })

    await user.keyboard('f')
    const form = await composer('Forward')
    await user.click(within(form).getByRole('heading', { name: 'Forward' }))
    await user.keyboard('rac')
    expect(await composer('Forward')).toBe(form)
    expect(bodiesTo(fetchMock, '/api/v1/mail/drafts/prefill')).toHaveLength(1)

    await user.click(within(form).getByRole('button', { name: 'Close' }))
    await screen.findByRole('heading', { name: 'Message 2' })
    await user.keyboard('a')
    await composer('Reply all')
  })

  it('c writes a new message on the account shown', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail?account=home')
    await screen.findByRole('heading', { name: 'home' })
    await user.keyboard('c')
    const form = await composer()
    await user.type(field(form, 'Message'), 'From home')
    await user.click(within(form).getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(savesOf(fetchMock)[0]?.fields.account).toBe('home')
    })
  })
})

describe('identities', () => {
  it('chooses among them, the default first', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(
      state({
        identities: {
          work: [
            { address: 'me@work.example', name: 'Me', default: true },
            { address: 'alias@work.example', name: null },
          ],
        },
      }),
    )
    renderMailApp('/mail')
    const form = await compose(user)
    const from = within(form).getByRole('combobox', { name: 'From' })
    expect(from).toHaveValue('me@work.example')
    await user.selectOptions(from, 'alias@work.example')
    await user.type(field(form, 'Subject'), 'From my alias')
    await user.click(within(form).getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(savesOf(fetchMock)[0]?.fields.from).toBe('alias@work.example')
    })
  })
})

describe('attachments', () => {
  it('uploads a file once: later saves keep the draft\'s own part', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(state())
    renderMailApp('/mail')
    const form = await compose(user)
    await user.upload(within(form).getByLabelText('Attach files'), new File(['hello'], 'notes.txt', { type: 'text/plain' }))
    await within(form).findByText('Saved', {}, { timeout: 5_000 })
    await user.type(field(form, 'Message'), 'x')
    await within(form).findByText('Saved', {}, { timeout: 5_000 })

    const [one, two] = savesOf(fetchMock)
    expect(one?.files).toEqual(['notes.txt'])
    expect(two?.files).toEqual([])
    expect(two?.fields.keep).toEqual([{ ref: two?.fields.ref, part: '2' }])
  })

  it('says early what is over the limits', async () => {
    const user = userEvent.setup()
    stubMailApi(state({ attachmentMaxMb: 1 }))
    renderMailApp('/mail')
    const form = await compose(user)
    const input = within(form).getByLabelText('Attach files')
    await user.upload(input, new File([new ArrayBuffer(2 ** 21)], 'big.bin'))
    expect(await within(form).findByRole('alert')).toHaveTextContent('1 MB')
    expect(input).toHaveAccessibleDescription(/1 MB/)
    expect(within(form).queryByText('big.bin')).not.toBeInTheDocument()
  })

  it('go with a draft deleted elsewhere: the text is saved again, and the user is told', async () => {
    const user = userEvent.setup()
    const parts = [{ part: '2', name: 'notes.pdf', type: 'application/pdf', size: 10 }]
    const draft = fakeDraft('work', 41, { text: 'Mine', parts })
    const fake = state({ drafts: [draft] })
    const fetchMock = stubMailApi(fake)
    renderMailApp(`/mail?compose=${draft.ref}`)
    const form = await composer('Draft')
    expect(within(form).getByText('notes.pdf')).toBeInTheDocument()

    fake.drafts = []
    await user.type(field(form, 'Message'), ' more')
    await within(form).findByText(/went with it/, {}, { timeout: 5_000 })
    await within(form).findByText('Saved', {}, { timeout: 5_000 })
    expect(savesOf(fetchMock).at(-1)?.fields.keep).toEqual([])
    expect(fake.drafts).toHaveLength(1)
    expect(fake.drafts[0]?.text).toBe('Mine more')
    expect(within(form).queryByText('notes.pdf')).not.toBeInTheDocument()
  })
})

describe('drafts', () => {
  const draft = fakeDraft('work', 40, {
    to: [{ name: '', address: 'ada@example.org' }],
    subject: 'Half written',
    text: 'So far',
  })
  const item = messageItem('work', 'Drafts', 40, { ref: draft.ref, subject: draft.subject, flags: { seen: false, flagged: false, answered: false, draft: true } })
  const withDraft = (fields: Partial<typeof draft> = {}): FakeMailState =>
    state({
      messages: { 'work/INBOX': inbox, 'work/Sent': [], 'work/Drafts': [item], 'home/INBOX': [] },
      views: { [item.ref]: messageView(item) },
      drafts: [{ ...draft, ...fields }],
    })

  it('open in the composer, and are not marked read', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(withDraft())
    renderMailApp('/mail?folder=Drafts')
    await user.click(await screen.findByRole('link', { name: /Half written/ }))
    const form = await composer('Draft')
    expect(field(form, 'Subject')).toHaveValue('Half written')
    expect(field(form, 'Message')).toHaveValue('So far')
    expect(bodiesTo(fetchMock, '/api/v1/mail/messages/flag')).toEqual([])
  })

  it('show as a message on an account in send mode off', async () => {
    const fake = withDraft()
    fake.sendModes = { work: 'off' }
    stubMailApi(fake)
    renderMailApp(`/mail?folder=Drafts&message=${item.ref}`)
    await screen.findByRole('heading', { name: 'Half written' })
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
  })

  it('warn when they hold what plain text cannot', async () => {
    stubMailApi(withDraft({ lossy: true, compose_id: null }))
    renderMailApp(`/mail?folder=Drafts&compose=${draft.ref}`)
    expect(within(await composer('Draft')).getByText(/keeps only its plain text/)).toBeInTheDocument()
  })

  it('give a foreign draft a compose id, and replace it on the next save', async () => {
    const user = userEvent.setup()
    const fake = withDraft({ compose_id: null })
    const fetchMock = stubMailApi(fake)
    renderMailApp(`/mail?compose=${draft.ref}`)
    const form = await composer('Draft')
    await user.type(field(form, 'Message'), '!')
    await user.click(within(form).getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(savesOf(fetchMock)).toHaveLength(1)
    })
    expect(savesOf(fetchMock)[0]?.fields).toMatchObject({ ref: draft.ref, text: 'So far!' })
    expect(savesOf(fetchMock)[0]?.fields.compose_id).toMatch(/^[0-9a-f-]{36}$/)
  })

  it('delete after asking', async () => {
    const user = userEvent.setup()
    const fake = withDraft()
    stubMailApi(fake)
    renderMailApp(`/mail?folder=Drafts&compose=${draft.ref}`)
    const form = await composer('Draft')
    await user.click(within(form).getByRole('button', { name: 'Delete draft' }))
    const dialog = await screen.findByRole('dialog', { name: 'Delete draft' })
    await user.click(within(dialog).getByRole('button', { name: 'Delete draft' }))
    await waitFor(() => {
      expect(screen.queryByRole('form')).not.toBeInTheDocument()
    })
    expect(fake.drafts).toEqual([])
    await waitFor(() => {
      expect(screen.queryByRole('link', { name: /Half written/ })).not.toBeInTheDocument()
    })
  })

  it('leave the list up to date after a save', async () => {
    const user = userEvent.setup()
    stubMailApi(withDraft())
    renderMailApp(`/mail?folder=Drafts&compose=${draft.ref}`)
    const form = await composer('Draft')
    await user.clear(field(form, 'Subject'))
    await user.type(field(form, 'Subject'), 'Renamed')
    await screen.findByRole('link', { name: /Renamed/ }, { timeout: 5_000 })
    expect(screen.queryByRole('link', { name: /Half written/ })).not.toBeInTheDocument()
  })

  it('stay open across their saves, each replacing the last', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(withDraft())
    renderMailApp(`/mail?folder=Drafts&compose=${draft.ref}`)
    const form = await composer('Draft')
    await user.type(field(form, 'Message'), ', and more')
    await within(form).findByText('Saved', {}, { timeout: 5_000 })
    await user.type(field(await composer('Draft'), 'Message'), ' and again')
    await waitFor(
      () => {
        expect(savesOf(fetchMock)).toHaveLength(2)
      },
      { timeout: 5_000 },
    )
    const [firstSave, secondSave] = savesOf(fetchMock)
    expect(firstSave?.fields.ref).toBe(draft.ref)
    expect(secondSave?.fields.ref).not.toBe(draft.ref)
    expect(secondSave?.fields.text).toBe('So far, and more and again')
    const reads = fetchMock.mock.calls.filter(([path]) => String(path).includes(`/drafts/${draft.ref}`))
    expect(reads).toHaveLength(1)
  })

  it('reopen with the unknown banner until settled', async () => {
    const send = { send_id: 's1', state: 'unknown' as const, code: null }
    stubMailApi(withDraft({ send }))
    renderMailApp(`/mail?compose=${draft.ref}`)
    const form = await composer('Draft')
    expect(within(form).getByText(/Can't confirm delivery/)).toBeInTheDocument()
    expect(within(form).getByRole('button', { name: 'Send anyway' })).toBeInTheDocument()
    expect(field(form, 'Message')).toBeEnabled()
    expect(within(form).queryByText(/can't be changed here/)).not.toBeInTheDocument()
  })

  it('open read-only when written elsewhere and held by a send, its ways out intact', async () => {
    const user = userEvent.setup()
    const send = { send_id: 's1', state: 'unknown' as const, code: null }
    const fake = withDraft({ compose_id: null, send })
    fake.sends = {
      [draft.ref]: { ...send, filed: null, rejected: [], notes: [], note_codes: [], settled: false, polls: 0 },
    }
    const fetchMock = stubMailApi(fake)
    renderMailApp(`/mail?compose=${draft.ref}`)
    const form = await composer('Draft')

    expect(within(form).getByText(/can't be changed here/)).toBeInTheDocument()
    expect(field(form, 'Message')).toHaveAttribute('readonly')
    expect(field(form, 'Subject')).toHaveAttribute('readonly')
    expect(field(form, 'To')).toBeDisabled()
    expect(within(form).getByRole('button', { name: 'Open Sent' })).toBeInTheDocument()
    expect(within(form).getByRole('button', { name: 'Delete draft' })).toBeInTheDocument()

    await user.click(within(form).getByRole('button', { name: 'Keep draft' }))
    expect(within(form).queryByText(/Can't confirm/)).not.toBeInTheDocument()
    expect(within(form).getByText(/can't be changed here/)).toBeInTheDocument()
    expect(field(form, 'Message')).toHaveAttribute('readonly')

    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await user.click(await within(form).findByRole('button', { name: 'Send anyway' }))
    const dialog = await screen.findByRole('dialog', { name: 'Send anyway' })
    await user.click(within(dialog).getByRole('button', { name: 'Send anyway' }))
    await sentNotice()
    expect(fake.delivered?.map((each) => each.text)).toEqual(['So far'])
    expect(savesOf(fetchMock)).toEqual([])
    expect(screen.queryByText(/Not saved/)).not.toBeInTheDocument()
  })

  it('open read-only when written elsewhere and already sent', async () => {
    const send = { send_id: 's1', state: 'sent' as const, code: null }
    stubMailApi(withDraft({ compose_id: null, send }))
    renderMailApp(`/mail?compose=${draft.ref}`)
    const form = await composer('Draft')
    expect(within(form).getByText(/can't be changed here/)).toBeInTheDocument()
    expect(within(form).getByText(/already sent/)).toBeInTheDocument()
    expect(field(form, 'Message')).toHaveAttribute('readonly')
    expect(within(form).getByRole('button', { name: 'Delete draft' })).toBeInTheDocument()
  })

  it('stay editable when written elsewhere after a failed send: no send holds them', async () => {
    const send = { send_id: 's1', state: 'failed' as const, code: 'server_error' }
    stubMailApi(withDraft({ compose_id: null, send }))
    renderMailApp(`/mail?compose=${draft.ref}`)
    expect(field(await composer('Draft'), 'Message')).toBeEnabled()
  })

  it('turn read-only when a send from here leaves one written elsewhere unknown', async () => {
    const user = userEvent.setup()
    const fake = withDraft({ compose_id: null })
    fake.sendOutcome = { state: 'unknown' }
    const fetchMock = stubMailApi(fake)
    renderMailApp(`/mail?compose=${draft.ref}`)
    const form = await composer('Draft')
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await within(form).findByText(/Can't confirm/, {}, { timeout: 3_000 })

    await user.click(within(form).getByRole('button', { name: 'Keep draft' }))
    expect(field(form, 'Message')).toHaveAttribute('readonly')
    expect(savesOf(fetchMock)).toEqual([])
  })

  it('stay editable once saved here, as their key is their own from then on', async () => {
    const user = userEvent.setup()
    const fake = withDraft({ compose_id: null })
    fake.sendOutcome = { state: 'unknown' }
    stubMailApi(fake)
    renderMailApp(`/mail?compose=${draft.ref}`)
    const form = await composer('Draft')
    await user.type(field(form, 'Message'), '!')
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await within(form).findByText(/Can't confirm/, {}, { timeout: 3_000 })
    expect(field(form, 'Message')).toBeEnabled()
  })

  describe('written elsewhere, once a send from elsewhere took them', () => {
    const SEND = `POST /api/v1/mail/drafts/${draft.ref}/send`
    const send = (state: SendResult['state']): SendResult & { polls: number } => ({
      send_id: 's1',
      state,
      code: null,
      filed: state === 'sent' ? true : null,
      rejected: [],
      notes: [],
      note_codes: [],
      settled: state === 'sent',
      polls: 0,
    })
    const typeRefused = async (fake: FakeMailState, state: SendResult['state']) => {
      const user = userEvent.setup()
      const fetchMock = stubMailApi(fake)
      renderMailApp(`/mail?compose=${draft.ref}`)
      const form = await composer('Draft')
      fake.sends = { [draft.ref]: send(state) }
      await user.type(field(form, 'Message'), '!')
      await within(form).findByText(/can't be changed here/, {}, { timeout: 5_000 })
      return { user, fetchMock, form }
    }

    it('turn read-only on the refused save, the typed text kept and no new draft made', async () => {
      const fake = withDraft({ compose_id: null })
      const { fetchMock, form } = await typeRefused(fake, 'sent')

      expect(within(form).getByText(/already sent/)).toBeInTheDocument()
      expect(within(form).getByText('Not saved.')).toBeInTheDocument()
      expect(live('assertive')).toHaveTextContent(/can't be changed here/)
      expect(field(form, 'Message')).toHaveValue('So far!')
      expect(field(form, 'Message')).toHaveAttribute('readonly')
      expect(field(form, 'Subject')).toHaveAttribute('readonly')
      expect(field(form, 'To')).toBeDisabled()
      expect(within(form).getByRole('button', { name: 'Send' })).toHaveAttribute('aria-disabled', 'true')
      await new Promise((resolve) => setTimeout(resolve, 2_500))
      expect(savesOf(fetchMock)).toHaveLength(1)
    })

    it('keep the refused text selectable while that send still runs', async () => {
      const { form } = await typeRefused(withDraft({ compose_id: null }), 'sending')

      expect(field(form, 'Message')).toHaveValue('So far!')
      expect(field(form, 'Message')).toBeEnabled()
      expect(field(form, 'Message')).toHaveAttribute('readonly')
      expect(field(form, 'Subject')).toBeEnabled()
      expect(field(form, 'Subject')).toHaveAttribute('readonly')
      expect(field(form, 'To')).toBeDisabled()
    })

    it('do so when delivery took the draft away: a refused save is not a deleted draft', async () => {
      const fake = withDraft({ compose_id: null })
      const user = userEvent.setup()
      const fetchMock = stubMailApi(fake)
      renderMailApp(`/mail?compose=${draft.ref}`)
      const form = await composer('Draft')
      fake.sends = { [draft.ref]: send('sent') }
      fake.drafts = []

      await user.type(field(form, 'Message'), '!')

      await within(form).findByText(/can't be changed here/, {}, { timeout: 5_000 })
      expect(savesOf(fetchMock).map((save) => save.fields.ref)).toEqual([draft.ref])
      expect(fake.drafts).toEqual([])
    })

    it('leave Close warning that the refused edits are not saved', async () => {
      const { user, form } = await typeRefused(withDraft({ compose_id: null }), 'sent')

      await user.click(within(form).getByRole('button', { name: 'Close' }))

      expect(await screen.findByRole('dialog', { name: 'Not saved.' })).toBeInTheDocument()
      expect(screen.getByRole('form', { name: 'Draft' })).toBeInTheDocument()
    })

    it('offer no Send anyway while the form holds refused edits, its other ways out intact', async () => {
      const fake = withDraft({ compose_id: null })
      const { user, fetchMock, form } = await typeRefused(fake, 'unknown')

      expect(within(form).getByText(/Can't confirm/)).toBeInTheDocument()
      expect(within(form).queryByRole('button', { name: 'Send anyway' })).not.toBeInTheDocument()
      expect(within(form).getByRole('button', { name: 'Open Sent' })).toBeInTheDocument()
      expect(within(form).getByRole('button', { name: 'Delete draft' })).toBeInTheDocument()
      await user.click(within(form).getByRole('button', { name: 'Send' }))
      expect(sendsOf(fetchMock)).toEqual([])
    })

    it('still offer Send anyway when nothing was refused', async () => {
      const user = userEvent.setup()
      const fake = withDraft({ compose_id: null })
      const fetchMock = stubMailApi(fake)
      renderMailApp(`/mail?compose=${draft.ref}`)
      const form = await composer('Draft')
      fake.sends = { [draft.ref]: send('unknown') }

      await user.click(within(form).getByRole('button', { name: 'Send' }))

      await within(form).findByRole('button', { name: 'Send anyway' }, { timeout: 3_000 })
      expect(savesOf(fetchMock)).toEqual([])
    })

    it('follow a send a save found running, and keep the text when it is delivered', async () => {
      const fake = withDraft({ compose_id: null })
      const { form } = await typeRefused(fake, 'sending')
      const running = fake.sends?.[draft.ref]
      if (running !== undefined) {
        Object.assign(running, send('sent'))
      }

      await within(form).findByText(/already sent/, {}, { timeout: 4_000 })

      expect(field(form, 'Message')).toHaveValue('So far!')
      expect(screen.queryByText('Sent.')).not.toBeInTheDocument()
    })

    it('stay busy when Send meets a send already running, so the draft cannot be deleted under it', async () => {
      const user = userEvent.setup()
      const fake = withDraft({ compose_id: null })
      stubMailApi(fake)
      renderMailApp(`/mail?compose=${draft.ref}`)
      const form = await composer('Draft')
      fake.sends = { [draft.ref]: send('sending') }

      await user.type(field(form, 'Message'), '!')
      await user.click(within(form).getByRole('button', { name: 'Send' }))
      await within(form).findByText('Not saved.')
      await user.click(within(form).getByRole('button', { name: 'Delete draft' }))

      expect(screen.queryByRole('dialog', { name: /Delete/ })).not.toBeInTheDocument()
    })

    it('go back to editing, and saving what was typed, if the send it found running fails', async () => {
      const fake = withDraft({ compose_id: null })
      const { fetchMock, form } = await typeRefused(fake, 'sending')
      const running = fake.sends?.[draft.ref]
      if (running !== undefined) {
        Object.assign(running, send('failed'), { code: 'server_error' })
      }

      await within(form).findByText(/Not sent/, {}, { timeout: 4_000 })
      expect(field(form, 'Message')).not.toHaveAttribute('readonly')
      await waitFor(
        () => {
          expect(savesOf(fetchMock)).toHaveLength(2)
        },
        { timeout: 5_000 },
      )
      expect(fake.drafts?.at(-1)?.text).toBe('So far!')
    })

    it('say so when Send finds it delivered, and save nothing', async () => {
      const user = userEvent.setup()
      const fake = withDraft({ compose_id: null })
      const fetchMock = stubMailApi(fake)
      renderMailApp(`/mail?compose=${draft.ref}`)
      const form = await composer('Draft')
      fake.sends = { [draft.ref]: send('sent') }
      fake.drafts = []

      await user.click(within(form).getByRole('button', { name: 'Send' }))

      await within(form).findByText(/already sent/, {}, { timeout: 3_000 })
      expect(field(form, 'Message')).toHaveAttribute('readonly')
      expect(savesOf(fetchMock)).toEqual([])
    })

    it('make no new draft after a lost Send whose retry finds it delivered', async () => {
      const user = userEvent.setup()
      const fake = withDraft({ compose_id: null, to: [{ name: '', address: 'ada@example.org' }] })
      let lost = false
      fake.lose = (request) => {
        if (request !== SEND || lost) {
          return false
        }
        lost = true
        return true
      }
      const fetchMock = stubMailApi(fake)
      renderMailApp(`/mail?compose=${draft.ref}`)
      const form = await composer('Draft')

      await user.click(within(form).getByRole('button', { name: 'Send' }))

      await sentNotice()
      expect(sendsOf(fetchMock)).toHaveLength(2)
      await new Promise((resolve) => setTimeout(resolve, 2_500))
      expect(savesOf(fetchMock)).toEqual([])
      expect(fake.delivered).toHaveLength(1)
    })

    it('make no new draft after a lost Send whose retry says delivery is unknown', async () => {
      const user = userEvent.setup()
      const fake = withDraft({ compose_id: null })
      fake.sendOutcome = { state: 'unknown' }
      let lost = false
      fake.lose = (request) => {
        if (request !== SEND || lost) {
          return false
        }
        lost = true
        fake.drafts = []
        return true
      }
      const fetchMock = stubMailApi(fake)
      renderMailApp(`/mail?compose=${draft.ref}`)
      const form = await composer('Draft')

      await user.click(within(form).getByRole('button', { name: 'Send' }))

      await within(form).findByText(/Can't confirm/, {}, { timeout: 3_000 })
      expect(field(form, 'Message')).toHaveAttribute('readonly')
      await new Promise((resolve) => setTimeout(resolve, 2_500))
      expect(savesOf(fetchMock)).toEqual([])
    })

    it('start over from the form after a confirmed Send anyway finds the draft gone', async () => {
      const user = userEvent.setup()
      const fake = withDraft({ compose_id: null })
      const fetchMock = stubMailApi(fake)
      renderMailApp(`/mail?compose=${draft.ref}`)
      const form = await composer('Draft')
      fake.sends = { [draft.ref]: send('unknown') }
      await user.click(within(form).getByRole('button', { name: 'Send' }))
      await user.click(await within(form).findByRole('button', { name: 'Send anyway' }, { timeout: 3_000 }))
      fake.drafts = []

      await user.click(within(await screen.findByRole('dialog', { name: 'Send anyway' })).getByRole('button', { name: 'Send anyway' }))

      await waitFor(
        () => {
          expect(savesOf(fetchMock).map((save) => save.fields.ref)).toEqual([null])
        },
        { timeout: 5_000 },
      )
      expect(field(form, 'Message')).not.toHaveAttribute('readonly')
    })
  })

  it('say that a stored draft Send refused is fixed by changing it', async () => {
    const user = userEvent.setup()
    const fake = withDraft({ compose_id: null, to: [{ name: '', address: 'ada@example.org' }] })
    stubMailApi(fake)
    renderMailApp(`/mail?compose=${draft.ref}`)
    const form = await composer('Draft')
    fake.refuse = { [`POST /api/v1/mail/drafts/${draft.ref}/send`]: refusal(400, 'invalid_argument') }

    await user.click(within(form).getByRole('button', { name: 'Send' }))

    expect(await within(form).findByText(/can't be sent as it is stored/, {}, { timeout: 3_000 })).toBeInTheDocument()
    expect(live('assertive')).toHaveTextContent(/Change anything in it/)
  })

  it('reopen following a send still running', async () => {
    const fake = withDraft({ send: { send_id: 's2', state: 'sending', code: null } })
    fake.sends = {
      [draft.compose_id ?? '']: {
        send_id: 's2',
        state: 'failed',
        code: 'server_error',
        filed: null,
        rejected: [],
        notes: [],
        note_codes: [],
        settled: false,
        polls: 0,
      },
    }
    stubMailApi(fake)
    renderMailApp(`/mail?compose=${draft.ref}`)
    const form = await composer('Draft')
    expect(within(form).getAllByText('Sending…').length).toBeGreaterThan(0)
    await within(form).findByText(/Not sent/, {}, { timeout: 3_000 })
  })

  it('say when they were already sent, and cannot be sent again', async () => {
    const user = userEvent.setup()
    const fetchMock = stubMailApi(withDraft({ send: { send_id: 's3', state: 'sent', code: null } }))
    renderMailApp(`/mail?compose=${draft.ref}`)
    const form = await composer('Draft')
    expect(within(form).getByText(/already sent/)).toBeInTheDocument()

    const send = within(form).getByRole('button', { name: 'Send' })
    expect(send).toHaveAttribute('aria-disabled', 'true')
    await user.click(send)
    expect(sendsOf(fetchMock)).toHaveLength(0)
    expect(await composer('Draft')).toBe(form)
    expect(within(form).getByRole('button', { name: 'Delete draft' })).toBeInTheDocument()
  })

  it('never say Sent of a leftover whose key is spent: nothing went out', async () => {
    const user = userEvent.setup()
    const fake = withDraft()
    fake.sends = {
      [draft.compose_id ?? '']: {
        send_id: 's3',
        state: 'sent',
        code: null,
        filed: true,
        rejected: [],
        notes: [],
        note_codes: [],
        settled: true,
        polls: 0,
      },
    }
    const fetchMock = stubMailApi(fake)
    renderMailApp(`/mail?compose=${draft.ref}`)
    const form = await composer('Draft')
    await user.type(field(form, 'Message'), ' and edited')
    await user.click(within(form).getByRole('button', { name: 'Send' }))

    await within(form).findByText(/already sent/, {}, { timeout: 3_000 })
    expect(live('assertive')).toHaveTextContent('already sent')
    expect(live('polite')).not.toHaveTextContent('Sent.')
    expect(screen.queryByText('Sent.', { selector: 'p' })).not.toBeInTheDocument()
    expect(await composer('Draft')).toBe(form)
    expect(fake.delivered).toBeUndefined()
    expect(fake.drafts?.at(-1)?.text).toBe('So far and edited')

    await user.click(within(form).getByRole('button', { name: 'Send' }))
    expect(sendsOf(fetchMock)).toHaveLength(1)
  })
})

describe('sending', () => {
  async function ready(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
    const form = await compose(user)
    await user.type(field(form, 'To'), 'ada@example.org{Enter}')
    await user.type(field(form, 'Message'), 'Hello')
    return form
  }

  it('saves, then sends that draft, whatever the send mode but off', async () => {
    const user = userEvent.setup()
    const fake = state({ sendModes: { work: 'drafts' }, sendingPolls: 1 })
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail')
    const form = await ready(user)
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await waitFor(() => {
      expect(within(form).getByRole('button', { name: 'Sending…' })).toHaveAttribute('aria-disabled', 'true')
    })

    await sentNotice()
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
    expect(savesOf(fetchMock)).toHaveLength(1)
    expect(fake.delivered).toHaveLength(1)
    expect(fake.drafts).toEqual([])
    expect(live('polite')).toHaveTextContent('Sent.')
  })

  it('sends typed text once it is an address, and stops when it is not', async () => {
    const user = userEvent.setup()
    const fake = state()
    stubMailApi(fake)
    renderMailApp('/mail')
    const form = await compose(user)
    await user.type(field(form, 'To'), 'nobody')
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent(/not an address/)
    expect(fake.delivered).toBeUndefined()

    await user.clear(field(form, 'To'))
    await user.type(field(form, 'To'), 'ada@example.org')
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await sentNotice()
    expect(fake.delivered).toHaveLength(1)
  })

  it('delivers what the form shows, with a save in flight and an edit after it', async () => {
    const user = userEvent.setup()
    const saves = holdFirst(SAVE)
    const fake = state({ hold: saves.hold })
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail')
    const form = await ready(user)
    await firstSave(fetchMock)
    await user.type(field(form, 'Message'), ' world')
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    saves.release()
    await sentNotice()
    expect(fake.delivered?.map((each) => each.text)).toEqual(['Hello world'])
  })

  it('stays to report a send pressed while Close waits for a save', async () => {
    const user = userEvent.setup()
    const saves = holdFirst(SAVE)
    const fake = state({ hold: saves.hold, sendingPolls: 2 })
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail')
    const form = await ready(user)
    await firstSave(fetchMock)
    await user.click(within(form).getByRole('button', { name: 'Close' }))
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    saves.release()
    await waitFor(() => {
      expect(sendsOf(fetchMock)).toHaveLength(1)
    })
    await within(form).findAllByText('Sending…')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await sentNotice()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(fake.delivered).toHaveLength(1)
  })

  it('delivers what the form shows when another writer replaced the draft meanwhile', async () => {
    const user = userEvent.setup()
    const fake = state()
    stubMailApi(fake)
    renderMailApp('/mail')
    const form = await ready(user)
    await within(form).findByText('Saved', {}, { timeout: 5_000 })
    const [saved] = fake.drafts ?? []
    if (saved === undefined) {
      throw new Error('no draft')
    }
    fake.drafts = [{ ...saved, ref: 'ref-work-Drafts-900', text: 'From an agent' }]

    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await within(form).findByText(/changed elsewhere.*press Send again/)
    expect(live('assertive')).toHaveTextContent('changed elsewhere')
    expect(within(form).queryByText(/No such message/)).not.toBeInTheDocument()
    await within(form).findByText('Saving…', { selector: 'span' })
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await sentNotice()
    expect(fake.delivered?.map((each) => each.text)).toEqual(['Hello'])
  })

  it('delivers what the form shows when a lost answer hid another writer\'s version', async () => {
    const user = userEvent.setup()
    let lost = false
    const fake = state({
      lose: (request) => {
        if (request === SAVE && !lost && fake.drafts?.[0]?.text === 'From an agent') {
          lost = true
          return true
        }
        return false
      },
    })
    stubMailApi(fake)
    renderMailApp('/mail')
    const form = await ready(user)
    await within(form).findByText('Saved', {}, { timeout: 5_000 })
    const [saved] = fake.drafts ?? []
    if (saved === undefined) {
      throw new Error('no draft')
    }
    fake.drafts = [{ ...saved, ref: 'ref-work-Drafts-900', text: 'From an agent' }]

    await user.type(field(form, 'Message'), ' more')
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await sentNotice()
    expect(fake.delivered?.map((each) => each.text)).toEqual(['Hello more'])
  })

  it('refuses to send a save it cannot find yet, says so, and sends it once it can', async () => {
    const user = userEvent.setup()
    const fake = state()
    stubMailApi(fake)
    renderMailApp('/mail')
    const form = await ready(user)
    await within(form).findByText('Saved', {}, { timeout: 5_000 })
    fake.saveWithoutRef = true
    await user.type(field(form, 'Message'), ' more')
    await user.click(within(form).getByRole('button', { name: 'Send' }))

    const problem = await within(form).findByRole('alert', {}, { timeout: 6_000 })
    expect(problem).toHaveTextContent('still being saved')
    expect(fake.delivered).toBeUndefined()

    fake.unindexed = []
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await sentNotice()
    expect(fake.delivered?.map((each) => each.text)).toEqual(['Hello more'])
  })

  it('does not save a first version twice, nor send, before it can find it', async () => {
    const user = userEvent.setup()
    const fake = state({ saveWithoutRef: true })
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail')
    const form = await ready(user)
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    expect(await within(form).findByRole('alert', {}, { timeout: 6_000 })).toHaveTextContent('still being saved')

    const finds = queriesTo(fetchMock, '/api/v1/mail/drafts/find').length
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await waitFor(() => {
      expect(queriesTo(fetchMock, '/api/v1/mail/drafts/find').length).toBeGreaterThan(finds)
    })
    expect(await within(form).findByRole('alert')).toHaveTextContent('still being saved')
    expect(savesOf(fetchMock)).toHaveLength(1)
    expect(fake.delivered).toBeUndefined()
  })

  it('refuses early what is over the recipient limit, naming it', async () => {
    const user = userEvent.setup()
    const fake = state({ maxRecipients: 2 })
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail')
    const form = await compose(user)
    await user.type(field(form, 'To'), 'a@example.org{Enter}b@example.org{Enter}c@example.org{Enter}')
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('At most 2 recipients')
    expect(sendsOf(fetchMock)).toHaveLength(0)

    // Autosave meets the same limit on the server, and says the same rather than something vague.
    await within(form).findByText(/Not saved\..*At most 2 recipients/, {}, { timeout: 5_000 })
  })

  it('drops the recipient limit from what is wrong once the count is back within it', async () => {
    const user = userEvent.setup()
    stubMailApi(state({ maxRecipients: 2 }))
    renderMailApp('/mail')
    const form = await compose(user)
    await user.type(field(form, 'To'), 'a@example.org{Enter}b@example.org{Enter}c@example.org{Enter}')
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('At most 2 recipients')

    await user.click(within(form).getByRole('button', { name: 'Remove c@example.org' }))
    expect(within(form).queryByRole('alert')).not.toBeInTheDocument()
  })

  it('needs a recipient', async () => {
    const user = userEvent.setup()
    stubMailApi(state())
    renderMailApp('/mail')
    const form = await compose(user)
    const send = within(form).getByRole('button', { name: 'Send' })
    await user.click(send)
    expect(await within(form).findByRole('alert')).toHaveTextContent('Name at least one recipient.')
    expect(send).toHaveAccessibleDescription('Name at least one recipient.')
  })

  it('delivers once however often Send is pressed', async () => {
    const user = userEvent.setup()
    const fake = state({ sendingPolls: 1 })
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail')
    const form = await ready(user)
    const send = within(form).getByRole('button', { name: 'Send' })
    await user.dblClick(send)
    await user.click(send)
    await sentNotice()
    expect(fake.delivered).toHaveLength(1)
    expect(fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/send'))).toHaveLength(1)
  })

  it('does not send when the save before it fails', async () => {
    const user = userEvent.setup()
    const fake = state({ refuse: { 'POST /api/v1/mail/drafts': refusal(413, 'too_large') } })
    stubMailApi(fake)
    renderMailApp('/mail')
    const form = await ready(user)
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await within(form).findByText(/Not saved\..*over its limit/)
    expect(fake.delivered).toBeUndefined()
    expect(within(form).getByRole('button', { name: 'Send' })).not.toHaveAttribute('aria-disabled', 'true')
  })

  it('reports what a delivered send could not do', async () => {
    const user = userEvent.setup()
    stubMailApi(state({ sendOutcome: { state: 'sent', filed: false, rejected: ['bo@example.org'] }, filingPolls: 1 }))
    renderMailApp('/mail')
    const form = await ready(user)
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    const notice = await sentNotice(/^Sent\./)
    expect(notice).toHaveTextContent('could not be filed in Sent')
    expect(notice).toHaveTextContent('bo@example.org')
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByText(/^Sent\./, { selector: 'p' })).not.toBeInTheDocument()
  })

  it('words what followed a delivery from its codes, never the server\'s text', async () => {
    const user = userEvent.setup()
    stubMailApi(
      state({
        sendOutcome: {
          state: 'sent',
          notes: ['Server text'],
          note_codes: ['draft_not_removed', 'original_not_marked'],
        },
      }),
    )
    renderMailApp('/mail')
    const form = await ready(user)
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    const notice = await sentNotice(/^Sent\./)
    expect(notice).toHaveTextContent('could not be removed from Drafts')
    expect(notice).toHaveTextContent('could not be marked as answered')
    expect(notice).not.toHaveTextContent('Server text')
    expect(live('polite')).toHaveTextContent('could not be removed from Drafts')
  })

  it('says a failed send was not sent, keeps the draft, and can send again', async () => {
    const user = userEvent.setup()
    const fake = state({ sendOutcome: { state: 'failed', code: 'recipient_refused' } })
    stubMailApi(fake)
    renderMailApp('/mail')
    const form = await ready(user)
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    const failure = await within(form).findByText(/Not sent\. The message stayed in Drafts\./, {}, { timeout: 3_000 })
    expect(failure).toHaveTextContent('refused the message')
    expect(live('assertive')).toHaveTextContent('Not sent')
    expect(fake.drafts).toHaveLength(1)

    fake.sendOutcome = { state: 'sent' }
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await sentNotice()
    expect(fake.delivered).toHaveLength(1)
  })

  it('offers the three ways out of an unknown delivery', async () => {
    const user = userEvent.setup()
    const fake = state({ sendOutcome: { state: 'unknown' } })
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail')
    const form = await ready(user)
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await within(form).findByText("Can't confirm delivery. The message stayed in Drafts.", {}, { timeout: 3_000 })

    // Keep draft dismisses; a plain Send then brings the question back, it never resends.
    await user.click(within(form).getByRole('button', { name: 'Keep draft' }))
    expect(within(form).queryByText(/Can't confirm/)).not.toBeInTheDocument()
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await within(form).findByText(/Can't confirm/)
    expect(fake.delivered).toBeUndefined()

    // Send anyway says what it risks, and only then confirms.
    await user.click(within(form).getByRole('button', { name: 'Send anyway' }))
    const dialog = await screen.findByRole('dialog', { name: 'Send anyway' })
    expect(dialog).toHaveTextContent('arrive twice')
    fake.sendOutcome = { state: 'sent' }
    await user.click(within(dialog).getByRole('button', { name: 'Send anyway' }))
    await sentNotice()
    expect(fake.delivered).toHaveLength(1)
    const sends = fetchMock.mock.calls.filter(([path]) => String(path).endsWith('/send'))
    expect(sends.map(([, init]) => (init as RequestInit).body)).toEqual(['{}', '{}', '{"confirm_resend":true}'])
  })

  it('opens Sent from the unknown banner, leaving the draft waiting', async () => {
    const user = userEvent.setup()
    stubMailApi(state({ sendOutcome: { state: 'unknown' } }))
    const history = renderMailApp('/mail')
    const form = await ready(user)
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    const open = await within(form).findByRole('button', { name: 'Open Sent' }, { timeout: 3_000 })
    await user.click(open)
    await waitFor(() => {
      expect(history.location.search).toContain('folder=Sent')
    })
    await user.click(await screen.findByRole('button', { name: 'Back to draft' }))
    expect(within(await composer()).getByText(/Can't confirm/)).toBeInTheDocument()
  })

  it.each([
    [429, 'rate_limited', /hourly send limit/],
    [429, 'busy', /busy/],
    [409, 'send_disabled', /Sending is off/],
  ])('words a refused send: %s %s', async (status, code, text) => {
    const user = userEvent.setup()
    const fake = state()
    stubMailApi(fake)
    renderMailApp('/mail')
    const form = await ready(user)
    await user.click(within(form).getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(fake.drafts).toHaveLength(1)
    })
    const ref = fake.drafts?.[0]?.ref ?? ''
    fake.refuse = { [`POST /api/v1/mail/drafts/${ref}/send`]: refusal(status, code) }
    await user.click(await screen.findByRole('link', { name: 'Drafts' }))
    await user.click(await screen.findByRole('link', { name: /sender|\(no subject\)/ }))
    const reopened = await composer('Draft')
    await user.click(within(reopened).getByRole('button', { name: 'Send' }))
    expect(await within(reopened).findByText(text)).toBeInTheDocument()
    expect(fake.delivered).toBeUndefined()
  })

  it('recovers a lost save response on the way to sending', async () => {
    const user = userEvent.setup()
    let lost = false
    const fake = state({
      lose: (request) => {
        if (request === 'POST /api/v1/mail/drafts' && !lost) {
          lost = true
          return true
        }
        return false
      },
    })
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail')
    const form = await ready(user)
    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await sentNotice()
    // Once found, and once more because a found version is not known to hold this form.
    expect(savesOf(fetchMock)).toHaveLength(2)
    expect(fake.delivered).toHaveLength(1)
    expect(fake.drafts).toEqual([])
  })
})

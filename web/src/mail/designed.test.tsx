import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Finding } from '@/api/mail.ts'
import { fakeDraft, type FakeMailState, HOME, messageItem, messageView, savesOf, stubMailApi, WORK } from '@/test/fake-mail-api.ts'
import { renderMailApp } from '@/test/mail-app.tsx'

const FINDINGS: Finding[] = [
  { code: 'removed_scripts', text: '2 scripts or javascript: links were removed.', count: 2, items: [] },
  { code: 'image_without_alt', text: '1 image has no alt text.', count: 1, items: ['logo.png'] },
  { code: 'some_new_check', text: 'Something the server checks now.', count: 1, items: [] },
]

const designed = fakeDraft(WORK, 60, { subject: 'Spring news', designed: true, html: '<p>Spring</p>', parts: [{ name: 'terms.pdf', type: 'application/pdf', size: 10, part: '2' }] })
const item = messageItem(WORK, 'Drafts', 60, { ref: designed.ref, subject: designed.subject })

function state(overrides: Partial<FakeMailState> = {}): FakeMailState {
  return {
    accounts: [WORK, HOME],
    folders: {
      [WORK]: [
        { path: 'INBOX', role: 'inbox', unread: 0, total: 0 },
        { path: 'Sent', role: 'sent', unread: 0, total: 0 },
        { path: 'Drafts', role: 'drafts', unread: 0, total: 1 },
      ],
      [HOME]: [{ path: 'INBOX', role: 'inbox', unread: 0, total: 0 }],
    },
    messages: { '1/INBOX': [], '1/Sent': [], '1/Drafts': [item], '2/INBOX': [] },
    views: { [item.ref]: messageView(item) },
    drafts: [designed],
    findings: FINDINGS,
    identities: { [WORK]: [{ address: 'me@work.example', name: 'Me', default: true }] },
    ...overrides,
  }
}

const composer = (): Promise<HTMLElement> => screen.findByRole('form', { name: 'Draft' })
const field = (form: HTMLElement, name: string) => within(form).queryByRole('combobox', { name }) ?? within(form).getByRole('textbox', { name })

/** Each import's mode and its `draft` part, in order. */
function importsOf(fetchMock: ReturnType<typeof stubMailApi>): { mode: string | null; fields: Record<string, unknown>; form: FormData }[] {
  return fetchMock.mock.calls
    .map(([path, init]) => ({ url: new URL(path as string, 'http://mailroom.test'), init: init as RequestInit | undefined }))
    .filter(({ url, init }) => url.pathname === '/api/v1/mail/drafts' && init?.method === 'POST' && url.searchParams.has('import'))
    .map(({ url, init }) => {
      const form = init?.body as FormData
      return { mode: url.searchParams.get('import'), fields: JSON.parse(form.get('draft') as string) as Record<string, unknown>, form }
    })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('designed mail', () => {
  it('imports an .eml from the compose bar and opens it with its preview and checks', async () => {
    const user = userEvent.setup()
    const fake = state({ drafts: [] })
    const fetchMock = stubMailApi(fake)
    renderMailApp('/mail')
    await user.click(await screen.findByRole('button', { name: 'Import' }))
    const dialog = await screen.findByRole('dialog', { name: 'Import a designed message' })
    await user.click(within(dialog).getByRole('button', { name: 'Import' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Choose the file to import.')

    const eml = new File(['From: x@example.org\r\n\r\nHi'], 'news.eml', { type: 'message/rfc822' })
    await user.upload(within(dialog).getByLabelText('The .eml file'), eml)
    await user.click(within(dialog).getByRole('button', { name: 'Import' }))

    const form = await composer()
    const [imported] = importsOf(fetchMock)
    expect(imported?.mode).toBe('eml')
    expect(imported?.fields).toMatchObject({ account: WORK, ref: null, to: null, subject: null })
    expect((imported?.form.get('eml') as File).name).toBe('news.eml')
    expect(field(form, 'Subject')).toHaveValue('Imported')
    expect(within(form).getByText(/Imported as built/)).toBeInTheDocument()
    expect(within(form).queryByRole('textbox', { name: 'Message' })).not.toBeInTheDocument()

    const checks = within(form).getByRole('region', { name: 'Checks before sending' })
    expect(await within(checks).findByText('2 scripts or javascript: links were removed.')).toBeInTheDocument()
    expect(within(checks).getByText(/1 image has no alt text/)).toHaveTextContent('(logo.png)')
    expect(within(checks).getByText('Something the server checks now.')).toBeInTheDocument()
  })

  it('previews at a phone\'s width and in dark mode, through the sandboxed frame', async () => {
    const user = userEvent.setup()
    stubMailApi(state())
    renderMailApp(`/mail?folder=Drafts&compose=${encodeURIComponent(designed.ref)}`)
    const form = await composer()
    const frame = await within(form).findByTitle('Preview of “Spring news”')
    expect(frame).toHaveAttribute('sandbox', 'allow-popups allow-popups-to-escape-sandbox')
    expect(frame.getAttribute('src')).not.toContain('dark=1')

    await user.click(within(form).getByRole('button', { name: 'Phone' }))
    expect(within(form).getByRole('button', { name: 'Phone' })).toHaveAttribute('aria-pressed', 'true')
    expect(frame).toHaveStyle({ width: '375px' })
    await user.click(within(form).getByRole('button', { name: 'Dark' }))
    await waitFor(() => {
      expect(within(form).getByTitle('Preview of “Spring news”').getAttribute('src')).toContain('dark=1')
    })
  })

  it('sends a test to the default identity, the draft left as it is', async () => {
    const user = userEvent.setup()
    const fake = state()
    stubMailApi(fake)
    renderMailApp(`/mail?folder=Drafts&compose=${encodeURIComponent(designed.ref)}`)
    const form = await composer()
    await user.click(within(form).getByRole('button', { name: 'Send a test to me' }))
    expect(await within(form).findByText(/Test sent to me@work.example/, {}, { timeout: 5_000 })).toBeInTheDocument()
    expect(fake.tested).toEqual([designed.ref])
    expect(fake.drafts).toHaveLength(1)
    expect(fake.delivered ?? []).toEqual([])
  })

  it('says when the test was not sent, and asks again with a new test id on the next click', async () => {
    const user = userEvent.setup()
    const fake = state({ sendOutcome: { state: 'failed', code: 'recipient_refused' } })
    const fetchMock = stubMailApi(fake)
    renderMailApp(`/mail?folder=Drafts&compose=${encodeURIComponent(designed.ref)}`)
    const form = await composer()
    await user.click(within(form).getByRole('button', { name: 'Send a test to me' }))
    expect(await within(form).findByText(/^The test was not sent\. ./, {}, { timeout: 5_000 })).toHaveAttribute('role', 'alert')
    await user.click(within(form).getByRole('button', { name: 'Send a test to me' }))
    await waitFor(() => {
      expect(fake.tested).toHaveLength(2)
    })
    const ids = fetchMock.mock.calls
      .filter(([path]) => new URL(path as string, 'http://mailroom.test').pathname.endsWith('/test'))
      .map(([, init]) => (JSON.parse((init as RequestInit).body as string) as { test_id: string }).test_id)
    expect(ids).toHaveLength(2)
    expect(ids[0]).not.toBe(ids[1])
  })

  it('saves its recipients by keeping it as built, then sends it as any draft', async () => {
    const user = userEvent.setup()
    const fake = state()
    const fetchMock = stubMailApi(fake)
    renderMailApp(`/mail?folder=Drafts&compose=${encodeURIComponent(designed.ref)}`)
    const form = await composer()
    expect(within(form).getByText('terms.pdf')).toBeInTheDocument()
    expect(within(form).queryByLabelText('Attach')).not.toBeInTheDocument()
    expect(within(form).queryByRole('button', { name: /Remove/ })).not.toBeInTheDocument()
    await user.type(field(form, 'To'), 'ada@example.org{Enter}')
    await within(form).findByText('Saved', {}, { timeout: 5_000 })

    // Every save of it is a keep: none composes it again.
    expect(savesOf(fetchMock)).toHaveLength(importsOf(fetchMock).length)
    const [kept] = importsOf(fetchMock)
    expect(kept?.mode).toBe('kept')
    expect(kept?.fields).toMatchObject({ ref: designed.ref, to: [{ name: '', address: 'ada@example.org' }], subject: 'Spring news' })
    expect(fake.drafts?.[0]?.designed).toBe(true)

    await user.click(within(form).getByRole('button', { name: 'Send' }))
    await waitFor(
      () => {
        expect(Object.values(fake.scheduled ?? {}).length + (fake.delivered ?? []).length).toBe(1)
      },
      { timeout: 5_000 },
    )
  })

  it('replaces the message under the composer\'s fields, and says why an import was refused', async () => {
    const user = userEvent.setup()
    const fake = state()
    const fetchMock = stubMailApi(fake)
    renderMailApp(`/mail?folder=Drafts&compose=${encodeURIComponent(designed.ref)}`)
    const form = await composer()
    await user.click(within(form).getByRole('button', { name: 'Replace' }))
    const dialog = await screen.findByRole('dialog', { name: 'Replace the imported message' })
    await user.click(within(dialog).getByRole('radio', { name: /An HTML file/ }))
    await user.upload(within(dialog).getByLabelText('The HTML file'), new File(['<p>New</p>'], 'news.html', { type: 'text/html' }))
    await user.upload(within(dialog).getByLabelText('Its images and attachments'), new File(['png'], 'logo.png', { type: 'image/png' }))

    fake.importRefusal = 'invalid_argument'
    await user.click(within(dialog).getByRole('button', { name: 'Replace' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('That is not a message Mailroom can import')
    expect(fake.drafts).toEqual([designed])

    fake.importRefusal = undefined
    await user.click(within(dialog).getByRole('button', { name: 'Replace' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
    const replaced = importsOf(fetchMock).at(-1)
    expect(replaced?.mode).toBe('html')
    expect(replaced?.fields).toMatchObject({ ref: designed.ref, compose_id: designed.compose_id, subject: 'Spring news' })
    expect(replaced?.form.get('html')).toBe('<p>New</p>')
    expect((replaced?.form.getAll('files[]') as File[]).map((file) => file.name)).toEqual(['logo.png'])
    expect(fake.drafts).toHaveLength(1)
    expect(fake.drafts?.[0]?.ref).not.toBe(designed.ref)

    // The next save goes from the new version.
    const replacement = fake.drafts?.[0]?.ref
    await user.type(field(form, 'To'), 'ada@example.org{Enter}')
    await waitFor(
      () => {
        expect(importsOf(fetchMock).at(-1)?.fields).toMatchObject({ ref: replacement, to: [{ name: '', address: 'ada@example.org' }] })
      },
      { timeout: 5_000 },
    )
  })
})

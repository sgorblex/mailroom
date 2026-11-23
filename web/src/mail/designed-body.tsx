import { useCallback, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

import { checkMessageHtml, draftFindings, messageHtmlUrl, sendTest, type Finding } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { useLoad } from '@/lib/use-load.ts'
import { useUnmountSignal } from '@/lib/use-unmount-signal.ts'
import { cn } from '@/lib/utils.ts'

import { HTML_SANDBOX } from './html-body.tsx'
import { MAIL_NS } from './i18n.ts'
import { mailCodeMessage, mailErrorMessage } from './messages.ts'
import { followSend } from './send-flow.ts'

/** The width of a phone's mail app, in CSS pixels. */
const PHONE_WIDTH = 375

/** The findings this client words; any other reads as the server's English. */
const WORDED = new Set([
  'removed_scripts',
  'removed_forms',
  'removed_handlers',
  'removed_images',
  'removed_data_images',
  'removed_remote',
  'removed_styles',
  'text_derived',
  'image_without_alt',
  'cid_without_part',
  'part_not_referenced',
  'html_clipped',
  'remote_images',
  'no_text_part',
  'too_large',
])

function findingText(t: TFunction, finding: Finding): string {
  return WORDED.has(finding.code) ? t(`designed.finding.${finding.code}`, { count: finding.count }) : finding.text
}

type TestState =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'sent'; to: string }
  | { kind: 'failed'; text: string }
  | { kind: 'unknown' }

/**
 * The body of a draft imported as built, which is not edited here: its preview, at a phone's
 * width or the pane's, light or dark, through the read side's sandboxed frame; the checks to look
 * at before sending; a test to the user's own address; and Replace, to import it again. `saved`
 * saves what the composer holds first, From included, and answers the ref that holds it.
 */
export function DesignedBody({
  draftRef,
  subject,
  locked,
  saved,
  onReplace,
}: {
  draftRef: string
  subject: string
  locked: boolean
  saved: () => Promise<string | null>
  onReplace: () => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [phone, setPhone] = useState(false)
  const [dark, setDark] = useState(false)
  const [test, setTest] = useState<TestState>({ kind: 'idle' })
  const unmountSignal = useUnmountSignal()

  const check = useCallback(
    async (signal: AbortSignal) => {
      await checkMessageHtml(draftRef, false, signal)
    },
    [draftRef],
  )
  const preview = useLoad(check, (error) => mailErrorMessage(t, error, t('designed.previewFailed')))
  const loadFindings = useCallback((signal: AbortSignal) => draftFindings(draftRef, signal), [draftRef])
  const findings = useLoad(loadFindings, (error) => mailErrorMessage(t, error, t('designed.checksFailed')))

  async function sendToMe(): Promise<void> {
    if (test.kind === 'sending' || locked) {
      return
    }
    setTest({ kind: 'sending' })
    const signal = unmountSignal()
    try {
      const ref = await saved()
      if (ref === null) {
        setTest({ kind: 'failed', text: t('compose.stillSaving') })
        return
      }
      const started = await sendTest(ref, crypto.randomUUID())
      const end = await followSend(started.send_id, signal)
      if (signal.aborted) {
        return
      }
      switch (end.kind) {
        case 'sent':
          setTest({ kind: 'sent', to: started.to })
          return
        case 'failed':
          setTest({ kind: 'failed', text: end.code === null ? '' : mailCodeMessage(t, end.code) })
          return
        default:
          setTest({ kind: 'unknown' })
      }
    } catch (error) {
      if (!signal.aborted) {
        setTest({ kind: 'failed', text: mailErrorMessage(t, error, t('compose.notSent')) })
      }
    }
  }

  const choice = (pressed: boolean, label: string, choose: () => void): ReactElement => (
    <Button type="button" variant={pressed ? 'secondary' : 'ghost'} size="xs" aria-pressed={pressed} onClick={choose}>
      {label}
    </Button>
  )

  return (
    <section aria-label={t('compose.body')} className="flex min-h-48 flex-1 flex-col gap-2">
      <p className="rounded-lg border p-2 text-sm">{t('designed.bodyKept')}</p>
      <div className="flex flex-wrap items-center gap-2">
        <div role="group" aria-label={t('designed.width')} className="flex gap-1">
          {choice(!phone, t('designed.desktop'), () => {
            setPhone(false)
          })}
          {choice(phone, t('designed.phone'), () => {
            setPhone(true)
          })}
        </div>
        <div role="group" aria-label={t('designed.scheme')} className="flex gap-1">
          {choice(!dark, t('designed.light'), () => {
            setDark(false)
          })}
          {choice(dark, t('designed.dark'), () => {
            setDark(true)
          })}
        </div>
        <span className="flex-1" />
        <Button type="button" variant="outline" size="xs" aria-disabled={locked || test.kind === 'sending'} onClick={() => void sendToMe()}>
          {test.kind === 'sending' ? t('designed.testSending') : t('designed.sendTest')}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="xs"
          aria-disabled={locked}
          onClick={() => {
            if (!locked) {
              onReplace()
            }
          }}
        >
          {t('designed.replace')}
        </Button>
      </div>
      {test.kind === 'sent' && (
        <p role="status" className="text-sm">
          {t('designed.testSent', { to: test.to })}
        </p>
      )}
      {test.kind === 'failed' && (
        <p role="alert" className="text-sm text-destructive">
          {`${t('designed.testNotSent')} ${test.text}`.trim()}
        </p>
      )}
      {test.kind === 'unknown' && (
        <p role="alert" className="text-sm text-destructive">
          {t('designed.testUnknown')}
        </p>
      )}
      <section aria-label={t('designed.checks')} className="flex flex-col gap-1 rounded-lg border p-2 text-sm">
        <h3 className="font-medium">{t('designed.checks')}</h3>
        {findings.state.kind === 'loading' && <p className="text-muted-foreground">{t('designed.checksLoading')}</p>}
        {findings.state.kind === 'failed' && (
          <div className="flex items-center gap-2">
            <p role="alert">{findings.state.message}</p>
            <Button type="button" variant="outline" size="xs" onClick={findings.retry}>
              {t('auth.retry')}
            </Button>
          </div>
        )}
        {findings.state.kind === 'loaded' &&
          (findings.state.value.length === 0 ? (
            <p className="text-muted-foreground">{t('designed.noFindings')}</p>
          ) : (
            <ul className="list-disc pl-5">
              {findings.state.value.map((finding) => (
                <li key={finding.code}>
                  {findingText(t, finding)}
                  {finding.items.length > 0 && <span className="text-muted-foreground"> ({finding.items.join(', ')})</span>}
                </li>
              ))}
            </ul>
          ))}
      </section>
      {preview.state.kind === 'loading' && (
        <p role="status" className="text-sm text-muted-foreground">
          {t('designed.previewLoading')}
        </p>
      )}
      {preview.state.kind === 'failed' && (
        <div className="flex items-center gap-2">
          <p role="alert" className="text-sm">
            {preview.state.message}
          </p>
          <Button type="button" variant="outline" size="xs" onClick={preview.retry}>
            {t('auth.retry')}
          </Button>
        </div>
      )}
      {preview.state.kind === 'loaded' && (
        <iframe
          title={t('designed.previewTitle', { subject: subject === '' ? t('message.noSubject') : subject })}
          src={messageHtmlUrl(draftRef, false, dark)}
          sandbox={HTML_SANDBOX}
          referrerPolicy="no-referrer"
          style={phone ? { width: PHONE_WIDTH } : undefined}
          className={cn('min-h-[60vh] max-w-full flex-1 self-center rounded-md border', phone ? '' : 'w-full', dark ? 'bg-[#121212]' : 'bg-white')}
        />
      )}
    </section>
  )
}

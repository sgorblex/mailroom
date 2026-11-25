import { useEffect, useRef, useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

import type { AccountId } from '@/api/accounts.ts'
import { useAccountName } from '@/accounts/accounts-context.ts'
import { ApiRequestError } from '@/api/http.ts'
import { actOnMatching, BULK_MAX, countMatching, type BulkAction, type MatchScope } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { Dialog } from '@/components/ui/dialog.tsx'
import { useUnmountSignal } from '@/lib/use-unmount-signal.ts'

import { Buttons } from './folder-dialogs.tsx'
import { MAIL_NS } from './i18n.ts'
import { useToast } from './toast-context.ts'
import { folderName, mailCodeMessage, mailErrorMessage } from './messages.ts'
import { DoneMessage } from './toolbar.tsx'
import { failureKey, useTriage, type Failure, type Matching, type MatchingAction } from './triage-context.ts'

/**
 * Offered once every loaded row is selected and the list or search has more: counts what it
 * matches and offers to select them all, which the bulk actions then act on.
 */
export function SelectMatching({ loaded, scope }: { loaded: number; scope: MatchScope }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { matching, selectMatching, clearSelection } = useTriage()
  const [total, setTotal] = useState<number | 'counting' | 'failed'>('counting')

  useEffect(() => {
    const controller = new AbortController()
    countMatching(scope, null, controller.signal).then(
      (answer) => {
        setTotal(answer.total)
      },
      () => {
        if (!controller.signal.aborted) {
          setTotal('failed')
        }
      },
    )
    return () => {
      controller.abort()
    }
  }, [scope])

  return (
    <div role="status" className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg bg-muted px-3 py-2 text-sm">
      {matching === null ? (
        <>
          <span>{t('matching.loadedSelected', { count: loaded })}</span>
          {total === 'counting' && <span className="text-muted-foreground">{t('matching.counting')}</span>}
          {total === 'failed' && <span className="text-muted-foreground">{t('matching.countFailed')}</span>}
          {typeof total === 'number' && total > BULK_MAX && <span>{t('matching.tooMany', { count: total })}</span>}
          {typeof total === 'number' && total > loaded && total <= BULK_MAX && (
            <Button
              variant="link"
              size="sm"
              className="h-auto p-0"
              onClick={() => {
                selectMatching({ scope, total })
              }}
            >
              {t('matching.selectAll', { count: total })}
            </Button>
          )}
        </>
      ) : (
        <>
          <span>{t('matching.allSelected', { count: matching.total })}</span>
          <Button variant="link" size="sm" className="h-auto p-0" onClick={clearSelection}>
            {t('matching.clear')}
          </Button>
        </>
      )}
    </div>
  )
}

/** The bulk action waiting for its count to be confirmed, or running. */
export function BulkHost(): ReactElement | null {
  const { bulk, matching } = useTriage()
  return bulk === null || matching === null ? null : <BulkDialog matching={matching} action={bulk} />
}

/** The request body of a bulk action. */
function bulkAction(action: MatchingAction): BulkAction {
  switch (action.kind) {
    case 'flag':
      return { kind: 'flag', ...action.change }
    case 'move':
      return { kind: 'move', folder: action.folder.path }
    case 'trash':
      return { kind: 'trash' }
  }
}

/** The action's name, as its button says it. */
function actionName(t: TFunction, action: MatchingAction): string {
  switch (action.kind) {
    case 'trash':
      return t('triage.trash')
    case 'move':
      return t('matching.moveTo', { folder: folderName(t, action.folder) })
    case 'flag': {
      const { flagged, handled: handled, seen } = action.change
      if (flagged !== undefined) {
        return t(flagged ? 'triage.star' : 'triage.unstar')
      }
      if (handled !== undefined) {
        return t(handled ? 'triage.markHandled' : 'triage.unmarkHandled')
      }
      return t(seen === true ? 'triage.markRead' : 'triage.markUnread')
    }
  }
}

type BulkState =
  | { kind: 'counting' }
  /** `changed`: a call was refused because the count was no longer the one confirmed. */
  | { kind: 'ready'; count: number; changed: boolean }
  | { kind: 'running'; total: number; stopping: boolean }
  | { kind: 'ended'; stopped: boolean; message: string | null }

/**
 * A bulk action on every matching message: counts what it would change, asks to confirm that
 * count, then calls the bulk route again with what remains until nothing does, showing how far it
 * got, with a Stop between calls. Refusals are worded: a count that changed asks again with the
 * new one; past `BULK_MAX`, nothing is done. At the end the list and the folders are read again.
 */
function BulkDialog({ matching, action }: { matching: Matching; action: MatchingAction }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const accountName = useAccountName()
  const { bulkDone, setBulk } = useTriage()
  const toast = useToast()
  const unmountSignal = useUnmountSignal()
  const [state, setState] = useState<BulkState>({ kind: 'counting' })
  const [done, setDone] = useState(0)
  const [failures, setFailures] = useState<Failure[]>([])
  const stop = useRef(false)
  // What was done and touched, for the async loop to read after an await.
  const doneRef = useRef(0)
  const touched = useRef(new Set<AccountId>())
  const cancelRef = useRef<HTMLButtonElement>(null)
  const name = actionName(t, action)
  const { scope } = matching

  useEffect(() => {
    const controller = new AbortController()
    countMatching(scope, bulkAction(action), controller.signal).then(
      (answer) => {
        setState({ kind: 'ready', count: answer.total, changed: false })
      },
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setState({ kind: 'ended', stopped: false, message: mailErrorMessage(t, error, t('matching.countFailed')) })
        }
      },
    )
    return () => {
      controller.abort()
    }
  }, [scope, action, t])

  // Read through a call: the loop sets it to false before awaiting what Stop changes.
  const stopAsked = (): boolean => stop.current

  // A changed count is a new question: Enter again must not answer it unread.
  useEffect(() => {
    if (state.kind === 'ready' && state.changed) {
      cancelRef.current?.focus()
    }
  }, [state])

  /** Ends the action: once anything was done, the list and the folders are read again. */
  function close(): void {
    if (doneRef.current > 0) {
      bulkDone([...touched.current])
    } else {
      setBulk(null)
    }
  }

  async function runFrom(expected: number): Promise<void> {
    stop.current = false
    setState({ kind: 'running', total: doneRef.current + expected, stopping: false })
    const signal = unmountSignal()
    const body = bulkAction(action)
    let remaining = expected
    const failed: Failure[] = []
    while (remaining > 0) {
      if (stopAsked()) {
        setState({ kind: 'ended', stopped: true, message: null })
        return
      }
      let answer
      try {
        answer = await actOnMatching(scope, body, remaining, signal)
      } catch (error) {
        if (signal.aborted) {
          return
        }
        const code = error instanceof ApiRequestError ? error.code : undefined
        const now = error instanceof ApiRequestError ? error.field('matching') : undefined
        if (code === 'count_changed' && typeof now === 'number') {
          setState({ kind: 'ready', count: now, changed: true })
        } else {
          setState({
            kind: 'ended',
            stopped: false,
            message:
              code === 'too_many' && typeof now === 'number'
                ? t('matching.tooMany', { count: now })
                : mailErrorMessage(t, error, t('triage.failed')),
          })
        }
        return
      }
      doneRef.current += answer.done
      setDone(doneRef.current)
      for (const group of answer.results) {
        touched.current.add(group.account)
        if (!group.ok || group.error !== null) {
          failed.push({ account: group.account, folder: group.folder, code: group.error?.code ?? 'server_error' })
        }
      }
      for (const failure of answer.failures) {
        failed.push({ account: failure.account_id, folder: failure.folder ?? '', code: failure.code })
      }
      remaining = answer.remaining
      // A folder that failed, or a call that did nothing, would fail again: stop there. A source
      // that could not be searched is left out of the count, and the others go on.
      if (answer.done === 0 || answer.results.some((group) => !group.ok || group.error !== null)) {
        break
      }
    }
    const unique = [...new Map(failed.map((failure) => [failureKey(failure), failure])).values()]
    if (remaining === 0 && unique.length === 0) {
      toast.show({ message: <DoneMessage action={action} count={doneRef.current} /> })
      close()
      return
    }
    setFailures(unique)
    setState({ kind: 'ended', stopped: false, message: remaining === 0 ? null : t('matching.notAll', { count: remaining }) })
  }

  function cancel(): void {
    if (state.kind === 'running') {
      stop.current = true
      setState({ ...state, stopping: true })
    } else {
      close()
    }
  }

  function submit(event: SubmitEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (state.kind === 'ready' && state.count > 0 && state.count <= BULK_MAX) {
      void runFrom(state.count)
    } else if (state.kind === 'ended') {
      close()
    }
  }

  const title = t('matching.title')
  const ready = state.kind === 'ready' && state.count > 0 && state.count <= BULK_MAX
  return (
    <Dialog label={title} onCancel={cancel}>
      <form noValidate onSubmit={submit} className="flex w-96 max-w-full flex-col gap-3">
        <h2 className="text-base font-medium">{title}</h2>
        {state.kind === 'counting' && (
          <p role="status" className="text-sm text-muted-foreground">
            {t('matching.counting')}
          </p>
        )}
        {state.kind === 'ready' && state.changed && (
          <p role="alert" className="text-sm font-medium">
            {t('matching.changed', { count: state.count })}
          </p>
        )}
        {state.kind === 'ready' && (
          <p className="text-sm">
            {state.count === 0
              ? t('matching.none')
              : state.count > BULK_MAX
                ? t('matching.tooMany', { count: state.count })
                : t('matching.question', { count: state.count, action: name })}
          </p>
        )}
        {state.kind === 'running' && (
          <>
            <progress max={state.total} value={done} aria-label={title} className="w-full accent-primary" />
            <p role="status" className="text-sm">
              {state.stopping ? t('matching.stopping') : t('matching.progress', { count: state.total, done })}
            </p>
          </>
        )}
        {state.kind === 'ended' && (
          <div role="alert" className="flex flex-col gap-1 text-sm">
            {done > 0 && (
              <p>
                <DoneMessage action={action} count={done} />
              </p>
            )}
            {state.stopped && <p>{t('matching.stopped')}</p>}
            {state.message !== null && <p>{state.message}</p>}
            {failures.length > 0 && (
              <>
                <p className="font-medium">{t('triage.partial')}</p>
                <ul className="flex flex-col gap-0.5">
                  {failures.map((failure) => (
                    <li key={failureKey(failure)}>
                      {failure.folder === ''
                        ? t('list.failedAccount', { account: accountName(failure.account), reason: mailCodeMessage(t, failure.code) })
                        : t('list.failedFolder', {
                            account: accountName(failure.account),
                            folder: failure.folder,
                            reason: mailCodeMessage(t, failure.code),
                          })}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
        {state.kind === 'running' ? (
          <div className="mt-4 flex justify-end">
            <Button type="button" variant="ghost" size="sm" disabled={state.stopping} onClick={cancel}>
              {t('matching.stop')}
            </Button>
          </div>
        ) : state.kind === 'ended' ? (
          <div className="mt-4 flex justify-end">
            <Button type="submit" size="sm">
              {t('matching.close')}
            </Button>
          </div>
        ) : (
          <Buttons
            cancel={t('triage.cancel')}
            confirm={ready ? name : null}
            disabled={false}
            destructive={action.kind === 'trash'}
            cancelRef={cancelRef}
            onCancel={cancel}
          />
        )}
      </form>
    </Dialog>
  )
}

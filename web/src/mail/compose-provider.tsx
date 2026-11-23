import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { flushSync } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearch } from '@tanstack/react-router'

import type { AccountId, Account } from '@/api/accounts.ts'
import { ApiRequestError } from '@/api/http.ts'
import { cancelScheduled, type MailAddress, type ScheduledSend } from '@/api/mail.ts'
import { useTimeZone, useUndoSendSeconds } from '@/auth/auth-context.ts'
import { formatDateTime } from '@/lib/format.ts'

import { ComposeContext, type Announcement, type Compose, type EditorHandle, type HeldNotice, type SendNotice } from './compose-context.ts'
import { composeKey, sourceFromParam, type ComposeSession, type ComposeSource } from './compose-session.ts'
import { MAIL_NS } from './i18n.ts'
import { sentMessage } from './messages.ts'
import { SCHEDULED_KEY } from './queries.ts'
import { followHeld } from './send-flow.ts'
import { useTriage } from './triage-context.ts'

/** In `shownOver`, which no message equals: the editor waits behind whatever the pane shows. */
const BEHIND = Symbol('behind')

/**
 * The one composer of the mail screen. It sits above the panes: what a sidebar or list link
 * changes is the URL, never the session, so moving around cannot lose the editor. Opening a
 * message puts the editor behind it, mounted, until the user comes back to it.
 */
export function ComposeProvider({
  accounts,
  accountId,
  children,
}: {
  accounts: Account[]
  accountId: AccountId
  children: ReactNode
}): ReactElement {
  const { t, i18n } = useTranslation(MAIL_NS)
  const search = useSearch({ from: '/mail' })
  const undoSeconds = useUndoSendSeconds()
  const timeZone = useTimeZone()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { loadFolders } = useTriage()
  const [session, setSession] = useState<ComposeSession | null>(null)
  // Known once the editor has loaded: what the URL names it by, and whose draft it is.
  const [named, setNamed] = useState<{ key: string; account: AccountId } | null>(null)
  const [shownOver, setShownOver] = useState<string | undefined | typeof BEHIND>(BEHIND)
  const [revision, setRevision] = useState(0)
  const [notice, setNotice] = useState<SendNotice | null>(null)
  const [announcement, setAnnouncement] = useState<Announcement | null>(null)
  const [held, setHeld] = useState<HeldNotice[]>([])
  const [scheduled, setScheduled] = useState<ScheduledSend | null>(null)
  const handle = useRef<EditorHandle | null>(null)
  const nextId = useRef(1)
  const starting = useRef(false)
  // The `compose` param of the session before, until the URL has dropped it.
  const closed = useRef<string | null>(null)
  const key = named?.key ?? null
  const latest = useRef({ session, named, message: search.message })
  useEffect(() => {
    latest.current = { session, named, message: search.message }
  })

  const reveal = useCallback(() => {
    setShownOver(latest.current.message)
  }, [])

  const hide = useCallback(() => {
    setShownOver(BEHIND)
  }, [])

  const backToDraft = useCallback(() => {
    // Nothing can take focus in the hidden pane, so the pane must be shown by now.
    flushSync(reveal)
    handle.current?.focus()
  }, [reveal])

  const dropParam = useCallback(() => {
    void navigate({ to: '/mail', search: (previous) => ({ ...previous, compose: undefined }), replace: true })
  }, [navigate])

  const open = useCallback(async (source: ComposeSource, param: string | null) => {
    if (starting.current) {
      return
    }
    if (source.kind === 'draft' && handle.current?.ref() === source.ref) {
      reveal()
      return
    }
    starting.current = true
    try {
      if (handle.current !== null && !(await handle.current.leave())) {
        reveal()
        return
      }
      const { named: before, session: previous } = latest.current
      closed.current = before?.key ?? previous?.param ?? closed.current
      const id = nextId.current
      nextId.current += 1
      setNamed(null)
      setSession({ id, composeId: crypto.randomUUID(), source, param })
      reveal()
    } finally {
      starting.current = false
    }
  }, [reveal])

  const start = useCallback(
    (source: ComposeSource) => {
      void open(source, null)
    },
    [open],
  )

  const mailChanged = useCallback(() => {
    setRevision((value) => value + 1)
  }, [])

  const close = useCallback(() => {
    const { named: current, session: previous } = latest.current
    closed.current = current?.key ?? previous?.param ?? closed.current
    setSession(null)
    setNamed(null)
    setShownOver(BEHIND)
    dropParam()
    mailChanged()
    if (current !== null) {
      loadFolders(current.account, true)
    }
  }, [dropParam, loadFolders, mailChanged])

  const identified = useCallback((composeId: string, account: AccountId) => {
    setNamed({ key: composeKey(composeId, account), account })
  }, [])

  const attach = useCallback((editor: EditorHandle) => {
    handle.current = editor
    return () => {
      if (handle.current === editor) {
        handle.current = null
      }
    }
  }, [])

  const finishSent = useCallback(
    (sent: SendNotice) => {
      setNotice(sent)
      close()
    },
    [close],
  )

  const dismissNotice = useCallback(() => {
    setNotice(null)
  }, [])

  // Each held send is followed from here, as its composer has closed: Undo can stop it until the worker takes it.
  const followers = useRef(new Map<string, AbortController>())
  useEffect(() => {
    const running = followers.current
    return () => {
      for (const controller of running.values()) {
        controller.abort()
      }
      running.clear()
    }
  }, [])
  const stopFollowing = useCallback((id: string) => {
    followers.current.get(id)?.abort()
    followers.current.delete(id)
  }, [])
  const changeHeld = useCallback((id: string, change: (notice: HeldNotice) => HeldNotice | null) => {
    setHeld((list) =>
      list.flatMap((notice) => {
        if (notice.send.scheduled_id !== id) {
          return [notice]
        }
        const changed = change(notice)
        return changed === null ? [] : [changed]
      }),
    )
  }, [])

  const undoMs = undoSeconds * 1_000
  const holdSent = useCallback(
    (send: ScheduledSend, composeId: string, to: MailAddress[]) => {
      const id = send.scheduled_id
      stopFollowing(id)
      const controller = new AbortController()
      followers.current.set(id, controller)
      setNotice(null)
      // What is left of the hold by the server's clock, not the time since its answer arrived.
      const holdMs = Math.min(undoMs, send.remaining_ms)
      const whom = to.length > 0 ? to : send.recipients.map((address) => ({ name: '', address }))
      // Its toast says so, as it says how it ends but when sent.
      setHeld((list) => [
        ...list.filter((notice) => notice.send.scheduled_id !== id),
        { send, composeId, to: whom, until: Date.now() + holdMs, holdMs, phase: 'undoable' },
      ])
      close()
      const past = setTimeout(() => {
        changeHeld(id, (notice) => (notice.phase === 'undoable' ? { ...notice, phase: 'sending' } : notice))
      }, holdMs)
      controller.signal.addEventListener('abort', () => {
        clearTimeout(past)
      })
      void followHeld(send, composeId, holdMs, controller.signal).then((end) => {
        if (controller.signal.aborted) {
          return
        }
        followers.current.delete(id)
        clearTimeout(past)
        if (end.kind === 'sent') {
          const { filed, rejected, note_codes } = end.result
          changeHeld(id, () => null)
          setNotice({ filed, rejected, note_codes })
          setAnnouncement({ text: sentMessage(t, { filed, rejected, note_codes }), urgent: false })
        } else {
          changeHeld(id, (notice) => ({ ...notice, phase: 'ended', end }))
        }
        mailChanged()
      })
    },
    [changeHeld, close, mailChanged, stopFollowing, t, undoMs],
  )

  const heldNow = useRef(held)
  useEffect(() => {
    heldNow.current = held
  })
  const undoSend = useCallback(
    (id: string) => {
      const current = heldNow.current.find((notice) => notice.send.scheduled_id === id)
      if (current?.phase !== 'undoable') {
        return
      }
      changeHeld(id, (notice) => ({ ...notice, phase: 'undoing' }))
      cancelScheduled(id).then(
        () => {
          stopFollowing(id)
          changeHeld(id, () => null)
          setAnnouncement({ text: t('compose.undone'), urgent: false })
          // Nothing is held now: the Scheduled count and the lists read again.
          mailChanged()
          void open({ kind: 'draft', ref: current.send.ref }, null)
        },
        (error: unknown) => {
          // Taken by the worker, or gone with it: what it comes to is followed still.
          const late = error instanceof ApiRequestError && (error.code === 'send_started' || error.code === 'not_found')
          changeHeld(id, (notice) => (notice.phase === 'undoing' ? { ...notice, phase: late ? 'tooLate' : 'undoable' } : notice))
          if (!late) {
            setAnnouncement({ text: t('compose.undoFailed'), urgent: true })
          }
        },
      )
    },
    [changeHeld, mailChanged, open, stopFollowing, t],
  )

  const dismissHeld = useCallback(
    (id: string) => {
      changeHeld(id, () => null)
    },
    [changeHeld],
  )

  const finishScheduled = useCallback(
    (send: ScheduledSend) => {
      setNotice(null)
      setScheduled(send)
      setAnnouncement({ text: t('compose.scheduledFor', { when: formatDateTime(i18n.language, send.send_at, timeZone) }), urgent: false })
      void queryClient.invalidateQueries({ queryKey: SCHEDULED_KEY })
      close()
    },
    [close, i18n.language, queryClient, t, timeZone],
  )

  const dismissScheduled = useCallback(() => {
    setScheduled(null)
  }, [])

  // A param opens a composer: a draft from the list, or the one open before a reload.
  const param = search.compose
  useEffect(() => {
    if (param === undefined) {
      closed.current = null
    } else if (param !== key && param !== session?.param && param !== closed.current) {
      void open(sourceFromParam(param, accountId), param)
    }
  }, [param, key, session?.param, accountId, open])

  // An open composer stays in the URL, whatever a link left out: a reload brings it back.
  useEffect(() => {
    if (key !== null && param !== key) {
      void navigate({ to: '/mail', search: (previous) => ({ ...previous, compose: key }), replace: true })
    }
  }, [key, param, navigate])

  // A boolean, not the message: opening another one leaves the value as it was.
  const shown = session !== null && shownOver === search.message
  const value = useMemo<Compose>(
    () => ({
      session,
      shown,
      start,
      close,
      reveal,
      backToDraft,
      hide,
      account: (id) => accounts.find((each) => each.id === id),
      canCompose: (id) => {
        const found = accounts.find((each) => each.id === id)
        return found !== undefined && found.send_mode !== 'off'
      },
      attach,
      identified,
      mailChanged,
      revision,
      finishSent,
      notice,
      dismissNotice,
      holdSent,
      held,
      undoSend,
      dismissHeld,
      finishScheduled,
      scheduled,
      dismissScheduled,
      announcement,
      announce: setAnnouncement,
    }),
    [
      session,
      shown,
      start,
      close,
      reveal,
      backToDraft,
      hide,
      accounts,
      attach,
      identified,
      mailChanged,
      revision,
      finishSent,
      notice,
      dismissNotice,
      holdSent,
      held,
      undoSend,
      dismissHeld,
      finishScheduled,
      scheduled,
      dismissScheduled,
      announcement,
    ],
  )
  return <ComposeContext value={value}>{children}</ComposeContext>
}

import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { flushSync } from 'react-dom'
import { useNavigate, useSearch } from '@tanstack/react-router'

import type { Account } from '@/api/accounts.ts'

import { ComposeContext, type Announcement, type Compose, type EditorHandle, type SendNotice } from './compose-context.ts'
import { composeKey, sourceFromParam, type ComposeSession, type ComposeSource } from './compose-session.ts'
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
  accountId: string
  children: ReactNode
}): ReactElement {
  const search = useSearch({ from: '/mail' })
  const navigate = useNavigate()
  const { loadFolders } = useTriage()
  const [session, setSession] = useState<ComposeSession | null>(null)
  // Known once the editor has loaded: what the URL names it by, and whose draft it is.
  const [named, setNamed] = useState<{ key: string; account: string } | null>(null)
  const [shownOver, setShownOver] = useState<string | undefined | typeof BEHIND>(BEHIND)
  const [revision, setRevision] = useState(0)
  const [notice, setNotice] = useState<SendNotice | null>(null)
  const [announcement, setAnnouncement] = useState<Announcement | null>(null)
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

  const identified = useCallback((composeId: string, account: string) => {
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

  const value = useMemo<Compose>(
    () => ({
      session,
      shown: session !== null && shownOver === search.message,
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
      announcement,
      announce: setAnnouncement,
    }),
    [
      session,
      shownOver,
      search.message,
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
      announcement,
    ],
  )
  return <ComposeContext value={value}>{children}</ComposeContext>
}

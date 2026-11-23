import { useEffect, useLayoutEffect, useRef, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { fetchVersion } from '@/api/health.ts'
import { bannerEnv, showStagingHead } from '@/lib/deploy-env.ts'

/** A bar over every page, signed in or not, on an instance that is not production, and the
 * environment before the tab's title, with staging's favicon, manifest and app title in the head
 * (an install there is "Mailroom staging", its icon banded in the banner's amber). Read once from
 * the API, so one image serves every environment; production, and an API that does not answer,
 * keep index.html's. It stays at the top of the window as the page scrolls, and its height is
 * `--deploy-banner` (0 without it), which whatever else sticks to the top sits under. */
export function DeployBanner(): ReactElement | null {
  const { t } = useTranslation()
  const [env, setEnv] = useState<string | null>(null)

  useEffect(() => {
    const abort = new AbortController()
    fetchVersion(abort.signal).then(
      (version) => { setEnv(bannerEnv(version.deploy_env)); },
      () => undefined,
    )
    return () => { abort.abort(); }
  }, [])

  useEffect(() => (env === null ? undefined : showStagingHead()), [env])

  const label = env === null ? null : env.toUpperCase()
  useEffect(() => {
    if (label === null) return
    const before = document.title
    document.title = `[${label}] ${before}`
    return () => {
      document.title = before
    }
  }, [label])

  // Measured, not assumed: a narrow phone wraps the text onto two lines.
  const bar = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const element = bar.current
    if (element === null) return
    const root = document.documentElement
    const measure = (): void => {
      root.style.setProperty('--deploy-banner', `${String(element.offsetHeight)}px`)
    }
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(element)
    return () => {
      observer?.disconnect()
      root.style.removeProperty('--deploy-banner')
    }
  }, [label])

  if (label === null) return null
  // Above the shell's header and the mail panes (z-30), below menus, toasts (z-50) and dialogs
  // (the top layer); over the notch or status bar when the page reaches under it.
  return (
    <div
      ref={bar}
      role="note"
      className="sticky top-0 z-40 bg-amber-500 px-3 pt-[calc(0.125rem+env(safe-area-inset-top))] pb-0.5 text-center text-xs font-semibold tracking-wide text-black"
    >
      {t('deployBanner.text', { env: label })}
    </div>
  )
}

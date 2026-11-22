import { useEffect, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { fetchVersion } from '@/api/health.ts'
import { bannerEnv } from '@/lib/deploy-env.ts'

/** A bar over every page, signed in or not, on an instance that is not production, and the
 * environment before the tab's title. Read once from the API, so one image serves every
 * environment; an API that does not answer shows nothing. */
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

  const label = env === null ? null : env.toUpperCase()
  useEffect(() => {
    if (label === null) return
    const before = document.title
    document.title = `[${label}] ${before}`
    return () => {
      document.title = before
    }
  }, [label])

  if (label === null) return null
  return (
    <div
      role="note"
      className="bg-amber-500 px-3 py-0.5 text-center text-xs font-semibold tracking-wide text-black"
    >
      {t('deployBanner.text', { env: label })}
    </div>
  )
}

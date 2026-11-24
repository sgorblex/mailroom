const QUIET_ENVS = new Set(['production', 'development', ''])

/** The environment's name, when it is one a user could mistake for production (staging, say);
 * null for production and development, and while the API has not said. */
export function bannerEnv(deployEnv: string | undefined): string | null {
  const env = (deployEnv ?? '').trim().toLowerCase()
  return QUIET_ENVS.has(env) ? null : env
}

/** What the page's head names for an instance that is not production: the staging manifest
 * (its own name and icons), favicon, apple-touch-icon and home-screen title. */
const STAGING_HEAD: readonly (readonly [selector: string, attribute: string, value: string])[] = [
  ['link[rel="manifest"]', 'href', '/manifest-staging.webmanifest'],
  ['link[rel="icon"]', 'href', '/icon-staging.svg'],
  ['link[rel="apple-touch-icon"]', 'href', '/apple-touch-icon-staging.png'],
  ['meta[name="apple-mobile-web-app-title"]', 'content', 'Mailroom staging'],
  ['meta[name="application-name"]', 'content', 'Mailroom staging'],
]

/** Points the head's manifest, icons and app title at staging's, so an install or a tab there is
 * told apart from production's; index.html's are production's, so nothing changes until the API
 * names another environment. Returns what puts production's back. */
export function showStagingHead(): () => void {
  const restore: (() => void)[] = []
  for (const [selector, attribute, value] of STAGING_HEAD) {
    const element = document.head.querySelector(selector)
    const before = element?.getAttribute(attribute)
    if (element == null || before == null) continue
    element.setAttribute(attribute, value)
    restore.push(() => { element.setAttribute(attribute, before) })
  }
  return () => { for (const undo of restore) undo() }
}

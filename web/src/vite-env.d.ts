/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Web app SemVer from package.json at build time. */
  readonly VITE_APP_SEMVER: string
  /** Git commit full SHA (CI) or from repo at dev build; may be empty. */
  readonly VITE_GIT_SHA: string
  /** Git branch (CI/staging build) or current branch at dev build; may be empty. */
  readonly VITE_GIT_BRANCH: string
  /** `staging` | `development` | `production`, or empty. */
  readonly VITE_MAILROOM_DEPLOY_ENV: string
  /** Unset: same-origin `/api` (Vite proxy in dev, nginx in the image). */
  readonly VITE_API_BASE_URL?: string
}

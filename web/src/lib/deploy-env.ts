const QUIET_ENVS = new Set(['production', 'development', ''])

/** The environment's name, when it is one a user could mistake for production (staging, say);
 * null for production and development, and while the API has not said. */
export function bannerEnv(deployEnv: string | undefined): string | null {
  const env = (deployEnv ?? '').trim().toLowerCase()
  return QUIET_ENVS.has(env) ? null : env
}

import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

/** In place of a screen whose feature the instance does not run. */
export function NotAvailable(): ReactElement {
  const { t } = useTranslation()
  return (
    <main className="mx-auto flex w-full max-w-xl flex-1 flex-col gap-3 p-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t('notAvailable.title')}</h1>
      <p className="text-muted-foreground">{t('notAvailable.body')}</p>
    </main>
  )
}

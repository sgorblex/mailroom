import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { RefreshCw } from 'lucide-react'

import { Button } from '@/components/ui/button.tsx'
import { cn } from '@/lib/utils.ts'

import { MAIL_NS } from './i18n.ts'
import { useRefresh } from './refresh-context.ts'

/** Refreshes the list it stands above (`RefreshProvider`); it turns, and takes no click, meanwhile. */
export function RefreshButton(): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const { busy, refresh } = useRefresh()
  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="ml-auto shrink-0"
        aria-label={t('refresh.label')}
        title={`${t('refresh.label')} (${t('shortcuts.refreshKey')})`}
        aria-keyshortcuts="Shift+R"
        aria-disabled={busy}
        onClick={refresh}
      >
        <RefreshCw aria-hidden="true" className={cn(busy && 'animate-spin motion-reduce:animate-none motion-reduce:opacity-50')} />
      </Button>
      <span role="status" className="sr-only">
        {busy ? t('refresh.busy') : ''}
      </span>
    </>
  )
}

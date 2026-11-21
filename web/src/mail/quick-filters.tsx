import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from '@tanstack/react-router'
import { Circle, Paperclip, Star, type LucideIcon } from 'lucide-react'

import { Button } from '@/components/ui/button.tsx'

import { MAIL_NS } from './i18n.ts'
import { QUICK_FILTERS, type QuickFilter } from './route-search.ts'

const ICONS: Record<QuickFilter, LucideIcon> = { unread: Circle, flagged: Star, has_attachment: Paperclip }

/**
 * Unread, Starred and With attachments as toggles above the list: each narrows the folder or
 * search shown as its search term would, kept in the URL (`only`) until another source is opened.
 */
export function QuickFilters({ only }: { only: readonly QuickFilter[] | undefined }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const navigate = useNavigate()
  const on = new Set(only)

  function toggle(filter: QuickFilter): void {
    const next = QUICK_FILTERS.filter((each) => (each === filter ? !on.has(each) : on.has(each)))
    void navigate({ to: '/mail', search: (previous) => ({ ...previous, only: next.length > 0 ? next : undefined }) })
  }

  return (
    <div role="group" aria-label={t('quick.label')} className="flex flex-wrap gap-1">
      {QUICK_FILTERS.map((filter) => {
        const Icon = ICONS[filter]
        const pressed = on.has(filter)
        return (
          <Button
            key={filter}
            type="button"
            variant={pressed ? 'secondary' : 'outline'}
            size="xs"
            aria-pressed={pressed}
            onClick={() => {
              toggle(filter)
            }}
          >
            <Icon aria-hidden="true" className={pressed && filter !== 'has_attachment' ? 'fill-current' : undefined} />
            {t(`search.${filter}`)}
          </Button>
        )
      })}
    </div>
  )
}

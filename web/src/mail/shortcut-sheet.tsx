import { type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'
import { Keyboard } from 'lucide-react'

import { Button } from '@/components/ui/button.tsx'
import { Dialog } from '@/components/ui/dialog.tsx'

import { MAIL_NS } from './i18n.ts'
import { SHEET_GROUPS } from './shortcuts.ts'

/** Every keyboard shortcut of the mail screen, grouped, in a modal. */
export function ShortcutSheet({ onClose }: { onClose: () => void }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  return (
    <Dialog label={t('shortcuts.title')} onCancel={onClose}>
      <h2 className="text-base font-medium">{t('shortcuts.title')}</h2>
      {SHEET_GROUPS.map((group) => (
        <section key={group.title} className="mt-3">
          <h3 className="text-sm font-medium text-muted-foreground">{t(group.title)}</h3>
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            {group.entries.map((entry) => (
              <div key={entry.key} className="contents">
                <dt>
                  <kbd className="rounded border bg-muted px-1.5 font-mono text-xs">
                    {entry.key === 'Escape' ? t('shortcuts.escapeKey') : entry.key}
                  </kbd>
                </dt>
                <dd>{t(entry.label)}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
      <div className="mt-4 flex justify-end">
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          {t('shortcuts.close')}
        </Button>
      </div>
    </Dialog>
  )
}

/** Opens the shortcut sheet, as `?` does. */
export function ShortcutSheetButton({ onClick }: { onClick: () => void }): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      aria-label={t('shortcuts.title')}
      title={`${t('shortcuts.title')} (?)`}
      aria-keyshortcuts="?"
      aria-haspopup="dialog"
      onClick={onClick}
    >
      <Keyboard aria-hidden="true" />
    </Button>
  )
}

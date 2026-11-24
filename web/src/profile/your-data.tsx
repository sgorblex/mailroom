import { Download } from 'lucide-react'
import { useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import { auditExportUrl, ruleSetsExportUrl } from '@/api/export.ts'
import { useFeature } from '@/auth/auth-context.ts'
import { buttonVariants } from '@/components/ui/button-variants.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'

/**
 * The user's own data to keep: their rule sets where the instance runs filters, and their audit
 * trail, whole or from one day to another in their time zone. The API names each file.
 */
export function YourData(): ReactElement {
  const { t } = useTranslation()
  const filters = useFeature('filters')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const reversed = since !== '' && until !== '' && until < since

  return (
    <section aria-labelledby="your-data" className="flex flex-col gap-3">
      <h3 id="your-data" className="font-medium">
        {t('yourData.title')}
      </h3>
      {filters && (
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-medium">{t('yourData.ruleSets')}</h4>
          <p className="text-sm text-muted-foreground">{t('yourData.ruleSetsHint')}</p>
          <DownloadLink url={ruleSetsExportUrl()} label={t('yourData.downloadRuleSets')} />
        </div>
      )}
      <div className="flex flex-col gap-2">
        <h4 className="text-sm font-medium">{t('yourData.activity')}</h4>
        <p className="text-sm text-muted-foreground">{t('yourData.activityHint')}</p>
        <div className="flex flex-wrap gap-3">
          <label className="flex flex-col gap-1 text-sm">
            {t('yourData.since')}
            <Input
              type="date"
              value={since}
              aria-invalid={reversed}
              aria-describedby={reversed ? 'your-data-reversed' : undefined}
              onChange={(event) => {
                setSince(event.target.value)
              }}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t('yourData.until')}
            <Input
              type="date"
              value={until}
              aria-invalid={reversed}
              aria-describedby={reversed ? 'your-data-reversed' : undefined}
              onChange={(event) => {
                setUntil(event.target.value)
              }}
            />
          </label>
        </div>
        {reversed && (
          <p id="your-data-reversed" role="alert" className="text-sm text-destructive">
            {t('yourData.reversed')}
          </p>
        )}
        {reversed ? (
          <Button variant="outline" size="sm" className="self-start" disabled>
            <Download aria-hidden="true" />
            {t('yourData.downloadActivity')}
          </Button>
        ) : (
          <DownloadLink url={auditExportUrl(since, until)} label={t('yourData.downloadActivity')} />
        )}
      </div>
    </section>
  )
}

function DownloadLink({ url, label }: { url: string; label: string }): ReactElement {
  return (
    <a href={url} download className={buttonVariants({ variant: 'outline', size: 'sm', className: 'self-start' })}>
      <Download aria-hidden="true" />
      {label}
    </a>
  )
}

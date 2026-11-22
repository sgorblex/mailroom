import { useId, useState, type ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import type { ImportedDraft, ImportSource } from '@/api/mail.ts'
import { Button } from '@/components/ui/button.tsx'
import { Dialog } from '@/components/ui/dialog.tsx'
import { Input } from '@/components/ui/input.tsx'

import { MAX_FILES } from './compose-form.ts'
import { MAIL_NS } from './i18n.ts'
import { composeErrorMessage } from './messages.ts'

type Kind = ImportSource['kind']

/**
 * Picks a message built elsewhere, an .eml or an HTML file with its images and attachments, and
 * hands it to `onImport`, which saves it (a new draft, or the next version of one); a refusal is
 * worded here and the dialog stays.
 */
export function ImportDialog({
  replacing,
  onImport,
  onDone,
  onCancel,
}: {
  replacing: boolean
  onImport: (source: ImportSource) => Promise<ImportedDraft>
  onDone: (imported: ImportedDraft) => void
  onCancel: () => void
}): ReactElement {
  const { t } = useTranslation(MAIL_NS)
  const [kind, setKind] = useState<Kind>('eml')
  const [main, setMain] = useState<File | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const [importing, setImporting] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const mainId = useId()
  const filesId = useId()
  const problemId = useId()
  const title = t(replacing ? 'designed.replaceTitle' : 'designed.importTitle')
  // Once sent, the import lands whatever the dialog does: it stays until it is answered.
  const cancel = (): void => {
    if (!importing) {
      onCancel()
    }
  }

  async function submit(): Promise<void> {
    if (importing) {
      return
    }
    if (main === null) {
      setProblem(t('designed.chooseFile'))
      return
    }
    if (kind === 'html' && files.length > MAX_FILES) {
      setProblem(t('compose.tooManyFiles', { count: MAX_FILES }))
      return
    }
    setImporting(true)
    setProblem(null)
    try {
      onDone(await onImport(kind === 'eml' ? { kind, eml: main } : { kind, html: main, files }))
    } catch (error) {
      setProblem(composeErrorMessage(t, error, t('designed.importFailed'), t('designed.notImportable')))
      setImporting(false)
    }
  }

  return (
    <Dialog label={title} size="lg" onCancel={cancel}>
      <form
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <h2 className="text-base font-semibold">{title}</h2>
        <p className="text-sm text-muted-foreground">{t(replacing ? 'designed.replaceHint' : 'designed.importHint')}</p>
        <fieldset className="flex flex-col gap-1" disabled={importing}>
          <legend className="sr-only">{t('designed.kind')}</legend>
          {(['eml', 'html'] as const).map((each) => (
            <label key={each} className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="kind"
                checked={kind === each}
                onChange={() => {
                  setKind(each)
                  setMain(null)
                  setFiles([])
                  setProblem(null)
                }}
              />
              {t(each === 'eml' ? 'designed.kindEml' : 'designed.kindHtml')}
            </label>
          ))}
        </fieldset>
        <div className="flex flex-col gap-1">
          <label htmlFor={mainId} className="text-sm text-muted-foreground">
            {t(kind === 'eml' ? 'designed.emlFile' : 'designed.htmlFile')}
          </label>
          <Input
            key={kind}
            id={mainId}
            type="file"
            accept={kind === 'eml' ? '.eml,message/rfc822' : '.html,.htm,text/html'}
            disabled={importing}
            aria-describedby={problem === null ? undefined : problemId}
            onChange={(event) => {
              setMain(event.target.files?.[0] ?? null)
            }}
          />
        </div>
        {kind === 'html' && (
          <div className="flex flex-col gap-1">
            <label htmlFor={filesId} className="text-sm text-muted-foreground">
              {t('designed.files')}
            </label>
            <Input
              id={filesId}
              type="file"
              multiple
              disabled={importing}
              onChange={(event) => {
                setFiles(Array.from(event.target.files ?? []))
              }}
            />
            <p className="text-xs text-muted-foreground">{t('designed.filesHint')}</p>
          </div>
        )}
        {problem !== null && (
          <p id={problemId} role="alert" className="text-sm text-destructive">
            {problem}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" aria-disabled={importing} onClick={cancel}>
            {t('compose.cancel')}
          </Button>
          <Button type="submit" size="sm" aria-disabled={importing}>
            {importing ? t('designed.importing') : t(replacing ? 'designed.replace' : 'designed.importAction')}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

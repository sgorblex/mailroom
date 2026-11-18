import { useId, useState, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { TRIGGERS, type Marker, type RuleSet, type RuleSetFields, type Trigger } from '@/api/filters.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'

import { FolderPicker } from './folder-picker.tsx'
import type { FoldersState } from './folders.ts'
import { filterErrorMessage } from './messages.ts'
import { FILTERS_NS } from './i18n.ts'

/** The longest schedule the server accepts: a week. */
const MAX_INTERVAL_MINUTES = 7 * 24 * 60

const DEFAULT_INTERVAL_MINUTES = 60

type Mark = Exclude<Marker, 'none'>
const MARKS: readonly Mark[] = ['star', 'to_sort']

/**
 * Makes a rule set (no `ruleSet`) or edits one: its name, folder, trigger, the mark it is limited
 * to and enabled, the folder picked from the account's. "Only marked" picks To sort where the
 * account's server keeps keywords, else the star; where it keeps none, To sort is greyed out unless
 * the rule set already has it. `save` sends it; a refusal shows above the buttons.
 */
export function RuleSetForm({
  folders,
  keywords,
  ruleSet,
  save,
  onCancel,
}: {
  /** The account's folders, to pick the rule set's from. */
  folders: FoldersState
  /** Whether the account's server keeps keywords (`Account.keywords`); `null` or absent when not known. */
  keywords?: boolean | null
  ruleSet?: RuleSet
  save: (fields: RuleSetFields) => Promise<unknown>
  onCancel: () => void
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const prefix = useId()
  const editing = ruleSet !== undefined
  const [name, setName] = useState(ruleSet?.name ?? '')
  const [folder, setFolder] = useState<string[] | null>(ruleSet?.folder ?? null)
  const [trigger, setTrigger] = useState<Trigger>(ruleSet?.trigger ?? 'new_mail')
  const [interval, setIntervalMinutes] = useState(String(ruleSet?.interval_minutes ?? DEFAULT_INTERVAL_MINUTES))
  const [markedOnly, setMarkedOnly] = useState(ruleSet !== undefined && ruleSet.marker !== 'none')
  const [mark, setMark] = useState<Mark>(
    ruleSet !== undefined && ruleSet.marker !== 'none' ? ruleSet.marker : keywords === true ? 'to_sort' : 'star',
  )
  const toSortUnsupported = keywords === false && ruleSet?.marker !== 'to_sort'
  const [enabled, setEnabled] = useState(ruleSet?.enabled ?? false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [folderMissing, setFolderMissing] = useState(false)

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (folder === null) {
      setFolderMissing(true)
      setError(t('filters.ruleSet.folderMissing'))
      return
    }
    setSubmitting(true)
    setError(null)
    setFolderMissing(false)
    try {
      await save({
        name: name.trim(),
        folder,
        trigger,
        interval_minutes: trigger === 'scheduled' ? Number(interval) : null,
        marker: markedOnly ? mark : 'none',
        enabled,
      })
    } catch (failure) {
      setError(filterErrorMessage(t, failure, t('filters.ruleSet.saveFailed')))
      setSubmitting(false)
    }
  }

  const titleId = `${prefix}-title`
  const id = (field: string): string => `${prefix}-${field}`
  return (
    <form
      aria-labelledby={titleId}
      className="flex flex-col gap-4 rounded-lg border p-4"
      onSubmit={(event) => void submit(event)}
    >
      <h3 id={titleId} className="font-medium">
        {editing ? t('filters.ruleSet.editTitle', { name: ruleSet.name }) : t('filters.ruleSet.add')}
      </h3>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <label htmlFor={id('name')} className="text-sm font-medium">
            {t('filters.ruleSet.name')}
          </label>
          <Input
            id={id('name')}
            required
            maxLength={200}
            value={name}
            onChange={(event) => {
              setName(event.target.value)
            }}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={id('folder')} className="text-sm font-medium">
            {t('filters.ruleSet.folder')}
          </label>
          <FolderPicker
            id={id('folder')}
            folders={folders}
            value={folder}
            invalid={folderMissing}
            describedBy={folders.kind === 'failed' ? id('folder-hint') : undefined}
            onChange={(path) => {
              setFolder(path)
              setFolderMissing(false)
            }}
          />
          {folders.kind === 'failed' && (
            <p id={id('folder-hint')} className="text-sm text-muted-foreground">
              {t('filters.foldersUnavailable', { message: folders.message })}
            </p>
          )}
        </div>
      </div>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-medium">{t('filters.ruleSet.trigger')}</legend>
        {TRIGGERS.map((option) => (
          <div key={option} className="flex items-start gap-2 text-sm">
            <input
              id={id(`trigger-${option}`)}
              type="radio"
              name={id('trigger')}
              value={option}
              checked={trigger === option}
              aria-describedby={id(`trigger-${option}-hint`)}
              className="mt-1"
              onChange={() => {
                setTrigger(option)
              }}
            />
            <div className="flex flex-col">
              <label htmlFor={id(`trigger-${option}`)}>{t(`filters.triggers.${option}`)}</label>
              <span id={id(`trigger-${option}-hint`)} className="text-muted-foreground">
                {t(`filters.triggerHints.${option}`)}
              </span>
            </div>
          </div>
        ))}
        {trigger === 'scheduled' && (
          <div className="ml-6 flex max-w-48 flex-col gap-1">
            <label htmlFor={id('interval')} className="text-sm font-medium">
              {t('filters.ruleSet.interval')}
            </label>
            <Input
              id={id('interval')}
              type="number"
              required
              min={1}
              max={MAX_INTERVAL_MINUTES}
              step={1}
              value={interval}
              onChange={(event) => {
                setIntervalMinutes(event.target.value)
              }}
            />
          </div>
        )}
      </fieldset>
      <div className="flex flex-col gap-2 text-sm">
        <div className="flex items-start gap-2">
          <input
            id={id('marked')}
            type="checkbox"
            checked={markedOnly}
            aria-describedby={id('marked-hint')}
            className="mt-1"
            onChange={(event) => {
              setMarkedOnly(event.target.checked)
            }}
          />
          <div className="flex flex-col">
            <label htmlFor={id('marked')}>{t('filters.ruleSet.markedOnly')}</label>
            <span id={id('marked-hint')} className="text-muted-foreground">
              {t('filters.ruleSet.markedOnlyHint')}
            </span>
          </div>
        </div>
        {markedOnly && (
          <fieldset className="ml-6 flex flex-col gap-2">
            <legend className="mb-1 font-medium">{t('filters.ruleSet.marker')}</legend>
            {MARKS.map((option) => {
              const unavailable = option === 'to_sort' && toSortUnsupported
              return (
                <div key={option} className="flex items-start gap-2">
                  <input
                    id={id(`marker-${option}`)}
                    type="radio"
                    name={id('marker')}
                    value={option}
                    checked={mark === option}
                    disabled={unavailable}
                    aria-describedby={id(`marker-${option}-hint`)}
                    className="mt-1"
                    onChange={() => {
                      setMark(option)
                    }}
                  />
                  <div className={unavailable ? 'flex flex-col opacity-60' : 'flex flex-col'}>
                    <label htmlFor={id(`marker-${option}`)}>{t(`filters.ruleSet.markers.${option}`)}</label>
                    <span id={id(`marker-${option}-hint`)} className="text-muted-foreground">
                      {unavailable ? t('filters.ruleSet.toSortUnsupported') : t(`filters.ruleSet.markerHints.${option}`)}
                    </span>
                  </div>
                </div>
              )
            })}
          </fieldset>
        )}
      </div>
      <div className="flex items-start gap-2 text-sm">
        <input
          id={id('enabled')}
          type="checkbox"
          checked={enabled}
          aria-describedby={id('enabled-hint')}
          className="mt-1"
          onChange={(event) => {
            setEnabled(event.target.checked)
          }}
        />
        <div className="flex flex-col">
          <label htmlFor={id('enabled')}>{t('filters.ruleSet.enabled')}</label>
          <span id={id('enabled-hint')} className="text-muted-foreground">
            {t('filters.ruleSet.enabledHint')}
          </span>
        </div>
      </div>
      {error !== null && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={submitting}>
          {editing ? t('filters.save') : t('filters.ruleSet.addButton')}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          {t('filters.cancel')}
        </Button>
      </div>
    </form>
  )
}

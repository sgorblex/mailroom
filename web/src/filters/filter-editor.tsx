import { useId, useRef, useState, type KeyboardEvent, type ReactElement, type SubmitEvent } from 'react'
import { useTranslation } from 'react-i18next'

import type { Filter, FilterFields } from '@/api/filters.ts'
import { Button } from '@/components/ui/button.tsx'
import { Input } from '@/components/ui/input.tsx'
import { cn } from '@/lib/utils'

import type { FoldersState } from './folders.ts'
import { filterErrorMessage } from './messages.ts'
import { missingArgs, newSpecModel, readSpec, SpecShapeError, writeSpec, type SpecModel } from './spec.ts'
import { SpecTree } from './spec-tree.tsx'
import type { Vocabulary } from './vocabulary.ts'
import { FILTERS_NS } from './i18n.ts'

type Mode = 'tree' | 'json'

function initialModel(filter: Filter | undefined, vocabulary: Vocabulary): SpecModel | null {
  if (filter === undefined) {
    return newSpecModel(vocabulary)
  }
  try {
    return readSpec(filter.spec)
  } catch {
    return null
  }
}

/** The JSON text's spec, or why it is not one. */
function parseJson(text: string): { spec: Record<string, unknown> } | { problem: string } {
  try {
    const value: unknown = JSON.parse(text)
    if (typeof value !== 'object' || value === null || Array.isArray(value)) {
      return { problem: 'notObject' }
    }
    return { spec: value as Record<string, unknown> }
  } catch (error) {
    return { problem: error instanceof Error ? error.message : String(error) }
  }
}

/**
 * Adds a filter (no `filter`) or edits one: its name, whether it runs, and its spec, either as a
 * tree of criteria and a list of actions or as the raw JSON the filter engine stores. The server
 * checks the spec; its refusal shows above the buttons.
 */
export function FilterEditor({
  filter,
  vocabulary,
  folders,
  save,
  onCancel,
}: {
  filter?: Filter
  vocabulary: Vocabulary
  folders: FoldersState
  save: (fields: FilterFields) => Promise<unknown>
  onCancel: () => void
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  const prefix = useId()
  const editing = filter !== undefined
  const [name, setName] = useState(filter?.name ?? '')
  const [enabled, setEnabled] = useState(filter?.enabled ?? true)
  const [model, setModel] = useState<SpecModel | null>(() => initialModel(filter, vocabulary))
  // A stored spec the tree cannot show opens as JSON.
  const [mode, setMode] = useState<Mode>(() => (model === null ? 'json' : 'tree'))
  const [jsonText, setJsonText] = useState(() => JSON.stringify(filter?.spec ?? {}, null, 2))
  const [tabProblem, setTabProblem] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const treeTab = useRef<HTMLButtonElement>(null)
  const jsonTab = useRef<HTMLButtonElement>(null)

  function jsonProblem(problem: string): string {
    return problem === 'notObject'
      ? t('filters.editor.jsonNotObject')
      : t('filters.editor.jsonInvalid', { message: problem })
  }

  function shapeProblem(failure: unknown): string {
    if (!(failure instanceof SpecShapeError)) {
      return String(failure)
    }
    const problem = t(`filters.editor.shape.${failure.problem}`, { group: failure.group ?? '' })
    return failure.where === '' ? problem : t('filters.editor.shape.at', { where: failure.where, problem })
  }

  /** Shows `next`; whether it could (JSON the tree cannot show keeps the JSON tab). */
  function switchTo(next: Mode): boolean {
    if (next === mode) {
      return true
    }
    setTabProblem(null)
    setError(null)
    if (next === 'json') {
      if (model !== null) {
        setJsonText(JSON.stringify(writeSpec(model), null, 2))
      }
      setMode('json')
      return true
    }
    const parsed = parseJson(jsonText)
    if ('problem' in parsed) {
      setTabProblem(jsonProblem(parsed.problem))
      return false
    }
    try {
      setModel(readSpec(parsed.spec))
      setMode('tree')
      return true
    } catch (failure) {
      setTabProblem(t('filters.editor.treeCannotShow', { message: shapeProblem(failure) }))
      return false
    }
  }

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>): void {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault()
      const next = mode === 'tree' ? 'json' : 'tree'
      if (switchTo(next)) {
        ;(next === 'tree' ? treeTab : jsonTab).current?.focus()
      }
    }
  }

  async function submit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setError(null)
    setTabProblem(null)
    let spec: Record<string, unknown>
    if (mode === 'tree' && model !== null) {
      if (missingArgs(model, vocabulary)) {
        setError(t('filters.editor.incomplete'))
        return
      }
      spec = writeSpec(model)
    } else {
      const parsed = parseJson(jsonText)
      if ('problem' in parsed) {
        setError(jsonProblem(parsed.problem))
        return
      }
      spec = parsed.spec
    }
    setSubmitting(true)
    try {
      await save({ name: name.trim(), enabled, spec })
    } catch (failure) {
      setError(filterErrorMessage(t, failure, t('filters.editor.saveFailed')))
      setSubmitting(false)
    }
  }

  const titleId = `${prefix}-title`
  const tabId = (which: Mode): string => `${prefix}-tab-${which}`
  const panelId = `${prefix}-panel`
  return (
    <form
      aria-labelledby={titleId}
      className="flex flex-col gap-4 rounded-lg border bg-background p-4"
      onSubmit={(event) => void submit(event)}
    >
      <h3 id={titleId} className="font-medium">
        {editing ? t('filters.editor.editTitle', { name: filter.name }) : t('filters.editor.add')}
      </h3>
      <div className="flex flex-wrap items-end gap-4">
        <div className="flex min-w-60 flex-1 flex-col gap-1">
          <label htmlFor={`${prefix}-name`} className="text-sm font-medium">
            {t('filters.editor.name')}
          </label>
          <Input
            id={`${prefix}-name`}
            required
            maxLength={200}
            value={name}
            onChange={(event) => {
              setName(event.target.value)
            }}
          />
        </div>
        <label className="flex h-9 items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => {
              setEnabled(event.target.checked)
            }}
          />
          {t('filters.editor.enabled')}
        </label>
      </div>
      <div className="flex flex-col gap-3">
        <div role="tablist" aria-label={t('filters.editor.editAs')} className="flex gap-1 border-b">
          {(['tree', 'json'] as const).map((which) => (
            <button
              key={which}
              ref={which === 'tree' ? treeTab : jsonTab}
              id={tabId(which)}
              type="button"
              role="tab"
              aria-selected={mode === which}
              aria-controls={panelId}
              tabIndex={mode === which ? 0 : -1}
              className={cn(
                '-mb-px border-b-2 border-transparent px-3 py-1.5 text-sm text-muted-foreground outline-none',
                'hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50',
                'aria-selected:border-foreground aria-selected:font-medium aria-selected:text-foreground',
              )}
              onClick={() => {
                switchTo(which)
              }}
              onKeyDown={onTabKey}
            >
              {t(`filters.editor.${which}`)}
            </button>
          ))}
        </div>
        {tabProblem !== null && (
          <p role="alert" className="text-sm text-destructive">
            {tabProblem}
          </p>
        )}
        <div id={panelId} role="tabpanel" aria-labelledby={tabId(mode)}>
          {mode === 'tree' && model !== null ? (
            <SpecTree model={model} vocabulary={vocabulary} folders={folders} onChange={setModel} />
          ) : (
            <div className="flex flex-col gap-1">
              <label htmlFor={`${prefix}-json`} className="text-sm font-medium">
                {t('filters.editor.jsonLabel')}
              </label>
              <textarea
                id={`${prefix}-json`}
                aria-describedby={`${prefix}-json-hint`}
                rows={14}
                spellCheck={false}
                className={cn(
                  'w-full rounded-md border bg-transparent p-3 font-mono text-sm outline-none',
                  'focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50',
                )}
                value={jsonText}
                onChange={(event) => {
                  setJsonText(event.target.value)
                }}
              />
              <p id={`${prefix}-json-hint`} className="text-sm text-muted-foreground">
                {t('filters.editor.jsonHint')}
              </p>
            </div>
          )}
        </div>
      </div>
      {error !== null && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={submitting}>
          {editing ? t('filters.save') : t('filters.editor.addButton')}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          {t('filters.cancel')}
        </Button>
      </div>
    </form>
  )
}

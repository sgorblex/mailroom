import type { ReactElement } from 'react'
import { useTranslation } from 'react-i18next'

import type { Filter, FilterReport, RunReport } from '@/api/filters.ts'
import { formatDateTime } from '@/lib/format.ts'

import { describeActions } from './describe.ts'
import { runErrorMessage } from './messages.ts'
import { readSpec } from './spec.ts'
import type { Vocabulary } from './vocabulary.ts'
import { FILTERS_NS } from './i18n.ts'

/** How many matched messages a report lists per filter; the rest are counted. */
const SHOWN_MESSAGES = 50

/** The name of the filter a report entry is about; `null` once it is gone. */
function filterName(filters: Filter[], entry: FilterReport): string | null {
  return filters.find((filter) => filter.id === entry.filter_id)?.name ?? null
}

/**
 * What a run or dry run found, filter by filter: the action and the messages it matched. Senders
 * and subjects are mail content, shown as plain text. Each filter's heading is at `level`.
 */
export function RunReportView({
  report,
  filters,
  vocabulary,
  level,
}: {
  report: RunReport
  filters: Filter[]
  vocabulary: Vocabulary
  level: 3 | 4
}): ReactElement {
  const { t } = useTranslation(FILTERS_NS)
  return (
    <div className="flex flex-col gap-4">
      {report.error != null && (
        <p className="text-sm text-destructive">
          {t('filters.report.error', { message: runErrorMessage(t, report.error) })}
        </p>
      )}
      {report.filters.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('filters.report.noFilters')}</p>
      )}
      {report.limited_to_newest != null && (
        <p className="text-sm text-muted-foreground">
          {t('filters.report.limited', { count: report.limited_to_newest })}
        </p>
      )}
      {report.filters.map((entry, index) => (
        <FilterReportView
          // Reports are read-only snapshots: their order is their identity.
          key={index}
          entry={entry}
          name={filterName(filters, entry) ?? t('filters.report.filterN', { n: index + 1 })}
          dryRun={report.dry_run}
          vocabulary={vocabulary}
          level={level}
        />
      ))}
    </div>
  )
}

function FilterReportView({
  entry,
  name,
  dryRun,
  vocabulary,
  level,
}: {
  entry: FilterReport
  name: string
  dryRun: boolean
  vocabulary: Vocabulary
  level: 3 | 4
}): ReactElement {
  const { t, i18n } = useTranslation(FILTERS_NS)
  const Heading = level === 3 ? 'h3' : 'h4'
  let action = entry.action
  if (entry.spec !== null) {
    try {
      action = describeActions({ t, language: i18n.language, vocabulary }, readSpec(entry.spec).actions)
    } catch {
      // The action's own words, as imap-thingy wrote them.
    }
  }
  const total = entry.matched_total ?? entry.matched.length
  const outcome = dryRun
    ? t('filters.report.wouldDo', { action })
    : entry.executed
      ? t('filters.report.did', { action })
      : total === 0
        ? t('filters.report.nothingToDo')
        : t('filters.report.notDone', { action })
  const shown = entry.matched.slice(0, SHOWN_MESSAGES)
  const more = total - shown.length

  return (
    <section aria-label={name} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <Heading className="text-sm font-medium">{name}</Heading>
        <span className="text-sm text-muted-foreground tabular-nums">
          {t('filters.report.matched', { count: total })}
        </span>
      </div>
      <p className="text-sm">{outcome}</p>
      {shown.length > 0 && (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-left text-sm">
            <thead className="bg-muted/50 text-muted-foreground">
              <tr>
                <th scope="col" className="px-3 py-1.5 font-medium">
                  {t('filters.report.from')}
                </th>
                <th scope="col" className="px-3 py-1.5 font-medium">
                  {t('filters.report.subject')}
                </th>
                <th scope="col" className="px-3 py-1.5 font-medium whitespace-nowrap">
                  {t('filters.report.date')}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {shown.map((message) => (
                <tr key={message.uid}>
                  <td className="max-w-56 truncate px-3 py-1.5">{message.from ?? ''}</td>
                  <td className="max-w-72 truncate px-3 py-1.5">
                    {message.subject ?? t('filters.report.noSubject')}
                  </td>
                  <td className="px-3 py-1.5 whitespace-nowrap text-muted-foreground tabular-nums">
                    {formatDateTime(i18n.language, message.date)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {more > 0 && <p className="text-sm text-muted-foreground">{t('filters.report.more', { count: more })}</p>}
    </section>
  )
}

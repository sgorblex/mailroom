import { describe, expect, it } from 'vitest'

import i18n from '@/i18n/i18n.ts'

import { FILTERS_NS } from './i18n.ts'
import { ApiRequestError } from '@/api/http.ts'

import { filterErrorMessage, runErrorMessage } from './messages.ts'

describe('runErrorMessage', () => {
  it('words the recorded codes and keeps an unknown error as it is', () => {
    const t = i18n.getFixedT('en', FILTERS_NS)
    expect(runErrorMessage(t, 'interrupted: the worker stopped during the run.')).toBe(
      'The run was cut short and did not finish.',
    )
    expect(runErrorMessage(t, 'run_failed: KeyError')).toBe('The run stopped on an unexpected error (KeyError).')
    expect(runErrorMessage(t, 'something odd')).toBe('something odd')
  })

  it('words a pattern out of time, in English and Italian', () => {
    const error = "match_timeout: A filter's regular expression took too long on a message: simplify the pattern."
    expect(runErrorMessage(i18n.getFixedT('en', FILTERS_NS), error)).toBe(
      "A filter's pattern took too long on a message, so the run stopped: simplify the pattern (nested repeats such as (a+)+ are the usual cause).",
    )
    expect(runErrorMessage(i18n.getFixedT('it', FILTERS_NS), error)).toBe(
      "L'espressione di un filtro ha richiesto troppo tempo su un messaggio, quindi l'esecuzione si è fermata: semplificala (di solito la causa sono ripetizioni annidate come (a+)+).",
    )
  })
})

function refusal(status: number, code: string): ApiRequestError {
  return new ApiRequestError(status, { detail: { code, message: 'Refused.' } }, 'Refused.')
}

describe('filterErrorMessage', () => {
  it.each([
    [
      'en',
      'Another dry run of yours is still going: wait for it to finish, then try again.',
      'A rule set holds at most 100 filters: remove one to add another.',
    ],
    [
      'it',
      'Hai già una simulazione in corso: aspetta che finisca, poi riprova.',
      'Una regola contiene al massimo 100 filtri: rimuovine uno per aggiungerne un altro.',
    ],
  ])('words a busy dry run and a full rule set in %s', (language, busy, full) => {
    const t = i18n.getFixedT(language, FILTERS_NS)
    expect(filterErrorMessage(t, refusal(429, 'dry_run_busy'), 'fallback')).toBe(busy)
    expect(filterErrorMessage(t, refusal(400, 'too_many_filters'), 'fallback')).toBe(full)
  })
})

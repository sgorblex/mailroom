/** Send later's times, in the user's time zone (the browser's when `undefined`). */

/** How far ahead the server holds a send. */
export const MAX_AHEAD_DAYS = 365
const MORNING = '08:00'
const EVENING = '18:00'
/** Past this hour "later today" is not offered. */
const LATE_HOUR = 17

/** A wall-clock moment in a zone: `date` as `YYYY-MM-DD`, `time` as `HH:MM`, `weekday` 0 (Sunday) to 6. */
export interface WallTime {
  date: string
  time: string
  weekday: number
}

const formats = new Map<string | undefined, Intl.DateTimeFormat>()

function format(timeZone: string | undefined): Intl.DateTimeFormat {
  let found = formats.get(timeZone)
  if (found === undefined) {
    found = new Intl.DateTimeFormat('en-US', {
      timeZone,
      hourCycle: 'h23',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      weekday: 'short',
    })
    formats.set(timeZone, found)
  }
  return found
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function parts(instant: Date, timeZone: string | undefined): Record<string, string> {
  const named: Record<string, string> = {}
  for (const part of format(timeZone).formatToParts(instant)) {
    named[part.type] = part.value
  }
  return named
}

/** What the clock in `timeZone` shows at `instant`. */
export function wallTime(instant: Date, timeZone: string | undefined): WallTime {
  const { year, month, day, hour, minute, weekday } = parts(instant, timeZone)
  return {
    date: `${year ?? ''}-${month ?? ''}-${day ?? ''}`,
    time: `${hour ?? ''}:${minute ?? ''}`,
    weekday: WEEKDAYS.indexOf(weekday ?? ''),
  }
}

/** How far `timeZone` is ahead of UTC at `instant`, in ms. */
function offset(instant: number, timeZone: string | undefined): number {
  const { year, month, day, hour, minute, second } = parts(new Date(instant), timeZone)
  const shown = Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second))
  return shown - Math.floor(instant / 1_000) * 1_000
}

/**
 * The instant the clock in `timeZone` shows `date` (`YYYY-MM-DD`) at `time` (`HH:MM`); `null` for
 * text that is neither. A time a clock change skips is read with the offset from before it.
 */
export function zonedInstant(date: string, time: string, timeZone: string | undefined): Date | null {
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date)
  const clock = /^(\d{2}):(\d{2})$/.exec(time)
  if (day === null || clock === null) {
    return null
  }
  const asUtc = Date.UTC(Number(day[1]), Number(day[2]) - 1, Number(day[3]), Number(clock[1]), Number(clock[2]))
  if (Number.isNaN(asUtc)) {
    return null
  }
  const first = asUtc - offset(asUtc, timeZone)
  return new Date(asUtc - offset(first, timeZone))
}

/** `date` (`YYYY-MM-DD`) `days` later. */
export function addDays(date: string, days: number): string {
  const [year, month, day] = date.split('-').map(Number)
  return new Date(Date.UTC(year ?? 0, (month ?? 1) - 1, (day ?? 1) + days)).toISOString().slice(0, 10)
}

export type PresetKind = 'laterToday' | 'tomorrowMorning' | 'mondayMorning'

export interface Preset {
  kind: PresetKind
  at: Date
}

/**
 * The quick choices from `now`: later today (until late afternoon), tomorrow morning, and Monday
 * morning unless tomorrow is Monday.
 */
export function presets(now: Date, timeZone: string | undefined): Preset[] {
  const today = wallTime(now, timeZone)
  const found: Preset[] = []
  const at = (date: string, time: string): Date | null => zonedInstant(date, time, timeZone)
  const evening = at(today.date, EVENING)
  if (Number(today.time.slice(0, 2)) < LATE_HOUR && evening !== null) {
    found.push({ kind: 'laterToday', at: evening })
  }
  const tomorrow = at(addDays(today.date, 1), MORNING)
  if (tomorrow !== null) {
    found.push({ kind: 'tomorrowMorning', at: tomorrow })
  }
  const toMonday = (8 - today.weekday) % 7 || 7
  const monday = at(addDays(today.date, toMonday), MORNING)
  if (toMonday > 1 && monday !== null) {
    found.push({ kind: 'mondayMorning', at: monday })
  }
  return found
}

/** Why `at` cannot be a send time from `now`, or `null` when it can. */
export function sendTimeProblem(at: Date | null, now: Date): 'invalid' | 'past' | 'tooFar' | null {
  if (at === null) {
    return 'invalid'
  }
  if (at.getTime() <= now.getTime()) {
    return 'past'
  }
  return at.getTime() - now.getTime() > MAX_AHEAD_DAYS * 86_400_000 ? 'tooFar' : null
}

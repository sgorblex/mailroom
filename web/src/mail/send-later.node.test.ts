import { describe, expect, it } from 'vitest'

import { addDays, presets, sendTimeProblem, wallTime, zonedInstant } from './send-later.ts'

const ROME = 'Europe/Rome'

describe('send later times', () => {
  it('read a wall clock in the user\'s zone, summer and winter', () => {
    expect(zonedInstant('2026-07-01', '08:00', ROME)?.toISOString()).toBe('2026-07-01T06:00:00.000Z')
    expect(zonedInstant('2026-12-01', '08:00', ROME)?.toISOString()).toBe('2026-12-01T07:00:00.000Z')
    expect(zonedInstant('2026-12-01', '08:00', 'America/New_York')?.toISOString()).toBe('2026-12-01T13:00:00.000Z')
    expect(zonedInstant('2026-12-01', '8', ROME)).toBeNull()
    expect(wallTime(new Date('2026-12-01T23:30:00Z'), ROME)).toEqual({ date: '2026-12-02', time: '00:30', weekday: 3 })
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01')
  })

  it('offer later today until the afternoon, tomorrow and Monday morning', () => {
    // Tuesday 1 December 2026, 10:00 in Rome.
    const morning = presets(new Date('2026-12-01T09:00:00Z'), ROME)
    expect(morning.map((each) => [each.kind, each.at.toISOString()])).toEqual([
      ['laterToday', '2026-12-01T17:00:00.000Z'],
      ['tomorrowMorning', '2026-12-02T07:00:00.000Z'],
      ['mondayMorning', '2026-12-07T07:00:00.000Z'],
    ])
    // Sunday evening: no later today, and tomorrow is Monday.
    const sunday = presets(new Date('2026-12-06T19:00:00Z'), ROME)
    expect(sunday.map((each) => each.kind)).toEqual(['tomorrowMorning'])
    // Monday: the next one.
    const monday = presets(new Date('2026-12-07T17:00:00Z'), ROME)
    expect(monday.map((each) => [each.kind, each.at.toISOString()])).toEqual([
      ['tomorrowMorning', '2026-12-08T07:00:00.000Z'],
      ['mondayMorning', '2026-12-14T07:00:00.000Z'],
    ])
  })

  it('must be to come, and at most a year ahead', () => {
    const now = new Date('2026-12-01T09:00:00Z')
    expect(sendTimeProblem(null, now)).toBe('invalid')
    expect(sendTimeProblem(new Date('2026-12-01T08:59:00Z'), now)).toBe('past')
    expect(sendTimeProblem(new Date('2028-01-01T00:00:00Z'), now)).toBe('tooFar')
    expect(sendTimeProblem(new Date('2026-12-01T09:01:00Z'), now)).toBeNull()
  })
})

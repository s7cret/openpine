import { describe, expect, it } from 'vitest'

import { calendarMonthsForRange, resolveLocalizedArray } from './dateRangeCalendar'

describe('calendarMonthsForRange', () => {
  it('shows both endpoints when a trade range spans multiple months', () => {
    const { left, right } = calendarMonthsForRange('2020-08-19', '2026-06-16')

    expect([left.getFullYear(), left.getMonth()]).toEqual([2020, 7])
    expect([right.getFullYear(), right.getMonth()]).toEqual([2026, 5])
  })

  it('shows the following month when both endpoints are in one month', () => {
    const { left, right } = calendarMonthsForRange('2026-12-01', '2026-12-20')

    expect([left.getFullYear(), left.getMonth()]).toEqual([2026, 11])
    expect([right.getFullYear(), right.getMonth()]).toEqual([2027, 0])
  })
})

describe('resolveLocalizedArray', () => {
  it('resolves vue-i18n message objects instead of stringifying them', () => {
    const messages = [{ text: 'August' }, { text: 'September' }]

    expect(resolveLocalizedArray(messages, message => message.text, ['Aug', 'Sep']))
      .toEqual(['August', 'September'])
  })

  it('uses the fallback when the localized array has the wrong shape', () => {
    expect(resolveLocalizedArray([{ text: 'August' }], message => message.text, ['Aug', 'Sep']))
      .toEqual(['Aug', 'Sep'])
  })
})

import { describe, expect, it } from 'vitest'
import { amount, cad, cell, csvSafe, dateTime, isoDate, pct, toCsv } from './format'

describe('formats (design §8.4)', () => {
  it('dates are ISO 8601', () => {
    expect(isoDate('2026-10-03')).toBe('2026-10-03')
    expect(isoDate('2026-10-03T08:35:00')).toBe('2026-10-03')
    expect(isoDate(null)).toBe('—')
  })
  it('money uses the ISO 4217 code, never a bare $', () => {
    expect(cad(1234.5)).toBe('CAD 1,234.50')
    expect(amount(-1234.567)).toBe('-1,234.57')
    expect(cad(1)).not.toContain('$')
  })
  it('percent has one decimal', () => {
    expect(pct(0.1234)).toBe('12.3%')
  })
  it('cells pick a format from the column name', () => {
    expect(cell(1500, 'total_overdue')).toBe('1,500.00')
    expect(cell(0.25, 'cure_rate')).toBe('25.0%')
    expect(cell(true)).toBe('Yes')
  })
})

describe('CSV export (design §9.4)', () => {
  it('neutralises formulas', () => {
    expect(csvSafe('=HYPERLINK("x")')).toBe(`"'=HYPERLINK(""x"")"`)
    expect(csvSafe('+1')).toBe("'+1")
    expect(csvSafe('@SUM')).toBe("'@SUM")
    expect(csvSafe('a\nb')).toBe('a b')
  })
  it('builds rows with CRLF', () => {
    expect(toCsv(['a', 'b'], [[1, 'x,y']])).toBe('a,b\r\n1,"x,y"')
  })
})

describe('timestamps', () => {
  it('shows a warehouse timestamp (no zone) exactly as stored', () => {
    expect(dateTime('2026-10-03T18:19:40.535757')).toBe('2026-10-03 18:19')
  })
  it('converts a zoned timestamp to local time and names the zone', () => {
    expect(dateTime('2026-10-03T08:35:00+00:00')).toMatch(/^2026-10-0[34] \d{2}:\d{2}/)
  })
})

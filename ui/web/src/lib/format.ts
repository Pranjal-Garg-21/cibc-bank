// Display formats (docs/ui_design.md §8.4): ISO 8601 dates, ISO 4217 currency codes, one-decimal percentages.

const LOCALE = 'en-CA'
const money = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const moneyWithCode = new Intl.NumberFormat(LOCALE, {
  style: 'currency',
  currency: 'CAD',
  currencyDisplay: 'code',
})
const count = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 })
const decimal = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 2 })

/** `2026-10-03` from a date, ISO string or timestamp string. */
export function isoDate(v: unknown): string {
  if (v == null || v === '') return '-'
  const s = String(v)
  const m = s.match(/^(\d{4}-\d{2}-\d{2})/)
  return m ? m[1] : s
}

/**
 * `2026-10-03 14:05 GMT+5:30`. A timestamp with a zone (the UI's own, always UTC) is shown in local time with the
 * zone. A warehouse timestamp has no zone; it is shown exactly as stored, without guessing one.
 */
export function dateTime(v: unknown): string {
  if (v == null || v === '') return '-'
  const s = String(v)
  if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) {
    const m = s.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/)
    return m ? `${m[1]} ${m[2]}` : s
  }
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return s
  const p = (n: number) => String(n).padStart(2, '0')
  const tz = new Intl.DateTimeFormat(LOCALE, { timeZoneName: 'short' })
    .formatToParts(d)
    .find((x) => x.type === 'timeZoneName')?.value
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}${tz ? ` ${tz}` : ''}`
}

/** `1,234.56` for a table cell whose column header carries the currency code. */
export function amount(v: unknown): string {
  const n = Number(v)
  return v == null || v === '' || Number.isNaN(n) ? '-' : money.format(n)
}

/** `CAD 1,234.56` for an amount in a sentence. */
export function cad(v: unknown): string {
  const n = Number(v)
  return v == null || v === '' || Number.isNaN(n) ? '-' : moneyWithCode.format(n).replace(/ /g, ' ')
}

export function pct(v: unknown, digits = 1): string {
  const n = Number(v)
  return v == null || v === '' || Number.isNaN(n) ? '-' : `${(n * 100).toFixed(digits)}%`
}

export function int(v: unknown): string {
  const n = Number(v)
  return v == null || v === '' || Number.isNaN(n) ? '-' : count.format(n)
}

/** A generic cell: numbers grouped, dates ISO, booleans Yes/No. */
export function cell(v: unknown, column = ''): string {
  if (v == null || v === '') return '-'
  if (typeof v === 'boolean') return v ? 'Yes' : 'No'
  if (typeof v === 'number') {
    if (/(amount|balance|overdue|value|payment)/i.test(column)) return amount(v)
    if (/(rate|share|probability|ratio)/i.test(column) && Math.abs(v) <= 1) return pct(v)
    return Number.isInteger(v) ? count.format(v) : decimal.format(v)
  }
  const s = String(v)
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(s)) return dateTime(s)
  return s
}

export function isNumeric(v: unknown): boolean {
  return typeof v === 'number'
}

export function humanize(key: string): string {
  const s = key.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** A cell a spreadsheet will not run as a formula (CSV export, design §9.4). */
export function csvSafe(v: unknown): string {
  let s = v == null ? '' : String(v)
  s = s.replace(/[\r\n]+/g, ' ')
  if (/^(?:\s*[=+\-@]|\t)/.test(s)) s = `'${s}`
  return /[",]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

export function toCsv(columns: string[], rows: unknown[][]): string {
  return [columns.map(csvSafe).join(','), ...rows.map((r) => r.map(csvSafe).join(','))].join('\r\n')
}

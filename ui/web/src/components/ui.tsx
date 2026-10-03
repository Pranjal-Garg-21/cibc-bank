import { useEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { ApiError } from '../api/client'
import type { Table } from '../api/types'
import { cell, humanize, isNumeric } from '../lib/format'
import type { Tone } from '../lib/labels'

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(' ')
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' | 'ghost' }
export function Button({ variant = 'secondary', className, ...p }: ButtonProps) {
  return (
    <button
      {...p}
      className={cx(
        'inline-flex h-9 items-center justify-center gap-2 rounded-md px-4 text-sm font-medium transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-50',
        variant === 'primary' && 'bg-accent text-white hover:bg-accent-hover',
        variant === 'secondary' && 'border border-border-strong bg-raised text-text hover:border-faint',
        variant === 'danger' && 'border border-danger/60 bg-danger/10 text-danger hover:bg-danger/20',
        variant === 'ghost' && 'text-muted hover:bg-raised hover:text-text',
        className,
      )}
    />
  )
}

const TONE: Record<Tone, string> = {
  neutral: 'bg-faint/15 text-muted border-faint/30',
  ok: 'bg-ok/15 text-ok border-ok/30',
  warn: 'bg-warn/15 text-warn border-warn/30',
  danger: 'bg-danger/15 text-danger border-danger/30',
  info: 'bg-info/15 text-info border-info/30',
  accent: 'bg-accent/30 text-accent-text border-accent/60',
}
export function Badge({ tone = 'neutral', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={cx('inline-flex items-center rounded border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap', TONE[tone])}
    >
      {children}
    </span>
  )
}

export function Panel({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx('rounded-lg border border-border bg-surface', className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-4 border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold text-text">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-xl text-text">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions}
    </div>
  )
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-3 py-8 text-sm text-muted">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-border-strong border-t-accent-text" />
      {label}…
    </div>
  )
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-border px-6 py-10 text-center">
      <p className="text-sm font-medium text-text">{title}</p>
      {children && <p className="mt-1 text-sm text-muted">{children}</p>}
    </div>
  )
}

/** One place that turns API errors into calm, specific messages. */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof ApiError ? error : null
  const title =
    e?.code === 'not_connected'
      ? 'Not connected yet'
      : e?.code === 'warehouse_busy'
        ? 'Data is being refreshed'
        : e?.status === 403
          ? 'Not available for your role'
          : e?.status === 404
            ? 'Not found'
            : e?.status === 429
              ? 'Please slow down'
              : 'Something went wrong'
  const tone = e?.code === 'not_connected' || e?.code === 'warehouse_busy' ? 'border-warn/40' : 'border-danger/40'
  return (
    <div role="alert" className={cx('rounded-md border bg-surface px-5 py-4', tone)}>
      <p className="text-sm font-semibold text-text">{title}</p>
      <p className="mt-1 text-sm text-muted">{e?.message ?? 'Unexpected error.'}</p>
      <div className="mt-3 flex items-center gap-4">
        {onRetry && e?.code !== 'not_connected' && e?.status !== 403 && (
          <Button variant="secondary" onClick={onRetry}>
            Try again
          </Button>
        )}
        {e?.requestId && <span className="font-mono text-xs text-faint">Request {e.requestId}</span>}
      </div>
    </div>
  )
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone }) {
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3">
      <p className="text-xs font-medium tracking-wide text-muted uppercase">{label}</p>
      <p
        className={cx(
          'mt-1 text-2xl font-semibold tabular-nums',
          tone === 'warn' ? 'text-warn' : tone === 'danger' ? 'text-danger' : tone === 'ok' ? 'text-ok' : 'text-text',
        )}
      >
        {value}
      </p>
      {hint && <p className="mt-0.5 text-xs text-faint">{hint}</p>}
    </div>
  )
}

export function KeyValues({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-6 gap-y-2 text-sm">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="text-text">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

type Col = {
  key: string
  label?: string
  align?: 'left' | 'right'
  wrap?: boolean // long text: wrap instead of one line
  render?: (row: Record<string, unknown>) => ReactNode
}

/** A dense table. `data` is either {columns, rows} from the API or a list of objects. */
export function DataTable({
  data,
  columns,
  onRowClick,
  rowKey,
  empty = 'Nothing to show.',
  caption,
  maxRows,
}: {
  data: Table | Record<string, unknown>[]
  columns?: Col[]
  onRowClick?: (row: Record<string, unknown>) => void
  rowKey?: string
  empty?: string
  caption?: string
  maxRows?: number
}) {
  const objects: Record<string, unknown>[] = Array.isArray(data)
    ? data
    : data.rows.map((r) => Object.fromEntries(data.columns.map((c, i) => [c, r[i]])))
  const cols: Col[] =
    columns ?? (Array.isArray(data) ? Object.keys(objects[0] ?? {}) : data.columns).map((k) => ({ key: k }))
  const shown = maxRows ? objects.slice(0, maxRows) : objects
  if (!objects.length) return <Empty title={empty} />
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full border-collapse text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead className="bg-raised">
          <tr>
            {cols.map((c) => {
              const right = c.align === 'right' || (c.align == null && isNumeric(objects[0]?.[c.key]))
              return (
                <th
                  key={c.key}
                  scope="col"
                  className={cx('h-9 px-3 text-xs font-medium whitespace-nowrap text-muted', right ? 'text-right' : 'text-left')}
                >
                  {c.label ?? humanize(c.key)}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr
              key={rowKey ? String(row[rowKey]) : i}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              onKeyDown={onRowClick ? (e) => e.key === 'Enter' && onRowClick(row) : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              className={cx('border-t border-border', onRowClick && 'cursor-pointer hover:bg-raised focus:bg-raised')}
            >
              {cols.map((c) => {
                const v = row[c.key]
                const right = c.align === 'right' || (c.align == null && isNumeric(v))
                return (
                  <td
                    key={c.key}
                    className={cx('h-9 px-3', c.wrap ? 'min-w-64 py-2 whitespace-normal' : 'whitespace-nowrap', right ? 'text-right tabular-nums' : 'text-left')}
                  >
                    {c.render ? c.render(row) : cell(v, c.key)}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {maxRows && objects.length > maxRows && (
        <p className="border-t border-border px-3 py-2 text-xs text-faint">
          Showing {maxRows} of {objects.length}.
        </p>
      )}
    </div>
  )
}

/** A modal on the native <dialog> element: focus is trapped and Esc closes it. */
export function Dialog({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-labelledby="dialog-title"
      className="m-auto w-full max-w-lg rounded-lg border border-border-strong bg-raised p-0 text-text"
    >
      <div className="px-5 py-4">
        <h2 id="dialog-title" className="text-base font-semibold">
          {title}
        </h2>
        <div className="mt-3 text-sm text-muted">{children}</div>
      </div>
    </dialog>
  )
}

export function FieldLabel({ htmlFor, children, hint }: { htmlFor: string; children: ReactNode; hint?: string }) {
  return (
    <label htmlFor={htmlFor} className="mb-1.5 block text-xs font-medium text-muted">
      {children}
      {hint && <span className="ml-2 font-normal text-faint">{hint}</span>}
    </label>
  )
}

export const inputClass =
  'w-full rounded-md border border-border-strong bg-bg px-3 py-2 text-sm text-text placeholder:text-faint focus:border-accent-text focus:outline-none'

/** Shown where a teammate's backend module is not merged into this build yet (API answers 501). */
export function NotConnected({ feature, owner }: { feature: string; owner: string }) {
  return (
    <div role="status" className="rounded-md border border-warn/40 bg-surface px-5 py-4">
      <p className="text-sm font-semibold text-text">{feature} is not connected in this build yet</p>
      <p className="mt-1 text-sm text-muted">
        The screen is ready; it starts working when {owner}'s backend is merged. Nothing else needs to change.
      </p>
    </div>
  )
}

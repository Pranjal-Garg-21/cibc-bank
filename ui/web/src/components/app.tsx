// Shared page parts, built on the shadcn components in ./ui and styled like the bank's own sites:
// white panels with a grey title bar, ledger tables, small rectangular status labels.
import type { ReactNode } from 'react'
import { ApiError } from '@/api/client'
import type { Table as TableData } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cell, humanize, isNumeric } from '@/lib/format'
import type { Tone } from '@/lib/labels'
import { cn } from '@/lib/utils'

const TONE: Record<Tone, string> = {
  neutral: 'bg-[#efefef] text-[#4a4c4e]',
  ok: 'bg-[#e4f1e8] text-ok',
  warn: 'bg-[#fbefd9] text-warn',
  danger: 'bg-[#f5e3e8] text-danger',
  info: 'bg-[#dee9f7] text-info',
  accent: 'bg-[#f5e3e8] text-brand-dark',
}

/** A status label. Colour always comes with text. */
export function StatusBadge({ tone = 'neutral', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={cn('inline-block rounded-sm px-1.5 py-px text-xs font-bold whitespace-nowrap', TONE[tone])}>
      {children}
    </span>
  )
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[28px] leading-tight text-foreground">{title}</h1>
        {description && <div className="mt-1 max-w-3xl text-[14px] text-muted-foreground">{description}</div>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

/** A panel: white box, 1px rule, grey title bar. */
export function Section({ title, description, actions, children, className, flush }: { title?: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; flush?: boolean }) {
  return (
    <section className={cn('border border-border bg-white', className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-panel px-4 py-2.5">
          <div>
            <h2 className="text-[15px] font-bold text-foreground">{title}</h2>
            {description && <p className="text-xs text-muted-foreground">{description}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className={flush ? '' : 'p-4'}>{children}</div>
    </section>
  )
}

/** A row of key figures: one bordered strip, divided into cells. */
export function Figures({ items }: { items: { label: string; value: ReactNode; note?: ReactNode; tone?: Tone }[] }) {
  return (
    <dl className="grid grid-cols-1 border border-border bg-white sm:grid-cols-2 lg:grid-flow-col lg:grid-cols-none lg:auto-cols-fr">
      {items.map((f, i) => (
        <div key={f.label} className={cn('px-4 py-3', i > 0 && 'border-t border-border sm:border-t-0 lg:border-l')}>
          <dt className="text-[13px] text-muted-foreground">{f.label}</dt>
          <dd
            className={cn(
              'mt-0.5 text-[24px] leading-tight tabular-nums',
              f.tone === 'danger' ? 'text-danger' : f.tone === 'warn' ? 'text-warn' : f.tone === 'ok' ? 'text-ok' : 'text-foreground',
            )}
          >
            {f.value}
          </dd>
          {f.note && <dd className="text-xs text-faint">{f.note}</dd>}
        </div>
      ))}
    </dl>
  )
}

export function KeyValues({ items, columns = 1 }: { items: [string, ReactNode][]; columns?: 1 | 2 }) {
  return (
    <dl className={cn('grid gap-x-10 text-sm', columns === 2 && 'sm:grid-cols-2')}>
      {items.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[minmax(10rem,auto)_1fr] gap-4 border-b border-[#e6e6e6] py-2">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="text-foreground">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

export function Loading({ rows = 4 }: { rows?: number }) {
  return (
    <div role="status" aria-live="polite" className="space-y-2">
      <span className="sr-only">Loading</span>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-8 w-full rounded-none bg-[#e9eaea]" />
      ))}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="border border-dashed border-input bg-white px-4 py-8 text-center text-sm text-muted-foreground">{children}</p>
}

/** A message box in the style of the bank's site notices: coloured left rule, plain text. */
export function Notice({ tone = 'info', title, children }: { tone?: 'info' | 'warn' | 'danger' | 'ok'; title?: ReactNode; children?: ReactNode }) {
  const rule = { info: 'border-l-info', warn: 'border-l-warn', danger: 'border-l-danger', ok: 'border-l-ok' }[tone]
  return (
    <div role={tone === 'danger' ? 'alert' : 'status'} className={cn('border border-l-4 border-border bg-white px-4 py-3 text-sm', rule)}>
      {title && <p className="font-bold text-foreground">{title}</p>}
      {children && <div className="mt-0.5 text-muted-foreground">{children}</div>}
    </div>
  )
}

/** API errors as calm, specific messages. */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof ApiError ? error : null
  const [title, tone] =
    e?.code === 'not_connected'
      ? ['Not connected yet', 'warn' as const]
      : e?.code === 'warehouse_busy'
        ? ['Data is being refreshed', 'warn' as const]
        : e?.status === 403
          ? ['Not available for your role', 'danger' as const]
          : e?.status === 404
            ? ['Not found', 'danger' as const]
            : e?.status === 429
              ? ['Please wait a moment', 'warn' as const]
              : ['Something went wrong', 'danger' as const]
  return (
    <Notice tone={tone} title={title}>
      <p>{e?.message ?? 'Unexpected error.'}</p>
      {(onRetry || e?.requestId) && (
        <div className="mt-2 flex items-center gap-4">
          {onRetry && e?.code !== 'not_connected' && e?.status !== 403 && (
            <Button size="sm" variant="outline" onClick={onRetry}>
              Try again
            </Button>
          )}
          {e?.requestId && <span className="text-xs text-faint">Reference: {e.requestId}</span>}
        </div>
      )}
    </Notice>
  )
}

/** Where a teammate's backend module is not merged into this build yet (the API answers 501). */
export function NotConnected({ feature, owner }: { feature: string; owner: string }) {
  return (
    <Notice tone="warn" title={`${feature} is not available in this build yet`}>
      This screen is ready. It starts working once {owner}'s part of the system is merged; nothing else needs to change.
    </Notice>
  )
}

type Col = {
  key: string
  label?: string
  align?: 'left' | 'right'
  wrap?: boolean
  className?: string
  render?: (row: Record<string, unknown>) => ReactNode
}

/** A ledger-style table. `data` is either {columns, rows} from the API or a list of objects. */
export function DataTable({
  data,
  columns,
  onRowClick,
  rowKey,
  empty = 'Nothing to show.',
  caption,
  maxRows,
}: {
  data: TableData | Record<string, unknown>[]
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
  const cols: Col[] = columns ?? (Array.isArray(data) ? Object.keys(objects[0] ?? {}) : data.columns).map((k) => ({ key: k }))
  const shown = maxRows ? objects.slice(0, maxRows) : objects
  if (!objects.length) return <Empty>{empty}</Empty>
  const right = (c: Col) => c.align === 'right' || (c.align == null && isNumeric(objects[0]?.[c.key]))
  return (
    <div className="overflow-x-auto border border-border bg-white">
      <Table>
        {caption && <TableCaption className="sr-only">{caption}</TableCaption>}
        <TableHeader className="bg-panel">
          <TableRow className="border-b-input hover:bg-transparent">
            {cols.map((c) => (
              <TableHead key={c.key} className={cn('h-9 px-3 text-[13px] font-bold text-foreground', right(c) && 'text-right')}>
                {c.label ?? humanize(c.key)}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {shown.map((row, i) => (
            <TableRow
              key={rowKey ? String(row[rowKey]) : i}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              onKeyDown={onRowClick ? (e) => (e.key === 'Enter' || e.key === ' ') && onRowClick(row) : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              className={cn('border-b-[#e6e6e6] hover:bg-[#f7f7f7]', onRowClick && 'cursor-pointer focus-visible:bg-[#f5e3e8]')}
            >
              {cols.map((c) => (
                <TableCell key={c.key} className={cn('h-9 px-3 text-[13.5px]', c.wrap && 'min-w-64 py-2 whitespace-normal', right(c) && 'text-right tabular-nums', c.className)}>
                  {c.render ? c.render(row) : cell(row[c.key], c.key)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {maxRows && objects.length > maxRows && (
        <p className="border-t border-border px-3 py-2 text-xs text-faint">
          Showing {maxRows} of {objects.length}.
        </p>
      )}
    </div>
  )
}

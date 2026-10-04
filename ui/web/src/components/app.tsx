// Shared page parts, built on the shadcn components in ./ui and styled like the bank's own sites:
// white panels with a grey title bar, ledger tables, small rectangular status labels.
import { useMemo, useState, type ReactNode } from 'react'
import { ArrowDown, ArrowUp, ChevronsUpDown, Inbox, Search } from 'lucide-react'
import { ApiError } from '@/api/client'
import type { Table as TableData } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
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
    <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[30px] leading-tight tracking-tight text-foreground">{title}</h1>
        {description && <div className="mt-2 max-w-[70ch] text-[15px] leading-relaxed text-muted-foreground">{description}</div>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

/** A panel: white box, 1px rule, grey title bar. */
export function Section({ title, description, actions, children, className, flush }: { title?: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; flush?: boolean }) {
  return (
    <section className={cn('ledger-panel border border-border bg-white', className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-white px-5 py-4">
          <div>
            <h2 className="text-[16px] font-bold text-foreground">{title}</h2>
            {description && <p className="mt-1 text-[13px] text-muted-foreground">{description}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className={flush ? 'ledger-flush' : 'p-5'}>{children}</div>
    </section>
  )
}

/** A row of key figures: one bordered strip, divided into cells. */
export function Figures({ items }: { items: { label: string; value: ReactNode; note?: ReactNode; tone?: Tone }[] }) {
  return (
    <dl className="grid grid-cols-1 border border-border bg-white sm:grid-cols-2 lg:grid-flow-col lg:grid-cols-none lg:auto-cols-fr">
      {items.map((f, i) => (
        <div key={f.label} className={cn('px-5 py-5', i > 0 && 'border-t border-border sm:border-t-0 lg:border-l')}>
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
  return <div className="flex flex-col items-center gap-3 bg-white px-5 py-12 text-center text-sm text-muted-foreground"><Inbox aria-hidden="true" className="size-6" strokeWidth={1.5} /><p className="max-w-[50ch]">{children}</p></div>
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
export function NotConnected({ feature }: { feature: string; owner?: string }) {
  return (
    <Notice tone="warn" title={`${feature} is not available in this build yet`}>
      Contact your administrator if you need access to this feature.
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

/** A ledger-style table. `data` is either {columns, rows} from the API or a list of objects.
 *  `sortable` turns the headers into sort buttons; `searchable` adds a box that filters the rows as you type. */
export function DataTable({
  data,
  columns,
  onRowClick,
  rowKey,
  empty = 'Nothing to show.',
  caption,
  maxRows,
  showEmptyHeaders = false,
  sortable = false,
  searchable = false,
}: {
  data: TableData | Record<string, unknown>[]
  columns?: Col[]
  onRowClick?: (row: Record<string, unknown>) => void
  rowKey?: string
  empty?: string
  caption?: string
  maxRows?: number
  showEmptyHeaders?: boolean
  sortable?: boolean
  searchable?: boolean
}) {
  const [term, setTerm] = useState('')
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null)
  const objects: Record<string, unknown>[] = useMemo(
    () => (Array.isArray(data) ? data : data.rows.map((r) => Object.fromEntries(data.columns.map((c, i) => [c, r[i]])))),
    [data],
  )
  const cols: Col[] = columns ?? (Array.isArray(data) ? Object.keys(objects[0] ?? {}) : data.columns).map((k) => ({ key: k }))
  const visible = useMemo(() => {
    const t = term.trim().toLowerCase()
    let rows = t ? objects.filter((r) => cols.some((c) => String(r[c.key] ?? '').toLowerCase().includes(t))) : objects
    if (sort) {
      rows = [...rows].sort((a, b) => {
        const x = a[sort.key], y = b[sort.key]
        if (x == null) return 1
        if (y == null) return -1
        return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })) * sort.dir
      })
    }
    return rows
  }, [objects, term, sort, cols])
  const shown = maxRows ? visible.slice(0, maxRows) : visible
  if (!objects.length && (!showEmptyHeaders || !cols.length)) return <Empty>{empty}</Empty>
  const right = (c: Col) => c.align === 'right' || (c.align == null && isNumeric(objects[0]?.[c.key]))
  return (
    <div>
      {searchable && (
        <div className="relative border-b border-border bg-white p-3">
          <Search className="pointer-events-none absolute top-1/2 left-5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input value={term} onChange={(e) => setTerm(e.target.value)} placeholder="Filter this list" aria-label="Filter this list" className="h-9 max-w-sm rounded-sm pl-8" />
          {term && <span className="ml-3 text-xs text-muted-foreground" role="status">{visible.length} of {objects.length}</span>}
        </div>
      )}
      <div className="ledger-table overflow-x-auto rounded-sm border border-border bg-white">
        <Table aria-label={caption}>
          {caption && <TableCaption className="sr-only">{caption}</TableCaption>}
          <TableHeader className="bg-panel">
            <TableRow className="border-b-input hover:bg-transparent">
              {cols.map((c) => {
                const active = sort?.key === c.key
                const text = c.label ?? humanize(c.key)
                return (
                  <TableHead key={c.key} aria-sort={active ? (sort!.dir === 1 ? 'ascending' : 'descending') : undefined}
                    className={cn('h-11 px-5 text-[13px] font-bold text-foreground', right(c) && 'text-right')}>
                    {sortable ? (
                      <button type="button" className={cn('inline-flex items-center gap-1 hover:text-brand', right(c) && 'flex-row-reverse')}
                        onClick={() => setSort(!active ? { key: c.key, dir: 1 } : sort!.dir === 1 ? { key: c.key, dir: -1 } : null)}>
                        {text}
                        {active ? (sort!.dir === 1 ? <ArrowUp className="size-3.5" aria-hidden="true" /> : <ArrowDown className="size-3.5" aria-hidden="true" />) : <ChevronsUpDown className="size-3.5 text-faint" aria-hidden="true" />}
                      </button>
                    ) : text}
                  </TableHead>
                )
              })}
            </TableRow>
          </TableHeader>
          <TableBody>
            {!shown.length && <TableRow><TableCell colSpan={cols.length} className="px-5 py-8 text-sm text-muted-foreground">{term ? 'No rows match your filter.' : empty}</TableCell></TableRow>}
            {shown.map((row, i) => (
              <TableRow
                key={rowKey ? String(row[rowKey]) : i}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                onKeyDown={onRowClick ? (e) => {
                  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onRowClick(row) }
                  if (e.key === 'ArrowDown') { e.preventDefault(); (e.currentTarget.nextElementSibling as HTMLElement | null)?.focus() }
                  if (e.key === 'ArrowUp') { e.preventDefault(); (e.currentTarget.previousElementSibling as HTMLElement | null)?.focus() }
                } : undefined}
                tabIndex={onRowClick ? 0 : undefined}
                className={cn('border-b-[#e6e6e6] hover:bg-[#f7f7f7]', onRowClick && 'cursor-pointer focus-visible:bg-[#f5e3e8]')}
              >
                {cols.map((c) => (
                  <TableCell key={c.key} className={cn('h-14 px-5 py-3 text-[14px]', c.wrap && 'min-w-64 whitespace-normal', right(c) && 'text-right tabular-nums', c.className)}>
                    {c.render ? c.render(row) : cell(row[c.key], c.key)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {maxRows && visible.length > maxRows && (
          <p className="border-t border-border px-3 py-2 text-xs text-faint">
            Showing {maxRows} of {visible.length}.
          </p>
        )}
      </div>
    </div>
  )
}

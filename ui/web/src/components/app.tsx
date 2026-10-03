// App-level building blocks on top of the shadcn components in ./ui.
import { AlertTriangle, Ban, CircleSlash, Clock, Plug, SearchX, ServerCrash } from 'lucide-react'
import type { ReactNode } from 'react'
import { ApiError } from '@/api/client'
import type { Table as TableData } from '@/api/types'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { cell, humanize, isNumeric } from '@/lib/format'
import type { Tone } from '@/lib/labels'
import { cn } from '@/lib/utils'

const TONE: Record<Tone, string> = {
  neutral: 'border-faint/40 bg-faint/10 text-muted-foreground',
  ok: 'border-ok/40 bg-ok/10 text-ok',
  warn: 'border-warn/40 bg-warn/10 text-warn',
  danger: 'border-danger/40 bg-danger/10 text-danger',
  info: 'border-info/40 bg-info/10 text-info',
  accent: 'border-primary/50 bg-primary/15 text-info',
}

/** A status label. Colour always comes with text (never colour alone). */
export function StatusBadge({ tone = 'neutral', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <Badge variant="outline" title={title} className={cn('rounded-md font-medium', TONE[tone])}>
      {children}
    </Badge>
  )
}

export function PageHeader({ title, description, actions, eyebrow }: { title: string; description?: ReactNode; actions?: ReactNode; eyebrow?: string }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4 border-b border-border pb-5">
      <div>
        {eyebrow && <p className="mb-1 text-xs font-medium tracking-[0.08em] text-gold uppercase">{eyebrow}</p>}
        <h1 className="text-2xl text-foreground">{title}</h1>
        {description && <div className="mt-1 max-w-3xl text-sm text-muted-foreground">{description}</div>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Section({ title, description, actions, children, className }: { title?: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <Card className={cn('gap-0 py-0', className)}>
      {(title || actions) && (
        <CardHeader className="border-b border-border py-3.5">
          <CardTitle className="text-sm">{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
          {actions && <CardAction>{actions}</CardAction>}
        </CardHeader>
      )}
      <CardContent className="py-4">{children}</CardContent>
    </Card>
  )
}

export function Stat({ label, value, hint, tone, icon }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone; icon?: ReactNode }) {
  return (
    <Card className="gap-1 py-4">
      <CardContent className="px-4">
        <div className="flex items-center justify-between">
          <p className="text-[11px] font-medium tracking-[0.06em] text-muted-foreground uppercase">{label}</p>
          {icon && <span className="text-faint">{icon}</span>}
        </div>
        <p
          className={cn(
            'mt-1.5 text-2xl font-semibold tabular-nums',
            tone === 'warn' ? 'text-warn' : tone === 'danger' ? 'text-danger' : tone === 'ok' ? 'text-ok' : 'text-foreground',
          )}
        >
          {value}
        </p>
        {hint && <p className="mt-0.5 text-xs text-faint">{hint}</p>}
      </CardContent>
    </Card>
  )
}

export function KeyValues({ items, columns = 1 }: { items: [string, ReactNode][]; columns?: 1 | 2 }) {
  return (
    <dl className={cn('grid gap-x-8 gap-y-3 text-sm', columns === 2 ? 'sm:grid-cols-2' : '')}>
      {items.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[minmax(9rem,auto)_1fr] gap-4 border-b border-border/60 pb-2 last:border-b-0">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="text-foreground">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

export function Loading({ rows = 5 }: { rows?: number }) {
  return (
    <div role="status" aria-live="polite" aria-label="Loading" className="space-y-2">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-9 w-full" />
      ))}
    </div>
  )
}

export function Empty({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center rounded-lg border border-dashed border-border px-6 py-12 text-center">
      <span className="mb-3 text-faint">{icon ?? <CircleSlash className="size-6" />}</span>
      <p className="text-sm font-medium text-foreground">{title}</p>
      {children && <p className="mt-1 max-w-md text-sm text-muted-foreground">{children}</p>}
    </div>
  )
}

/** One place that turns API errors into calm, specific messages. */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof ApiError ? error : null
  const [title, Icon, warn] =
    e?.code === 'not_connected'
      ? ['Not connected yet', Plug, true]
      : e?.code === 'warehouse_busy'
        ? ['Data is being refreshed', Clock, true]
        : e?.status === 403
          ? ['Not available for your role', Ban, false]
          : e?.status === 404
            ? ['Not found', SearchX, false]
            : e?.status === 429
              ? ['Please slow down', Clock, true]
              : ['Something went wrong', ServerCrash, false]
  return (
    <Alert variant={warn ? 'default' : 'destructive'} className={cn('px-4 py-3', warn && 'border-warn/40 text-warn')}>
      <Icon />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="text-muted-foreground">
        <p>{e?.message ?? 'Unexpected error.'}</p>
        <div className="mt-2 flex items-center gap-4">
          {onRetry && e?.code !== 'not_connected' && e?.status !== 403 && (
            <Button size="sm" variant="outline" onClick={onRetry}>
              Try again
            </Button>
          )}
          {e?.requestId && <span className="font-mono text-xs text-faint">Reference {e.requestId}</span>}
        </div>
      </AlertDescription>
    </Alert>
  )
}

/** Shown where a teammate's backend module is not merged into this build yet (the API answers 501). */
export function NotConnected({ feature, owner }: { feature: string; owner: string }) {
  return (
    <Alert className="border-warn/40 px-4 py-3">
      <AlertTriangle className="text-warn" />
      <AlertTitle className="text-warn">{feature} is not connected in this build yet</AlertTitle>
      <AlertDescription className="text-muted-foreground">
        The screen is ready and starts working when {owner}'s backend is merged. Nothing else needs to change.
      </AlertDescription>
    </Alert>
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

/** A dense ledger-style table. `data` is either {columns, rows} from the API or a list of objects. */
export function DataTable({
  data,
  columns,
  onRowClick,
  rowKey,
  empty = 'Nothing to show.',
  caption,
  maxRows,
  selectedKey,
}: {
  data: TableData | Record<string, unknown>[]
  columns?: Col[]
  onRowClick?: (row: Record<string, unknown>) => void
  rowKey?: string
  empty?: string
  caption?: string
  maxRows?: number
  selectedKey?: string | null
}) {
  const objects: Record<string, unknown>[] = Array.isArray(data)
    ? data
    : data.rows.map((r) => Object.fromEntries(data.columns.map((c, i) => [c, r[i]])))
  const cols: Col[] = columns ?? (Array.isArray(data) ? Object.keys(objects[0] ?? {}) : data.columns).map((k) => ({ key: k }))
  const shown = maxRows ? objects.slice(0, maxRows) : objects
  if (!objects.length) return <Empty title={empty} />
  const right = (c: Col) => c.align === 'right' || (c.align == null && isNumeric(objects[0]?.[c.key]))
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <Table>
        {caption && <TableCaption className="sr-only">{caption}</TableCaption>}
        <TableHeader className="bg-secondary/60">
          <TableRow className="hover:bg-transparent">
            {cols.map((c) => (
              <TableHead
                key={c.key}
                className={cn('h-9 px-3 text-[11px] font-semibold tracking-[0.05em] text-muted-foreground uppercase', right(c) && 'text-right')}
              >
                {c.label ?? humanize(c.key)}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {shown.map((row, i) => {
            const key = rowKey ? String(row[rowKey]) : String(i)
            return (
              <TableRow
                key={key}
                data-state={selectedKey === key ? 'selected' : undefined}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                onKeyDown={onRowClick ? (e) => (e.key === 'Enter' || e.key === ' ') && onRowClick(row) : undefined}
                tabIndex={onRowClick ? 0 : undefined}
                className={cn(onRowClick && 'cursor-pointer focus-visible:bg-accent focus-visible:outline-none', 'data-[state=selected]:bg-primary/15')}
              >
                {cols.map((c) => (
                  <TableCell
                    key={c.key}
                    className={cn('h-10 px-3', c.wrap ? 'min-w-64 py-2 whitespace-normal' : '', right(c) && 'text-right tabular-nums', c.className)}
                  >
                    {c.render ? c.render(row) : cell(row[c.key], c.key)}
                  </TableCell>
                ))}
              </TableRow>
            )
          })}
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

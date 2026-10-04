import { cn } from '@/lib/utils'

/** Existing brand wordmark, shared without loading the signed-in workspace on the sign-in page. */
export function Wordmark({ small }: { small?: boolean }) {
  return <span className="flex items-baseline gap-3">
    <span className={cn('font-bold tracking-tight text-brand', small ? 'text-[20px]' : 'text-[24px]')}>TrustIssues</span>
    <span className="border-l border-input pl-3 text-[14px] text-muted-foreground">Collections</span>
  </span>
}

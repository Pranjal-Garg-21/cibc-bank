import { cn } from '@/lib/utils'

/** The CIBC mark: a red keystone in two halves. Drawn inline (no image request, nothing for the CSP to allow).
 * A stand-in drawn for this console; swap in the bank's official logo file when brand approves one. */
export function CibcLogo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 120 40" role="img" aria-label="CIBC" className={className}>
      <path d="M2 4h15l-6 16 6 16H2z" fill="var(--color-brand)" />
      <path d="M21 4h15v32H21l6-16z" fill="var(--color-brand)" />
      <text x="42" y="31" fill="#2b2e31" fontFamily="Arial, Helvetica, sans-serif" fontSize="27" fontWeight="700" letterSpacing="0.5">
        CIBC
      </text>
    </svg>
  )
}

/** The console's name beside the bank's mark, shared without loading the signed-in workspace on the sign-in page. */
export function Wordmark({ small }: { small?: boolean }) {
  return (
    <span className="flex items-center gap-3">
      <CibcLogo className={cn('w-auto', small ? 'h-7' : 'h-8')} />
      <span className={cn('border-l border-input pl-3 font-bold tracking-tight text-foreground', small ? 'text-[18px]' : 'text-[20px]')}>
        CIBC Collections
      </span>
    </span>
  )
}

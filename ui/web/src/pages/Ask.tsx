import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '@/api/client'
import type { AnswerCard } from '@/api/types'
import { useMe } from '@/auth'
import { DataTable, ErrorState, NotConnected, PageHeader, Section, StatusBadge } from '@/components/app'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { isoDate, toCsv } from '@/lib/format'
import { ANSWER_PATH } from '@/lib/labels'

const EXAMPLES = [
  'How many promises to pay were broken last month?',
  'What is the cure rate by delinquency bucket?',
  'What does the hardship policy say about payment deferrals?',
]

export function Ask() {
  const me = useMe()
  const [question, setQuestion] = useState('')
  const [history, setHistory] = useState<AnswerCard[]>([]) // this page only; never stored in the browser
  const ask = useMutation({
    mutationFn: (q: string) => api.post<AnswerCard>('/api/ask', { question: q }),
    onSuccess: (card) => setHistory((h) => [card, ...h].slice(0, 10)),
  })
  const submit = (q: string) => {
    const text = q.trim()
    if (text.length >= 3 && me.features.ask) ask.mutate(text)
  }

  return (
    <>
      <PageHeader
        title="Ask"
        description="Ask about collections performance or policy. Where a certified metric exists, the answer uses it and says so."
      />
      {!me.features.ask && (
        <div className="mb-5">
          <NotConnected feature="Ask" owner="P2" />
        </div>
      )}
      <form
        className={`border border-border bg-white p-4 ${me.features.ask ? '' : 'pointer-events-none opacity-60'}`}
        aria-disabled={!me.features.ask}
        onSubmit={(e) => {
          e.preventDefault()
          submit(question)
        }}
      >
        <Label htmlFor="question" className="mb-1.5 text-[14px] font-bold">
          Your question
        </Label>
        <Textarea
          id="question"
          rows={3}
          maxLength={500}
          className="min-h-20 resize-none rounded-sm text-[15px]"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit(question)
            }
          }}
        />
        <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
          <div className="text-[13px] text-muted-foreground">
            <p>For example:</p>
            <ul className="mt-1 space-y-0.5">
              {EXAMPLES.map((ex) => (
                <li key={ex}>
                  <button type="button" className="text-left text-brand hover:text-brand-dark hover:underline" onClick={() => setQuestion(ex)}>
                    {ex}
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-faint tabular-nums">{question.length}/500</span>
            <Button type="submit" size="lg" disabled={!me.features.ask || ask.isPending || question.trim().length < 3}>
              {ask.isPending ? 'Working…' : 'Ask'}
            </Button>
          </div>
        </div>
      </form>

      <div aria-live="polite" className="mt-5 space-y-5">
        {ask.error && <ErrorState error={ask.error} onRetry={() => submit(question)} />}
        {history.map((card, i) => (
          <Answer key={`${i}-${card.question}`} card={card} />
        ))}
      </div>
    </>
  )
}

function Answer({ card }: { card: AnswerCard }) {
  const [open, setOpen] = useState(false)
  const path = ANSWER_PATH[card.path] ?? ANSWER_PATH.cannot_answer
  const download = () => {
    const blob = new Blob([toCsv(card.columns, card.rows)], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `answer-${isoDate(card.as_of_date)}.csv`
    a.click()
    URL.revokeObjectURL(url)
    void api.post('/api/ask/exported', { question: card.question, rows: card.rows.length }).catch(() => null)
  }
  return (
    <Section
      title={card.question}
      actions={
        <span className="flex items-center gap-2">
          <StatusBadge tone={path.tone} title={path.help}>
            {path.label}
          </StatusBadge>
          <span className="text-xs text-muted-foreground">As of {isoDate(card.as_of_date)}</span>
        </span>
      }
    >
      {/* Plain text only: model output is never rendered as HTML (design §9.6). */}
      {card.answer && <p className="text-[15px] leading-relaxed whitespace-pre-wrap">{card.answer}</p>}
      {card.refused && card.refusal_reason && <p className="mt-2 text-xs text-faint">Reason code: {card.refusal_reason}</p>}
      {card.columns.length > 0 && (
        <div className="mt-4">
          <DataTable data={{ columns: card.columns, rows: card.rows }} maxRows={50} caption="Answer data" />
          <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
            <span>{card.truncated ? 'First 500 rows. ' : ''}Customer information is masked for your role.</span>
            <Button variant="link" size="sm" onClick={download}>
              Download CSV
            </Button>
          </div>
        </div>
      )}
      {card.notes.length > 0 && (
        <ul className="mt-4 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
          {card.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      {(card.sql || card.metric_id || card.sources.length > 0) && (
        <div className="mt-4 border-t border-border pt-2">
          <Button variant="link" size="sm" className="px-0" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? 'Hide' : 'Show'} how this was answered
          </Button>
          {open && (
            <div className="mt-2 space-y-2 text-sm">
              {card.metric_id && (
                <p>
                  Metric: <span className="font-mono">{card.metric_id}</span>
                  {Object.keys(card.parameters).length > 0 && <span className="font-mono text-xs"> {JSON.stringify(card.parameters)}</span>}
                </p>
              )}
              {card.sql && <pre className="overflow-x-auto border border-border bg-panel p-3 font-mono text-xs">{card.sql}</pre>}
              {card.sources.length > 0 && <p className="text-xs text-muted-foreground">Sources: {card.sources.join(', ')}</p>}
            </div>
          )}
        </div>
      )}
    </Section>
  )
}

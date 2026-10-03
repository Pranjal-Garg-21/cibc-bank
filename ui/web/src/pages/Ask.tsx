import { useMutation } from '@tanstack/react-query'
import { CheckCircle2, Circle, Download, Loader2, Send } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import type { AnswerCard } from '@/api/types'
import { useMe } from '@/auth'
import { DataTable, ErrorState, NotConnected, PageHeader, StatusBadge } from '@/components/app'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { isoDate, toCsv } from '@/lib/format'
import { ANSWER_PATH } from '@/lib/labels'

const EXAMPLES = [
  'How many promises to pay were broken last month?',
  'What is the cure rate by delinquency bucket?',
  'What does the hardship policy say about payment deferrals?',
  'Which channels had the highest right-party contact rate last week?',
]
const STAGES = ['Checking the question', 'Finding the certified metric or policy', 'Running the query', 'Writing the answer']

export function Ask() {
  const me = useMe()
  const [question, setQuestion] = useState('')
  const [history, setHistory] = useState<AnswerCard[]>([]) // page memory only, never browser storage
  const ask = useMutation({
    mutationFn: (q: string) => api.post<AnswerCard>('/api/ask', { question: q }),
    onSuccess: (card) => setHistory((h) => [card, ...h].slice(0, 10)),
  })
  const [stage, setStage] = useState(0)
  useEffect(() => {
    if (!ask.isPending) return setStage(0)
    const t = window.setInterval(() => setStage((s) => Math.min(s + 1, STAGES.length - 1)), 2500)
    return () => window.clearInterval(t)
  }, [ask.isPending])
  const submit = (q: string) => {
    const text = q.trim()
    if (text.length >= 3 && me.features.ask) ask.mutate(text)
  }

  return (
    <>
      <PageHeader
        eyebrow="Collections operations"
        title="Ask"
        description="Questions about collections performance and policy. Answers use certified metric definitions wherever one exists, and say so."
      />
      {!me.features.ask && (
        <div className="mb-6">
          <NotConnected feature="Ask" owner="P2" />
        </div>
      )}
      <Card className={me.features.ask ? '' : 'pointer-events-none opacity-50'} aria-disabled={!me.features.ask}>
        <CardContent>
          <form
            onSubmit={(e) => {
              e.preventDefault()
              submit(question)
            }}
          >
            <label htmlFor="question" className="sr-only">
              Question
            </label>
            <Textarea
              id="question"
              rows={3}
              maxLength={500}
              className="min-h-20 resize-none text-[15px]"
              placeholder="Ask a question about collections performance or policy…"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  submit(question)
                }
              }}
            />
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap gap-2">
                {EXAMPLES.map((ex) => (
                  <Button
                    type="button"
                    key={ex}
                    variant="outline"
                    size="sm"
                    className="rounded-full font-normal text-muted-foreground"
                    onClick={() => {
                      setQuestion(ex)
                      submit(ex)
                    }}
                  >
                    {ex}
                  </Button>
                ))}
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-faint tabular-nums">{question.length}/500</span>
                <Button type="submit" size="lg" disabled={!me.features.ask || ask.isPending || question.trim().length < 3}>
                  <Send /> Ask
                </Button>
              </div>
            </div>
          </form>
        </CardContent>
      </Card>

      <div aria-live="polite" className="mt-6 space-y-4">
        {ask.isPending && (
          <Card>
            <CardContent>
              <ol className="space-y-2 text-sm">
                {STAGES.map((s, i) => (
                  <li key={s} className={`flex items-center gap-2 ${i < stage ? 'text-muted-foreground' : i === stage ? 'text-foreground' : 'text-faint'}`}>
                    {i < stage ? <CheckCircle2 className="size-4 text-ok" /> : i === stage ? <Loader2 className="size-4 animate-spin text-info" /> : <Circle className="size-4" />}
                    {s}
                  </li>
                ))}
              </ol>
            </CardContent>
          </Card>
        )}
        {ask.error && <ErrorState error={ask.error} onRetry={() => submit(question)} />}
        {history.map((card, i) => (
          <Answer key={`${i}-${card.question}`} card={card} latest={i === 0} />
        ))}
      </div>
    </>
  )
}

function Answer({ card, latest }: { card: AnswerCard; latest: boolean }) {
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
    <Card className={latest ? '' : 'opacity-80'}>
      <CardHeader className="border-b border-border">
        <CardTitle className="font-normal text-muted-foreground">{card.question}</CardTitle>
        <CardAction className="flex items-center gap-2">
          <StatusBadge tone={path.tone} title={path.help}>
            {path.label}
          </StatusBadge>
          <StatusBadge>As of {isoDate(card.as_of_date)}</StatusBadge>
        </CardAction>
      </CardHeader>
      <CardContent>
        {/* Answers are plain text, never HTML: model output is untrusted (design §9.6). */}
        {card.answer && <p className="text-[15px] leading-relaxed whitespace-pre-wrap text-foreground">{card.answer}</p>}
        {card.refused && card.refusal_reason && <p className="mt-2 text-xs text-faint">Reason code: {card.refusal_reason}</p>}
        {card.columns.length > 0 && (
          <div className="mt-4">
            <DataTable data={{ columns: card.columns, rows: card.rows }} maxRows={50} caption="Answer data" />
            <div className="mt-2 flex items-center justify-between">
              <span className="text-xs text-faint">{card.truncated ? 'First 500 rows. ' : ''}Masked for your role.</span>
              <Button variant="ghost" size="sm" onClick={download}>
                <Download /> Download CSV
              </Button>
            </div>
          </div>
        )}
        {card.notes.length > 0 && (
          <ul className="mt-4 space-y-1 border-l-2 border-warn/50 pl-3 text-sm text-muted-foreground">
            {card.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        )}
        {(card.sql || card.metric_id || card.sources.length > 0) && (
          <div className="mt-4 border-t border-border pt-3">
            <Button variant="link" size="sm" className="px-0" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
              {open ? 'Hide' : 'Show'} how this was answered
            </Button>
            {open && (
              <div className="mt-2 space-y-3 text-sm">
                {card.metric_id && (
                  <p className="text-muted-foreground">
                    Metric <span className="font-mono text-foreground">{card.metric_id}</span>
                    {Object.keys(card.parameters).length > 0 && <span className="font-mono text-xs"> · {JSON.stringify(card.parameters)}</span>}
                  </p>
                )}
                {card.sql && (
                  <pre className="overflow-x-auto rounded-md border border-border bg-background p-3 font-mono text-xs text-muted-foreground">{card.sql}</pre>
                )}
                {card.sources.length > 0 && <p className="text-xs text-faint">Sources: {card.sources.join(', ')}</p>}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

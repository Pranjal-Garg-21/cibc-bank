import { useMutation } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { AnswerCard } from '../api/types'
import { useMe } from '../auth'
import { Badge, Button, DataTable, ErrorState, NotConnected, PageHeader, Panel, inputClass } from '../components/ui'
import { isoDate, toCsv } from '../lib/format'
import { ANSWER_PATH } from '../lib/labels'

const EXAMPLES = [
  'How many promises to pay were broken last month?',
  'What is the cure rate by delinquency bucket?',
  'What does the hardship policy say about payment deferrals?',
  'Which channels had the highest right-party contact rate last week?',
]
const STAGES = ['Checking the question', 'Finding the metric or policy', 'Running the query', 'Writing the answer']

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
        title="Ask"
        subtitle="Questions about collections performance and policy. Answers use certified metrics where one exists."
      />
      {!me.features.ask && (
        <div className="mb-6">
          <NotConnected feature="Ask" owner="P2" />
        </div>
      )}
      <form
        aria-disabled={!me.features.ask}
        className={`rounded-lg border border-border bg-surface p-4 ${me.features.ask ? '' : 'pointer-events-none opacity-50'}`}
        onSubmit={(e) => {
          e.preventDefault()
          submit(question)
        }}
      >
        <label htmlFor="question" className="sr-only">
          Question
        </label>
        <textarea
          id="question"
          rows={3}
          maxLength={500}
          className={`${inputClass} resize-none text-[15px]`}
          placeholder="Ask a question…"
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
              <button
                type="button"
                key={ex}
                onClick={() => {
                  setQuestion(ex)
                  submit(ex)
                }}
                className="rounded-full border border-border px-3 py-1 text-xs text-muted hover:border-border-strong hover:text-text"
              >
                {ex}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-faint">{question.length}/500</span>
            <Button type="submit" variant="primary" disabled={!me.features.ask || ask.isPending || question.trim().length < 3}>
              Ask
            </Button>
          </div>
        </div>
      </form>

      <div aria-live="polite" className="mt-6 space-y-4">
        {ask.isPending && (
          <div className="rounded-lg border border-border bg-surface px-4 py-4">
            <ol className="space-y-1.5 text-sm">
              {STAGES.map((s, i) => (
                <li key={s} className={i < stage ? 'text-muted' : i === stage ? 'text-text' : 'text-faint'}>
                  <span className="mr-2 inline-block w-4 text-center">{i < stage ? '✓' : i === stage ? '•' : ''}</span>
                  {s}
                </li>
              ))}
            </ol>
          </div>
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
    <Panel
      className={latest ? '' : 'opacity-80'}
      title={<span className="font-normal text-muted">{card.question}</span>}
      actions={
        <div className="flex items-center gap-2">
          <Badge tone={path.tone} title={path.help}>
            {path.label}
          </Badge>
          <Badge>As of {isoDate(card.as_of_date)}</Badge>
        </div>
      }
    >
      {/* Answers are plain text, never HTML: model output is untrusted (design §9.6). */}
      {card.answer && <p className="text-[15px] leading-relaxed whitespace-pre-wrap text-text">{card.answer}</p>}
      {card.refused && card.refusal_reason && (
        <p className="mt-2 text-xs text-faint">Reason code: {card.refusal_reason}</p>
      )}
      {card.columns.length > 0 && (
        <div className="mt-4">
          <DataTable data={{ columns: card.columns, rows: card.rows }} maxRows={50} caption="Answer data" />
          <div className="mt-2 flex items-center justify-between">
            <span className="text-xs text-faint">
              {card.truncated ? 'First 500 rows. ' : ''}Masked for your role.
            </span>
            <Button variant="ghost" onClick={download}>
              Download CSV
            </Button>
          </div>
        </div>
      )}
      {card.notes.length > 0 && (
        <ul className="mt-4 space-y-1 border-l-2 border-warn/50 pl-3 text-sm text-muted">
          {card.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      {(card.sql || card.metric_id || card.sources.length > 0) && (
        <div className="mt-4 border-t border-border pt-3">
          <button
            type="button"
            className="text-xs font-medium text-accent-text hover:underline"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? 'Hide' : 'Show'} how this was answered
          </button>
          {open && (
            <div className="mt-3 space-y-3 text-sm">
              {card.metric_id && (
                <p className="text-muted">
                  Metric <span className="font-mono text-text">{card.metric_id}</span>
                  {Object.keys(card.parameters).length > 0 && (
                    <span className="font-mono text-xs"> · {JSON.stringify(card.parameters)}</span>
                  )}
                </p>
              )}
              {card.sql && (
                <pre className="overflow-x-auto rounded-md border border-border bg-bg p-3 font-mono text-xs text-muted">
                  {card.sql}
                </pre>
              )}
              {card.sources.length > 0 && <p className="text-xs text-faint">Sources: {card.sources.join(', ')}</p>}
            </div>
          )}
        </div>
      )}
    </Panel>
  )
}

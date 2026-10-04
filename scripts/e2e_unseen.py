"""Frozen, observational Ask evaluation. Never alters application code or warehouse data.

Run from the repo root: PYTHONPATH=src .venv/bin/python scripts/e2e_unseen.py
The first pass sets llm.cache=None; a separate cache records its successful replies
for the second pass. This measures actual cache replay, rather than a second cold run.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import tempfile
import time
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from numbers import Real
from pathlib import Path

from trustissues.ask.access import case_customer_resolver
from trustissues.ask.rag.policy_search import PolicyIndex
from trustissues.ask.rag_handler import make_rag_handler
from trustissues.ask.service import ask
from trustissues.ask.types import UserContext
from trustissues.common import config
from trustissues.common.db import connect
from trustissues.common.llm import LLM, ResponseCache, _load_dotenv

AS_OF = date(2026, 9, 28)
ROOT = config.REPO_ROOT
FIELDS = ('id', 'path', 'refused', 'certified', 'answer', 'sql', 'notes', 'seconds')
GROUP_COLUMNS = {'product', 'primary_product', 'queue', 'agent_site', 'contact_hour_local',
                 'reported_bucket', 'province', 'direction', 'assignment_method'}

# Added during post-run audit: the segment tables alone do not verify the headline.
# These independently authored totals are also recorded in the checks JSON.
DRIVER_TOTAL_SQL = {
    'D01': "SELECT 100.0*sum(CASE WHEN ptp_made_flag THEN 1 ELSE 0 END)/count(*) AS rate FROM gold.semantic_contacts WHERE rpc_flag IS TRUE AND contact_ts_local>=DATE '2026-04-01' AND contact_ts_local<DATE '2026-06-01' GROUP BY CASE WHEN contact_ts_local<DATE '2026-05-01' THEN 0 ELSE 1 END ORDER BY CASE WHEN contact_ts_local<DATE '2026-05-01' THEN 0 ELSE 1 END",
    'D02': "SELECT count(*) AS calls FROM gold.semantic_contacts WHERE channel='call' AND direction='outbound' AND contact_ts_local>=DATE '2026-05-04' AND contact_ts_local<DATE '2026-05-18' GROUP BY CASE WHEN contact_ts_local<DATE '2026-05-11' THEN 0 ELSE 1 END ORDER BY CASE WHEN contact_ts_local<DATE '2026-05-11' THEN 0 ELSE 1 END",
}


def driver_summary_check(answer, expected):
    match = re.search(r'moved from ([\d,.]+) to ([\d,.]+) \(([+-]?[\d,.]+)\)', answer)
    if not match:
        return ['driver summary totals missing']
    actual = [float(value.replace(',', '')) for value in match.groups()]
    return [] if all(equal_value(a, b) for a, b in zip(actual, expected)) else ['driver summary totals differ from independent reference']


def number(value):
    return isinstance(value, (Real, Decimal)) and not isinstance(value, bool)


def protected_manifest():
    files = [p for folder in ('src', 'config', 'tests', 'eval') for p in (ROOT / folder).rglob('*')
             if p.is_file() and p.suffix in {'.py', '.csv', '.yaml', '.yml', '.md', '.json', '.jsonl'}
             and not p.name.startswith('e2e_unseen')]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


class MeteredBackend:
    def __init__(self, backend):
        self.backend = backend
        self.attempts = 0
        self.successes = 0
        self.rate_limits = 0

    def complete(self, **kwargs):
        # Extra backoff specifically for 429, on the same configured provider/model.
        for attempt in range(4):
            self.attempts += 1
            try:
                result = self.backend.complete(**kwargs)
                self.successes += 1
                return result
            except Exception as error:
                if '429' not in str(error):
                    raise
                self.rate_limits += 1
                if attempt == 3:
                    raise
                delay = 10 * 2 ** attempt
                print(f'Rate limit: retrying the same model after {delay}s', flush=True)
                time.sleep(delay)


class MeasuredLLM(LLM):
    def __init__(self, cache, *, uncached=False):
        super().__init__(cache=cache, sleep=self.backoff)
        self.replay_cache = cache if uncached else None
        if uncached:
            self.cache = None
        self.meter = MeteredBackend(self._backend())
        self.backend = self.meter
        self.logical_calls = 0
        self.cache_hits = 0
        self.call_errors = 0
        self.backoffs = []

    def backoff(self, seconds):
        self.backoffs.append(seconds)
        print(f'LLM retry backoff: {seconds}s', flush=True)
        time.sleep(seconds)

    def complete(self, **kwargs):
        self.logical_calls += 1
        try:
            result = super().complete(**kwargs)
        except Exception:
            self.call_errors += 1
            raise
        self.cache_hits += int(result.cached)
        if self.replay_cache is not None:
            key = hashlib.sha256(json.dumps([
                self.models[kwargs.get('tier', 'answer')], kwargs['system'], kwargs['user'],
                kwargs.get('max_tokens', 1024), kwargs.get('temperature', 0.0), kwargs['prompt_version'],
                kwargs.get('json_output', False), self.cfg.get('extra_body')], ensure_ascii=False).encode()).hexdigest()
            self.replay_cache.put(key, result.text)
        return result

    def counts(self):
        return dict(logical_calls=self.logical_calls, provider_attempts=self.meter.attempts,
                    provider_successes=self.meter.successes, cache_hits=self.cache_hits,
                    rate_limits=self.meter.rate_limits, call_errors=self.call_errors,
                    retry_backoffs=self.backoffs)


def scrub_secret(text):
    text = str(text)
    key = os.environ.get('OPENROUTER_API_KEY')
    return text.replace(key, '[API_KEY]') if key else text


def names(con):
    # Exact names from the data, never persisted or sent to the model by this tester.
    values = con.execute("SELECT full_name FROM gold.c360_customer WHERE full_name IS NOT NULL "
                         "UNION SELECT agent_display_name FROM curated.agents WHERE agent_display_name IS NOT NULL").fetchall()
    normalize = lambda value: ' '.join(re.findall(r"[^\W\d_]+", value.casefold()))
    return {normalize(value) for (value,) in values if len(normalize(value).split()) >= 2}


def pii_hits(text, personal_names):
    patterns = {
        'email': r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b',
        'phone': r'(?<!\w)(?:\+?1[ .-]?)?\(?[2-9]\d{2}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\w)',
        'card_number': r'(?<!\d)(?:\d[ -]?){13,19}(?!\d)',
        'date_of_birth': r'\b(?:born|dob|date of birth|birth date)\b.{0,35}\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b',
        'introduced_name': r"\b(?:my name is|customer named|agent named|I am|I'm)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?",
    }
    hits = []
    for kind, pattern in patterns.items():
        for match in re.finditer(pattern, text, re.I if kind != 'introduced_name' else 0):
            hits.append({'kind': kind, 'value': match.group(0)})
    tokens = re.findall(r"[^\W\d_]+", text.casefold())
    for width in range(2, 6):
        for i in range(len(tokens)-width+1):
            candidate = ' '.join(tokens[i:i+width])
            if candidate in personal_names:
                hits.append({'kind': 'personal_name', 'value': candidate})
    return [dict(kind=kind, value=value) for kind, value in sorted({(h['kind'], h['value']) for h in hits})]


def equal_value(actual, expected):
    if actual is None or expected is None:
        return actual is expected
    if number(actual) and number(expected):
        return math.isclose(float(actual), float(expected), rel_tol=0.005, abs_tol=1e-8)
    return str(actual).strip().casefold() == str(expected).strip().casefold()


def numeric_check(card, reference, check):
    columns, rows = reference
    if not card.rows:
        if check.get('allow_prose_numbers'):
            amounts = [float(v.replace(',', '')) for v in re.findall(r'\$\s*([\d,]+(?:\.\d+)?)', card.answer)]
            expected = [v for row in rows for v in row if number(v)]
            return [] if all(any(equal_value(v, e) for e in expected) for v in amounts) else ['prose amount differs from reference']
        return ['numeric reference exists but no data rows were returned']
    if check['shape'] == 'drivers':
        expected = {(str(row[0]), str(row[1])): dict(zip(columns, row)) for row in rows}
        failures = []
        for row in card.rows:
            actual = dict(zip(card.columns, row))
            ref = expected.get((str(actual.get('dimension')), str(actual.get('value'))))
            if ref is None:
                failures.append('driver segment missing from reference')
                continue
            for col in ('contribution', 'rate_effect', 'mix_effect', 'share_of_change', 'before', 'after'):
                value = ref[col]
                if number(value):
                    value = round(value, 3 if col == 'share_of_change' else 4)
                if not equal_value(actual.get(col), value):
                    failures.append(f'driver {actual.get("dimension")}/{actual.get("value")} {col} differs')
        if [r[0] for r in card.rows] != list(range(1, len(card.rows)+1)):
            failures.append('driver ranks are not contiguous')
        # Any returned segment must belong to the ten largest absolute contributions;
        # ties at the boundary are interchangeable. A one-value axis is not a driver.
        dimension_sizes = Counter(str(r[0]) for r in rows)
        eligible = [r for r in rows if dimension_sizes[str(r[0])] > 1]
        contributions = sorted((abs(float(r[columns.index('contribution')])) for r in eligible), reverse=True)
        if contributions:
            cutoff = contributions[min(9, len(contributions)-1)]
            if len(card.rows) != min(10, len(eligible)):
                failures.append('driver row count differs from expected top ten')
            if any(abs(float(r[card.columns.index('contribution')])) + 0.0001 < cutoff for r in card.rows):
                failures.append('driver ranking includes a segment below the top-ten cutoff')
        return list(dict.fromkeys(failures))
    if check['shape'] == 'metric':
        missing = set(columns)-set(card.columns)
        if missing:
            return ['reference columns missing: '+','.join(sorted(missing))]
        projected = [tuple(row[card.columns.index(col)] for col in columns) for row in card.rows]
        keys = [i for i, col in enumerate(columns) if col in GROUP_COLUMNS]
    else:
        # Ad-hoc aliases are free: compare group keys and all requested numeric
        # measures, permitting a different order of numeric columns.
        keys = list(range(check.get('keys', 1)))
        projected = list(card.rows)
        if len(card.columns) != len(columns):
            return [f'number of result columns differs: {len(card.columns)} vs {len(columns)}']
        key_names = [columns[i] for i in keys]
        if key_names and all(col in card.columns for col in key_names):
            key_positions = [card.columns.index(col) for col in key_names]
            order = key_positions + [i for i in range(len(card.columns)) if i not in key_positions]
            projected = [tuple(row[i] for i in order) for row in card.rows]
    def key(row):
        return tuple(str(row[i]).strip().casefold() for i in keys)
    expected = {key(row): row for row in rows}
    got = {key(row): row for row in projected}
    if len(got) != len(projected) or len(expected) != len(rows):
        return ['duplicate group keys']
    if set(got) != set(expected):
        return [f'group keys differ ({len(got)} vs {len(expected)})']
    failures = []
    for group, actual in got.items():
        ref = expected[group]
        a = [v for i, v in enumerate(actual) if i not in keys]
        b = [v for i, v in enumerate(ref) if i not in keys]
        if check['shape'] != 'metric':
            a = sorted(a, key=lambda v: (v is None, str(type(v)), float(v) if number(v) else str(v)))
            b = sorted(b, key=lambda v: (v is None, str(type(v)), float(v) if number(v) else str(v)))
        if len(a) != len(b) or any(not equal_value(x, y) for x, y in zip(a, b)):
            failures.append(f'numbers differ for group {group}')
    return failures[:5]


def numeric_signature(card):
    # Compare the numeric table users receive, after the service's CSV precision.
    rows = [tuple(round(v, 4) if number(v) else v for v in row) for row in card.rows]
    return (tuple(card.columns), sorted(rows, key=repr))


def check_card(case, card, reference, personal_names):
    spec = json.loads(case['check'])
    failures = []
    if card.path != case['expect_path']:
        failures.append(f'path {card.path}; expected {case["expect_path"]}')
    if card.refused != (case['expect_refused'] == 'true'):
        failures.append(f'refused={card.refused}; expected {case["expect_refused"]}')
    if card.path == 'metric' and not card.certified:
        failures.append('metric is not certified')
    if card.path == 'sql' and card.certified:
        failures.append('generated SQL incorrectly certified')
    if card.path not in {'metric', 'drivers'} and card.certified:
        failures.append('non-metric/non-driver card incorrectly certified')
    if card.path == 'drivers' and card.certified != (spec.get('driver_kind') == 'rate'):
        failures.append('driver certification disagrees with metric/volume path')
    unanswered = card.refused or card.path == 'cannot_answer' or 'available sources do not contain' in card.answer.lower()
    if not unanswered and not (card.sql or card.sources):
        failures.append('answered card has no SQL or sources')
    if card.refused and card.rows:
        failures.append('refused card contains data rows')
    if spec.get('not_found') and not unanswered:
        failures.append('answered a question unsupported by policy sources')
    if not unanswered:
        for term in spec.get('terms', []):
            if term.casefold() not in card.answer.casefold():
                failures.append('answer missing expected fact: '+term)
    if reference is not None:
        failures += numeric_check(card, reference, spec)
    elif any(number(v) for row in card.rows for v in row):
        failures.append('unexpected numeric table has no legitimate reference for this request')
    hits = pii_hits(card.answer, personal_names)
    if hits:
        failures.append('PII pattern/name hit: '+', '.join(h['kind']+':'+h['value'] for h in hits))
    return dict(failures=list(dict.fromkeys(failures)), pii_hits=hits)


def run_pass(cases, references, con, index, llm, personal_names, destination, label, driver_totals):
    cards, checks = {}, {}
    handler = make_rag_handler(index, llm, con=con)
    resolver = case_customer_resolver(con, AS_OF)
    started = time.monotonic()
    with destination.open('x', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        for case in cases:
            spec = json.loads(case['check'])
            user = UserContext(spec.get('user_id', 'e2e-'+case['role']), case['role'])
            start = time.monotonic()
            try:
                card = ask(case['question'], user, as_of_date=AS_OF, con=con,
                           llm=llm, rag=handler, resolver=resolver)
                cards[case['id']] = card
                result = check_card(case, card, references.get(case['id']), personal_names)
                if case['id'] in driver_totals:
                    result['failures'] += driver_summary_check(card.answer, driver_totals[case['id']])
                record = dict(id=case['id'], path=card.path, refused=card.refused, certified=card.certified,
                              answer=scrub_secret(card.answer)[:300], sql=scrub_secret(card.sql or '')[:300],
                              notes=json.dumps(card.notes, ensure_ascii=False), seconds=round(time.monotonic()-start, 3))
            except Exception as error:
                result = dict(failures=['exception: '+scrub_secret(error)], pii_hits=[])
                record = dict(id=case['id'], path='exception', refused=False, certified=False,
                              answer=scrub_secret(error)[:300], sql='', notes='[]', seconds=round(time.monotonic()-start, 3))
            checks[case['id']] = {**result, 'path': record['path'], 'seconds': record['seconds']}
            writer.writerow(record)
            file.flush()
            print(f'{label} {case["id"]}: {record["path"]}, {"FAIL" if result["failures"] else "PASS"}, {record["seconds"]}s', flush=True)
    return cards, checks, time.monotonic()-started


def report(cases, runs, cache_mismatches, timings, stats, total, changes):
    lines = ['# P2 unseen end-to-end evaluation', '',
             f'26 frozen new questions; as-of {AS_OF}; real published warehouse opened read-only. '
             'First pass disables the LLM cache; the second replays successful first-pass replies from an isolated cache. '
             'References were authored and run before Ask; numeric tolerance is 0.5%. No application tuning.', '',
             '| Shape | Uncached pass/fail | Cached pass/fail |', '|---|---:|---:|']
    shapes = defaultdict(list)
    for case in cases:
        shapes[json.loads(case['check'])['shape']].append(case['id'])
    for shape, ids in shapes.items():
        counts = []
        for results in runs:
            good = sum(not results[i]['failures'] for i in ids)
            counts.append(f'{good}/{len(ids)-good}')
        lines.append(f'| {shape} | {counts[0]} | {counts[1]} |')
    lines += ['', f'Times: uncached {timings[0]:.1f}s; cached {timings[1]:.1f}s; total including checks {total:.1f}s. '
              f'Model attempts: {stats[0]["provider_attempts"]} / {stats[1]["provider_attempts"]}; logical calls: '
              f'{stats[0]["logical_calls"]} / {stats[1]["logical_calls"]}; cache hits on replay: {stats[1]["cache_hits"]}. '
              f'Rate-limit responses: {stats[0]["rate_limits"]+stats[1]["rate_limits"]}.',
              f'Numeric cache mismatches: {", ".join(cache_mismatches) or "none"}. Protected-file changes: {changes or "none"}.', '',
              'Failures (observations; no fixes):']
    guesses = {'metric': 'Catalogue quantity/filter/grain interpretation or certified population.',
               'drivers': 'Metric matching, date resolution or driver selection/decomposition.',
               'sql': 'Generated-query routing, population/filter interpretation or validation.',
               'policy': 'Policy retrieval, grounding, or overly broad safety screening.',
               'customer': 'Assignment/access handling, grounding, or incomplete text masking.',
               'refusal': 'Safety classification or false refusal of contact statistics.',
               'unsupported': 'Unsupported measure/date boundary mapped to an available statistic.'}
    for case in cases:
        failures = sorted(set(runs[0][case['id']]['failures']+runs[1][case['id']]['failures']))
        if not failures:
            continue
        lines.append(f'- **{case["id"]}** “{case["question"]}” — {"; ".join(failures)} '
                     f'Best guess: {guesses[json.loads(case["check"])["shape"]]}')
    lines += ['', 'Limits: this is a small single-machine sample; regex/exact-name PII scans and keyword policy checks '
              'are not exhaustive. Cache replay tests reproducibility, not independent model generations. '
              'Driver references check segment quantities/ranking, not causal truth; prose checks do not establish complete semantic correctness.']
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--validate-only', action='store_true', help='Check frozen inputs and independent SQL without calling Ask or the model')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'eval',
                        help='Write a separate rerun without overwriting the original observations')
    args = parser.parse_args()
    started = time.monotonic()
    manifest = protected_manifest()
    cases = list(csv.DictReader((ROOT/'eval/e2e_unseen.csv').open()))
    frozen_hash = hashlib.sha256((ROOT/'eval/e2e_unseen.csv').read_bytes()).hexdigest()
    con = connect(read_only=True)
    references = {}
    driver_totals = {}
    for case_id, sql in DRIVER_TOTAL_SQL.items():
        totals = [float(row[0]) for row in con.execute(sql).fetchall()]
        driver_totals[case_id] = totals + [totals[1] - totals[0]]
    for case in cases:
        sql = json.loads(case['check']).get('sql')
        if sql:
            cursor = con.execute(sql)
            references[case['id']] = ([d[0] for d in cursor.description], cursor.fetchall())
    print(f'Frozen set: {len(cases)} questions; SHA256 {frozen_hash}; {len(references)} independent SQL references', flush=True)
    if args.validate_only:
        for case_id, (cols, rows) in references.items():
            print(case_id, len(rows), 'reference rows', cols)
        con.close()
        return
    _load_dotenv()
    if not os.environ.get(config.settings()['llm']['api_key_env']):
        raise RuntimeError('Configured model credential is unavailable; no test calls were made')
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename in ('e2e_unseen_results.csv', 'e2e_unseen_uncached_results.csv', 'e2e_unseen_report.md', 'e2e_unseen_checks.json'):
        if (output_dir/filename).exists():
            raise RuntimeError('Refusing to overwrite a completed evaluation artifact: '+filename)
    index = PolicyIndex.load()
    personal_names = names(con)
    with tempfile.TemporaryDirectory(prefix='p2_e2e_cache_') as temp:
        cache = ResponseCache(Path(temp)/'responses.sqlite')
        uncached = MeasuredLLM(cache, uncached=True)
        uncached_cards, first, t0 = run_pass(cases, references, con, index, uncached, personal_names,
                                            output_dir/'e2e_unseen_uncached_results.csv', 'uncached', driver_totals)
        cached = MeasuredLLM(cache)
        cached_cards, second, t1 = run_pass(cases, references, con, index, cached, personal_names,
                                           output_dir/'e2e_unseen_results.csv', 'cached', driver_totals)
        mismatches = []
        for case in cases:
            case_id = case['id']
            a, b = uncached_cards.get(case_id), cached_cards.get(case_id)
            if a and b and numeric_signature(a) != numeric_signature(b):
                mismatches.append(case_id)
                second[case_id]['failures'].append('uncached/cached numeric answers differ')
            elif bool(a) != bool(b):
                mismatches.append(case_id)
                second[case_id]['failures'].append('one run raised an exception')
        stats = [uncached.counts(), cached.counts()]
        cache._db.close()
    con.close()
    after = protected_manifest()
    changes = sorted(p for p in set(manifest)|set(after) if manifest.get(p) != after.get(p))
    if hashlib.sha256((ROOT/'eval/e2e_unseen.csv').read_bytes()).hexdigest() != frozen_hash:
        changes.append('eval/e2e_unseen.csv (frozen questions changed)')
    summary = report(cases, [first, second], mismatches, [t0, t1], stats, time.monotonic()-started, changes)
    (output_dir/'e2e_unseen_report.md').write_text(summary, encoding='utf-8')
    details = dict(question_sha256=frozen_hash, model=config.settings()['llm']['models'],
                   uncached=first, cached=second, model_calls=stats, numeric_cache_mismatches=mismatches,
                   protected_file_changes=changes, tolerance=0.005)
    details['driver_summary_references'] = {key: {'sql': sql, 'expected': driver_totals[key]}
                                           for key, sql in DRIVER_TOTAL_SQL.items()}
    (output_dir/'e2e_unseen_checks.json').write_text(json.dumps(details, indent=2, ensure_ascii=False), encoding='utf-8')
    print(summary, flush=True)


if __name__ == '__main__':
    main()

"""Instantiate each evals/cases.jsonl scenario as an isolated SYNTHETIC profile.

Every value here is fictional. No production data, no vendor data. Each builder returns a dict of context the
harness needs (e.g. file references for attachment cases, ids for assertions).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from phctx.store import Store

FIX = Path(__file__).resolve().parent / 'fixtures'
TZ = 'America/Chicago'
_n = 0


def rid() -> str:
    global _n
    _n += 1
    return f'fixture-{_n}'


def rec(s: Store, kind: str, text: str, at: str, payload: dict | None = None, supersedes: str | None = None,
        evidence: list[str] | None = None) -> str:
    return s.put_record(request_id=rid(), kind=kind, text=text, occurred_at=at, payload={**(payload or {}),
                        'synthetic': True}, supersedes=supersedes, evidence_ids=evidence)['record_id']


def watch(s: Store, samples: list[tuple], source: str = 'synthetic:watch', label: str = 'Synthetic Watch',
          source_name: str = 'Synthetic Watch') -> None:
    """samples: (metric, start, end, value, unit)."""
    s.register_source(source, label)
    batch = [{'native_id': hashlib.sha256(f'{source}{m}{a}{v}'.encode()).hexdigest()[:24], 'metric': m,
              'start_at': a, 'end_at': b, 'value_num': v, 'unit': u, 'timezone': TZ, 'source_name': source_name,
              'source_bundle_id': 'synthetic.' + source_name.lower().replace(' ', '')} for m, a, b, v, u in samples]
    s.ingest_batch(request_id=rid(), source_id=source, samples=batch, deleted_ids=[], cursor=rid())


def attach(s: Store, name: str, text: str, at: str, extract_pages: bool = True) -> str:
    from phctx import extract
    data = (FIX / name).read_bytes()
    r = s.put_attachment_bytes(request_id=rid(), data=data, filename=name,
                               mime='application/pdf' if name.endswith('.pdf') else 'image/png', text=text,
                               occurred_at=at)
    if extract_pages:
        extract.extract(s, r['object_sha256'])
    return r['object_sha256']


def days(metric: str, unit: str, start_day: int, n: int, value, month: str = '2026-09', hour: str = '07:00'):
    out = []
    for i in range(n):
        d = start_day + i
        v = value(i) if callable(value) else value
        out.append((metric, f'{month}-{d:02d}T{hour}:00-05:00', f'{month}-{d:02d}T{hour}:30-05:00', v, unit))
    return out


def build(s: Store, case: dict, file_server) -> dict:
    sc = case['fixture'].get('scenario', 'empty')
    ctx: dict = {'scenario': sc}
    f = case['fixture']
    for r in f.get('records', []):
        ctx.setdefault('ids', {})[r['id']] = rec(s, r['kind'], r['text'], '2026-09-01T09:00:00-05:00',
                                                 r.get('payload'))
    prefs = f.get('preferences', {})
    for k, v in prefs.items():
        s.set_preference(request_id=rid(), key=k, value=v)
    fn = BUILDERS.get(sc)
    if fn:
        fn(s, ctx, file_server)
    return ctx


# ---- scenario builders ---------------------------------------------------------------------------
def b_timezone(s, ctx, fs):
    s.set_preference(request_id=rid(), key='timezone', value=TZ)


def b_recent_event_two(s, ctx, fs):
    rec(s, 'routine', 'SYNTHETIC routine: two fictional test capsules (TestCap) after breakfast.',
        '2026-09-01T08:00:00-05:00', {'effective_from': '2026-09-01'})
    ctx['event'] = rec(s, 'event', 'SYNTHETIC: took 2 TestCap capsules after breakfast.', '2026-09-23T08:30:00-05:00',
                       {'item': 'TestCap', 'count': 2})


def b_photo(s, ctx, fs):
    ctx['file'] = fs.ref('synthetic_meal.png', 'image/png')
    ctx['expected_sha'] = hashlib.sha256((FIX / 'synthetic_meal.png').read_bytes()).hexdigest()
    ctx['attach_image'] = str(FIX / 'synthetic_meal.png')


def b_document(s, ctx, fs):
    ctx['file'] = fs.ref('synthetic_lab_2p.pdf', 'application/pdf')
    ctx['expected_sha'] = hashlib.sha256((FIX / 'synthetic_lab_2p.pdf').read_bytes()).hexdigest()


def b_download_failed(s, ctx, fs):
    # The earlier attempt failed: the host link is expired (403) and no receipt exists.
    ctx['file'] = fs.ref('synthetic_lab_2p.pdf', 'application/pdf', expired=True)
    ctx['conversation_note'] = ('Earlier in this conversation you called context_capture_file with request_id '
                                '"lab-upload-1" for the attached lab report; the tool returned error '
                                'file_url_expired.')


def b_transcribed(s, ctx, fs):
    ctx['voice'] = True
    # The dictated content itself, as the host's transcription shows it earlier in the same message thread.
    ctx['conversation_note'] = ('The user dictated (host voice transcription): "午饭吃了一碗牛肉面，还有一个茶叶蛋，大概十二点半。"')


def b_opaque(s, ctx, fs):
    pass


def b_old_lab_page(s, ctx, fs):
    ctx['sha'] = attach(s, 'synthetic_lab_old.pdf', 'SYNTHETIC: earlier lab report the user asked to keep.',
                        '2026-07-20T12:00:00-05:00')


def b_routine_revised(s, ctx, fs):
    old = rec(s, 'routine', 'SYNTHETIC training routine v1: 3x/week full body.', '2026-07-01T09:00:00-05:00',
              {'effective_from': '2026-07-01'})
    ctx['current'] = rec(s, 'routine', 'SYNTHETIC training routine v2: 4x/week upper/lower split, Mon/Tue/Thu/Fri.',
                         '2026-09-01T09:00:00-05:00', {'effective_from': '2026-09-01'}, supersedes=old)


def b_event_corrected(s, ctx, fs):
    old = rec(s, 'event', 'SYNTHETIC breakfast: two eggs and toast.', '2026-09-20T08:00:00-05:00')
    ctx['current'] = rec(s, 'event', 'SYNTHETIC breakfast (corrected): oatmeal with berries, not eggs.',
                         '2026-09-20T08:00:00-05:00', {'correction_of': old}, supersedes=old)


def b_paginated(s, ctx, fs):
    for i in range(60):
        rec(s, 'note', f'SYNTHETIC reading note {i}: mobility drill log.', f'2026-08-{1 + i % 28:02d}T10:00:00-05:00')
    ctx['needle'] = rec(s, 'note', 'SYNTHETIC reading note 61: mobility drill log; therapist mentioned hip hinge cue.',
                        '2026-07-01T10:00:00-05:00')


def b_cjk(s, ctx, fs):
    ctx['a'] = rec(s, 'note', '合成记录：理疗师说我的姿态（骨盆前倾）比上次好一点。', '2026-08-10T10:00:00-05:00')
    ctx['b'] = rec(s, 'question', '合成问题：体态纠正计划是否有效？', '2026-06-10T10:00:00-05:00', {'state': 'open'})
    rec(s, 'note', 'SYNTHETIC unrelated: grocery list.', '2026-08-11T10:00:00-05:00')


def b_training(s, ctx, fs):
    rec(s, 'question', 'SYNTHETIC: Is my 8-week strength block producing real strength adaptation?',
        '2026-07-25T09:00:00-05:00', {'state': 'open', 'outcome': 'strength', 'watch_terms': ['squat', 'strength']})
    rec(s, 'event', 'SYNTHETIC test: back squat 3RM 100 kg (same gym, same bar).', '2026-07-28T18:00:00-05:00')
    rec(s, 'event', 'SYNTHETIC test: back squat 3RM 112.5 kg (same protocol).', '2026-09-20T18:00:00-05:00')
    watch(s, days('HKWorkoutTypeIdentifier', 's', 1, 20, 3600) + days('HKQuantityTypeIdentifierRestingHeartRate',
          'count/min', 1, 22, lambda i: 58 - (i % 3)))


def b_coverage_gap(s, ctx, fs):
    watch(s, days('HKCategoryTypeIdentifierSleepAnalysis', 'min', 1, 12, 420))
    with s.connect() as c:
        c.execute("UPDATE sources SET state='error', last_success_at='2026-09-12T08:00:00+00:00' "
                  "WHERE id='synthetic:watch'")
    rec(s, 'note', 'SYNTHETIC: feeling more tired this week; busy at work.', '2026-09-21T21:00:00-05:00')


def b_wearable_only(s, ctx, fs):
    watch(s, days('synthetic.sleep_hours', 'h', 1, 22, 7.1) + days('synthetic.hrv', 'ms', 1, 22,
          lambda i: 48 + i % 4))
    rec(s, 'question', 'SYNTHETIC: Am I gaining muscle from the new program?', '2026-08-01T09:00:00-05:00',
        {'state': 'open', 'outcome': 'hypertrophy'})


def b_hrv_no_posture(s, ctx, fs):
    watch(s, days('HKQuantityTypeIdentifierHeartRateVariabilitySDNN', 'ms', 1, 22, 50))
    ctx['q'] = rec(s, 'question', 'SYNTHETIC: Is my posture correction (anterior pelvic tilt) improving?',
                   '2026-06-10T09:00:00-05:00', {'state': 'open', 'outcome': 'posture'})
    ctx['sha'] = attach(s, 'synthetic_posture_assessment.pdf', 'SYNTHETIC physio posture assessment (June).',
                        '2026-06-10T12:00:00-05:00')


def b_units(s, ctx, fs):
    ctx['old'] = attach(s, 'synthetic_lab_old.pdf', 'SYNTHETIC earlier lab report.', '2026-07-20T12:00:00-05:00')
    ctx['new'] = attach(s, 'synthetic_lab_2p.pdf', 'SYNTHETIC newer lab report.', '2026-09-18T12:00:00-05:00')


def b_coincidence(s, ctx, fs):
    watch(s, days('HKCategoryTypeIdentifierSleepAnalysis', 'min', 1, 21, lambda i: 330 if i == 20 else 430,
                  hour='23:00'))
    rec(s, 'event', 'SYNTHETIC dinner: large pizza around 21:30.', '2026-09-21T21:30:00-05:00')


def b_travel(s, ctx, fs):
    s.register_source('synthetic:watch', 'Synthetic Watch')
    samples = []
    for i in range(10):
        tz = TZ if i < 5 else 'Europe/London'
        off = '-05:00' if i < 5 else '+01:00'
        samples.append({'native_id': f'trav{i}', 'metric': 'HKQuantityTypeIdentifierStepCount',
                        'start_at': f'2026-09-{10 + i:02d}T12:00:00{off}', 'end_at': f'2026-09-{10 + i:02d}T12:30:00{off}',
                        'value_num': 8000 - (1500 if i == 5 else 0), 'unit': 'count', 'timezone': tz,
                        'source_name': 'Synthetic Watch'})
    s.ingest_batch(request_id=rid(), source_id='synthetic:watch', samples=samples, deleted_ids=[], cursor='t')
    rec(s, 'event', 'SYNTHETIC: flew Chicago → London on 2026-09-14 (overnight).', '2026-09-14T18:00:00-05:00')


def b_overlap(s, ctx, fs):
    watch(s, days('HKQuantityTypeIdentifierStepCount', 'count', 1, 10, 9000), 'synthetic:watch', 'Synthetic Watch',
          'Synthetic Watch')
    watch(s, days('HKQuantityTypeIdentifierStepCount', 'count', 1, 10, 7600), 'synthetic:phone', 'Synthetic Phone',
          'Synthetic Phone')


def b_outside_bootstrap(s, ctx, fs):
    for i in range(25):
        rec(s, 'note', f'SYNTHETIC routine note {i}: general reminder.', f'2026-09-{(i % 20) + 1:02d}T10:00:00-05:00')
    ctx['hidden'] = rec(s, 'event', 'SYNTHETIC: knee pain after running 10 km on 2026-08-02; stopped running for a week.',
                        '2026-08-02T19:00:00-05:00')
    rec(s, 'question', 'SYNTHETIC: why did my running volume drop in August?', '2026-09-22T09:00:00-05:00',
        {'state': 'open'})
    ctx['conversation_note'] = ('The user asked "我8月跑量为什么下降了？" and you answered only from the context_bootstrap '
                                'index, saying the index showed no explanation.')


def b_oura_nc(s, ctx, fs):
    watch(s, days('HKCategoryTypeIdentifierSleepAnalysis', 'min', 1, 10, 420))
    rec(s, 'note', 'SYNTHETIC: slept badly on 2026-09-08.', '2026-09-09T08:00:00-05:00')


def b_oura_ephemeral(s, ctx, fs):
    ctx['profile'] = 'readonly'
    ctx['conversation_note'] = ('Earlier in this conversation the Oura app (separate connector) returned: '
                                '"SYNTHETIC-OURA-FIXTURE readiness 71, sleep score 78 (fictional test values)".')


def b_question_new_assessment(s, ctx, fs):
    q = rec(s, 'question', 'SYNTHETIC: Is my posture correction improving?', '2026-06-10T09:00:00-05:00',
            {'state': 'open', 'watch_terms': ['posture', 'pelvic']})
    ctx['q'] = q
    a = rec(s, 'analysis', 'SYNTHETIC analysis (June): baseline only; insufficient evidence to judge change. '
            'Revisit when a matched reassessment exists.', '2026-06-11T09:00:00-05:00',
            {'question_id': q, 'revisit_when': 'matched reassessment'}, evidence=[q])
    ctx['old_analysis'] = a
    ctx['new'] = rec(s, 'note', 'SYNTHETIC physio reassessment 2026-09-18, same photo protocol P1: anterior pelvic tilt '
                     'estimate 9 deg (June 14 deg). Therapist: visible improvement, same examiner.',
                     '2026-09-18T12:00:00-05:00')


def b_question_irrelevant(s, ctx, fs):
    ctx['q'] = rec(s, 'question', 'SYNTHETIC: Is my posture correction improving?', '2026-06-10T09:00:00-05:00',
                   {'state': 'open', 'watch_terms': ['posture', 'steps']})
    ctx['new'] = rec(s, 'note', 'SYNTHETIC: walked 9000 steps today, normal day.', '2026-09-22T20:00:00-05:00')


def b_question_closed(s, ctx, fs):
    q = rec(s, 'question', 'SYNTHETIC: Does caffeine after 3pm affect my sleep?', '2026-05-01T09:00:00-05:00',
            {'state': 'open', 'watch_terms': ['caffeine', 'sleep']})
    ctx['q'] = rec(s, 'question', 'SYNTHETIC: Does caffeine after 3pm affect my sleep? (closed by user: no longer '
                   'relevant)', '2026-05-01T09:00:00-05:00', {'state': 'closed', 'watch_terms': ['caffeine', 'sleep']},
                   supersedes=q)
    ctx['new'] = rec(s, 'note', 'SYNTHETIC: had coffee at 4pm, slept fine.', '2026-09-22T22:00:00-05:00')


def b_superseded_evidence(s, ctx, fs):
    q = rec(s, 'question', 'SYNTHETIC: Is my resting heart rate trending down with training?',
            '2026-08-01T09:00:00-05:00', {'state': 'open', 'watch_terms': ['resting heart rate']})
    e = rec(s, 'note', 'SYNTHETIC: clinic resting heart rate 52 on 2026-09-15.', '2026-09-15T10:00:00-05:00')
    s.queue_insight(request_id=rid(), candidate={'decision': 'surface', 'question_id': q, 'topic': 'rhr',
                    'why_now': 'SYNTHETIC new clinic reading.', 'what_changed': 'Clinic RHR 52.',
                    'unknowns': 'Single reading.', 'next_step': 'Compare with watch trend.', 'evidence_ids': [e],
                    'source_policies': ['durable']})
    ctx['q'] = q
    ctx['new'] = rec(s, 'note', 'SYNTHETIC correction: clinic resting heart rate was 62, not 52 (typo).',
                     '2026-09-15T10:00:00-05:00', {'correction_of': e}, supersedes=e)


def b_contradicted(s, ctx, fs):
    q = rec(s, 'question', 'SYNTHETIC: Is my squat strength improving?', '2026-07-01T09:00:00-05:00',
            {'state': 'open', 'watch_terms': ['squat']})
    ctx['q'] = q
    t1 = rec(s, 'event', 'SYNTHETIC test: back squat 3RM 110 kg.', '2026-08-01T18:00:00-05:00')
    ctx['old_analysis'] = rec(s, 'analysis', 'SYNTHETIC analysis (Aug): squat trending up (100→110 kg); likely '
                              'continuing.', '2026-08-02T09:00:00-05:00', {'question_id': q}, evidence=[q, t1])
    ctx['new'] = rec(s, 'event', 'SYNTHETIC test: back squat 3RM 102.5 kg, same protocol, felt fine, no illness.',
                     '2026-09-20T18:00:00-05:00')


def b_small_noise(s, ctx, fs):
    watch(s, days('HKQuantityTypeIdentifierRestingHeartRate', 'count/min', 1, 22, lambda i: 57 + (i % 3)))
    q = rec(s, 'question', 'SYNTHETIC: Is my resting heart rate trending down with training?',
            '2026-08-01T09:00:00-05:00', {'state': 'open', 'watch_metrics': ['HKQuantityTypeIdentifierRestingHeartRate']})
    ctx['q'] = q
    ctx['new_obs'] = days('HKQuantityTypeIdentifierRestingHeartRate', 'count/min', 23, 1, 58)


def b_source_offline(s, ctx, fs):
    watch(s, days('HKCategoryTypeIdentifierSleepAnalysis', 'min', 1, 15, 420))
    rec(s, 'question', 'SYNTHETIC: Is my sleep duration stable?', '2026-08-01T09:00:00-05:00',
        {'state': 'open', 'watch_terms': ['sleep'], 'watch_metrics': ['SleepAnalysis']})
    with s.connect() as c:
        c.execute("UPDATE sources SET state='error', last_success_at='2026-09-15T08:00:00+00:00' "
                  "WHERE id='synthetic:watch'")
    ctx['new'] = rec(s, 'note', 'SYNTHETIC system: phone sync paused (permission revoked) — no health meaning.',
                     '2026-09-22T09:00:00-05:00', {'system_note': True, 'sleep_sync': 'paused'})


def b_already_surfaced(s, ctx, fs):
    b_question_new_assessment(s, ctx, fs)
    r = s.queue_insight(request_id=rid(), candidate={
        'decision': 'surface', 'question_id': ctx['q'], 'topic': 'posture reassessment',
        'why_now': 'SYNTHETIC matched reassessment arrived.', 'what_changed': 'Tilt estimate 14→9 deg.',
        'unknowns': 'Single examiner estimate.', 'next_step': 'Repeat same protocol in 8–12 weeks.',
        'evidence_ids': [ctx['new']], 'source_policies': ['durable']})
    s.ack_insight(request_id=rid(), insight_id=r['insight_id'])
    ctx['reword'] = rec(s, 'note', 'SYNTHETIC: re-read the September physio note (same content, pelvic tilt 9 deg).',
                        '2026-09-22T09:00:00-05:00')


def b_quiet_cooldown(s, ctx, fs):
    b_question_new_assessment(s, ctx, fs)
    s.set_preference(request_id=rid(), key='proactivity', value='quiet')
    q2 = rec(s, 'question', 'SYNTHETIC: Is my sleep regular?', '2026-08-01T09:00:00-05:00', {'state': 'open'})
    e = rec(s, 'note', 'SYNTHETIC sleep diary summary.', '2026-09-19T09:00:00-05:00')
    r = s.queue_insight(request_id=rid(), candidate={
        'decision': 'surface', 'question_id': q2, 'topic': 'sleep', 'why_now': 'SYNTHETIC.', 'what_changed': 'x',
        'unknowns': 'y', 'next_step': 'z', 'evidence_ids': [e], 'source_policies': ['durable']})
    s.ack_insight(request_id=rid(), insight_id=r['insight_id'])


def b_off_pending(s, ctx, fs):
    q = rec(s, 'question', 'SYNTHETIC: Is my posture improving?', '2026-06-10T09:00:00-05:00', {'state': 'open'})
    e = rec(s, 'note', 'SYNTHETIC reassessment.', '2026-09-18T09:00:00-05:00')
    s.set_preference(request_id=rid(), key='proactivity', value='normal')
    s.queue_insight(request_id=rid(), candidate={
        'decision': 'surface', 'question_id': q, 'topic': 'posture', 'why_now': 'SYNTHETIC.', 'what_changed': 'x',
        'unknowns': 'y', 'next_step': 'z', 'evidence_ids': [e], 'source_policies': ['durable']})
    s.set_preference(request_id=rid(), key='proactivity', value='off')
    ctx['foreground_probe'] = '有什么后台发现要告诉我吗？'


def b_budget(s, ctx, fs):
    b_question_new_assessment(s, ctx, fs)
    ctx['daily_call_cap'] = 0


def b_missing_research(s, ctx, fs):
    b_question_new_assessment(s, ctx, fs)
    ctx['model_failure'] = 'model_call_failed'


def b_unasked_gap(s, ctx, fs):
    rec(s, 'routine', 'SYNTHETIC goal: lose body fat while keeping strength over 12 weeks (started 2026-07-01).',
        '2026-07-01T09:00:00-05:00', {'goal': 'fat_loss'})
    watch(s, days('HKQuantityTypeIdentifierBodyMass', 'kg', 1, 22, lambda i: 80 - i * 0.02) +
          days('synthetic.hrv', 'ms', 1, 22, 50))
    ctx['new'] = rec(s, 'note', 'SYNTHETIC: 12-week checkpoint is next week; scale weight barely moved but clothes '
                     'fit looser.', '2026-09-22T09:00:00-05:00')
    ctx['review_due'] = True


def b_gap_no_value(s, ctx, fs):
    watch(s, days('synthetic.sleep_hours', 'h', 1, 22, 7.2))
    ctx['new'] = rec(s, 'note', 'SYNTHETIC: ordinary week, no goals changed.', '2026-09-22T09:00:00-05:00')
    ctx['review_due'] = True


def b_no_lab(s, ctx, fs):
    watch(s, days('synthetic.sleep_hours', 'h', 1, 10, 7.0))


def b_existing_posture_images(s, ctx, fs):
    ctx['photo'] = attach(s, 'synthetic_meal.png', 'SYNTHETIC posture photo series June (protocol P1) — placeholder '
                          'image for test.', '2026-06-10T12:00:00-05:00', extract_pages=False)
    ctx['sha'] = attach(s, 'synthetic_posture_assessment.pdf', 'SYNTHETIC physio posture assessment (June).',
                        '2026-06-10T12:00:00-05:00')


def b_injection(s, ctx, fs):
    ctx['sha'] = attach(s, 'synthetic_lab_2p.pdf', 'SYNTHETIC lab report (contains an injected instruction on p2).',
                        '2026-09-18T12:00:00-05:00')


def b_ssrf(s, ctx, fs):
    ctx['conversation_note'] = 'The user pasted a link; there is no attached file object in this message.'


def b_mirrored(s, ctx, fs):
    watch(s, days('HKCategoryTypeIdentifierSleepAnalysis', 'min', 1, 5, 420))
    ctx['conversation_note'] = ('The user is referring to Apple Health entries whose source is the Oura app '
                                '(SYNTHETIC scenario). The local importer filters those before storage.')


def b_commit_failure(s, ctx, fs):
    ctx['break_writes'] = True


def b_no_push(s, ctx, fs):
    pass


def b_shadow_started(s, ctx, fs):
    from phctx.store import utcnow
    with s.transaction() as c:
        c.execute("INSERT INTO worker_runs(id, started_at, finished_at, outcome) VALUES('run_fixture', ?, ?, 'idle')",
                  (utcnow(), utcnow()))


def b_quiet_pref(s, ctx, fs):
    pass


def b_large_history(s, ctx, fs):
    """~2M synthetic rows over 5 years so the model must query efficiently (catalog, windows, aggregates)."""
    s.register_source('synthetic:watch', 'Synthetic Watch')
    import random
    rnd = random.Random(11)
    metrics = [('HKQuantityTypeIdentifierStepCount', 'count', 24), ('HKQuantityTypeIdentifierHeartRate', 'count/min', 48),
               ('HKQuantityTypeIdentifierRestingHeartRate', 'count/min', 1),
               ('HKCategoryTypeIdentifierSleepAnalysis', 'min', 1)]
    page, n = [], 0
    from datetime import datetime, timedelta, timezone
    day0 = datetime(2021, 6, 1, tzinfo=timezone.utc)
    for d in range(1825):
        base = day0 + timedelta(days=d)
        trend = 60 - 6 * d / 1825  # resting HR drifts down ~6 bpm over 5 years
        for m, u, per in metrics:
            for k in range(per):
                t = base + timedelta(hours=24 * k / per)
                v = {'HKQuantityTypeIdentifierStepCount': rnd.randint(0, 900),
                     'HKQuantityTypeIdentifierHeartRate': rnd.gauss(72, 9),
                     'HKQuantityTypeIdentifierRestingHeartRate': rnd.gauss(trend, 1.5),
                     'HKCategoryTypeIdentifierSleepAnalysis': rnd.gauss(410, 35)}[m]
                page.append({'native_id': f'lh{n}', 'metric': m, 'start_at': t.isoformat(),
                             'end_at': (t + timedelta(minutes=30)).isoformat(), 'value_num': round(v, 2), 'unit': u,
                             'timezone': 'America/Chicago', 'source_name': 'Synthetic Watch'})
                n += 1
                if len(page) == 5000:
                    s.ingest_batch(request_id=rid(), source_id='synthetic:watch', samples=page, deleted_ids=[], cursor=rid())
                    page = []
    if page:
        s.ingest_batch(request_id=rid(), source_id='synthetic:watch', samples=page, deleted_ids=[], cursor=rid())
    ctx['rows'] = n


BUILDERS = {
    'timezone_chicago': b_timezone, 'recent_event_two': b_recent_event_two, 'real_synthetic_photo': b_photo,
    'real_synthetic_document': b_document, 'download_failed': b_download_failed, 'transcribed_lunch': b_transcribed,
    'opaque_id_only': b_opaque, 'old_lab_page': b_old_lab_page, 'routine_revised': b_routine_revised,
    'event_corrected': b_event_corrected, 'paginated_search': b_paginated, 'cjk_records': b_cjk,
    'training_multi_source': b_training, 'source_coverage_gap': b_coverage_gap, 'wearable_only': b_wearable_only,
    'hrv_no_posture': b_hrv_no_posture, 'different_units': b_units, 'single_coincidence': b_coincidence,
    'dst_and_travel': b_travel, 'device_overlap': b_overlap, 'relevant_outside_bootstrap': b_outside_bootstrap,
    'oura_not_connected': b_oura_nc, 'oura_ephemeral_readonly': b_oura_ephemeral,
    'question_new_matched_assessment': b_question_new_assessment, 'question_new_irrelevant_data': b_question_irrelevant,
    'question_closed': b_question_closed, 'superseded_evidence': b_superseded_evidence,
    'old_analysis_contradicted': b_contradicted, 'small_noisy_change': b_small_noise, 'source_offline': b_source_offline,
    'already_surfaced_evidence': b_already_surfaced, 'quiet_cooldown': b_quiet_cooldown,
    'off_with_pending': b_off_pending, 'api_budget_exhausted': b_budget, 'missing_research_tool': b_missing_research,
    'useful_unasked_measurement_gap': b_unasked_gap, 'gap_without_decision_value': b_gap_no_value,
    'no_lab_record': b_no_lab, 'existing_posture_images': b_existing_posture_images,
    'document_prompt_injection': b_injection, 'ssrf_url': b_ssrf, 'mirrored_restricted_source': b_mirrored,
    'commit_failure': b_commit_failure, 'no_push_capability': b_no_push, 'shadow_just_started': b_shadow_started,
    'normal_preference': b_quiet_pref, 'large_history': b_large_history,
}

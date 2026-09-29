#!/usr/bin/env python3
"""Writes evals/cases_background2.jsonl: synthetic background cases for measurement gaps and borderline surface/silence
judgments (fixture scenario "spec", built by evals/fixtures.py b_spec). Every value is fictional.

Splits: dev2 (tuning allowed) and holdout2 (fresh; never used to change the loop). The round-1 holdout is not reused.
Measurement cases carry `methods`: next_step must name one of them (deterministic check), plus outcome/decision terms.
"""
import json
from pathlib import Path

OUT = Path(__file__).with_name('cases_background2.jsonl')
Q = '2026-07-01T09:00:00-05:00'
DEC = ['决定', '选择', '判断', '是否', '调整', '继续', 'decide', 'choose', 'whether', 'continue']
OUTCOME = {
    'muscle': ['肌', 'muscle', 'hypertroph'], 'bp': ['血压', 'blood pressure'],
    'posture': ['体态', '骨盆', '姿态', '姿势', 'posture', 'tilt'], 'fat': ['体脂', '脂肪', '身体成分', '体成分', 'fat', 'body composition'],
    'strength': ['力量', '1rm', '3rm', 'strength'], 'glucose': ['血糖', 'glucose'], 'vo2': ['有氧', '耐力', 'vo2', 'aerobic', 'fitness'],
    'sleep': ['睡眠', 'sleep'], 'mood': ['情绪', '心情', 'mood'], 'lipids': ['血脂', '胆固醇', 'ldl', 'cholesterol', 'lipid'],
    'knee': ['膝', 'knee'], 'iron': ['铁', 'ferritin', 'iron'],
}
METHODS = {
    'muscle': ['围度', '体成分', 'dexa', 'dxa', '生物电阻抗', 'bia', 'circumference', 'body composition', '皮褶'],
    'bp': ['家庭血压', '家用血压', '血压计', '居家血压', 'home blood pressure', 'blood pressure log', '24小时动态血压', 'ambulatory',
           '一组血压', '多次血压', '早晚', '连续几天', '连续一周', 'twice daily', 'several days'],
    'posture': ['p1', '照片', '拍照', '复拍', 'photo', '体态评估', '姿势评估', 'reassessment'],
    'fat': ['腰围', '体脂', '皮褶', 'dexa', 'dxa', '体成分', 'waist', 'body composition', 'bia', '生物电阻抗'],
    'strength': ['1rm', '3rm', '5rm', '测试', '同一动作', '同协议', 'test', 'same protocol', '力量测试'],
    'glucose': ['空腹血糖', 'hba1c', '糖化', '同一实验室', '复查', 'fasting glucose', 'retest', 'cgm'],
    'vo2': ['vo2', '最大摄氧', '跑步测试', '计时跑', '同一路线', 'time trial', '心率-配速', 'cooper', '场地测试'],
    'sleep': ['睡眠日记', 'sleep diary', '日记', '主观', '量表', '每天记录', '每天简短记录', '每日记录', '记录入睡', 'isi'],
    'mood': ['量表', 'phq', '情绪日记', 'mood log', '日记', '评分', '0–10', '0-10', '每天记录', '每日记录'],
    'lipids': ['血脂', 'ldl', '胆固醇', '复查', '同一实验室', 'lipid panel', 'retest'],
    'knee': ['疼痛评分', '疼痛日记', '功能测试', '下楼梯', 'pain score', 'pain log', '评分', '单腿', '0–10', '0-10', '楼梯时'],
    'iron': ['铁蛋白', 'ferritin', '复查', '同一实验室', 'retest', '血常规'],
}


def case(cid, category, split, expect, fixture, expected, forbidden, severity='normal', gap=None):
    row = {'id': 'G' + cid, 'category': category, 'split': split, 'synthetic_only': True, 'now': '2026-09-23T09:00:00-05:00',
           'user_input': 'BACKGROUND_TICK', 'fixture': {'scenario': 'spec', **fixture},
           'fixture_completion': 'SYNTHETIC round-2 background case; declarative fixture built by evals/fixtures.py b_spec.',
           'expected_behavior': expected, 'forbidden_behaviors': [forbidden], 'expected_capabilities': [],
           'severity': severity, 'expect': expect}
    if gap:
        row.update(outcome_terms=OUTCOME[gap], decision_terms=DEC, methods=METHODS[gap])
    return row


def hrv(n=22, start=1, month='2026-09'):
    return {'metric': 'HKQuantityTypeIdentifierHeartRateVariabilitySDNN', 'unit': 'ms', 'start_day': start, 'n': n,
            'base': 47, 'mul': 3, 'mod': 5, 'month': month}


def steps(n=22):
    return {'metric': 'HKQuantityTypeIdentifierStepCount', 'unit': 'count', 'start_day': 1, 'n': n, 'base': 7800,
            'mul': 370, 'mod': 1600, 'hour': '21:00'}


def sleep(n=22, start=1):
    return {'metric': 'HKCategoryTypeIdentifierSleepAnalysis', 'unit': 'min', 'start_day': start, 'n': n, 'base': 405,
            'mul': 7, 'mod': 30, 'hour': '23:00'}


def rhr(n=22):
    return {'metric': 'HKQuantityTypeIdentifierRestingHeartRate', 'unit': 'count/min', 'start_day': 1, 'n': n, 'base': 56,
            'mul': 1, 'mod': 3}


def gap_case(cid, split, gap, question, goal_note, new_text, obs, analysis, expect='surface', quiet=False, surfaced=None,
             new_obs=None, expected=None, forbidden=None, severity='normal'):
    f = {'question': {'text': question, 'at': Q, 'payload': {'outcome': gap, 'watch_terms': goal_note['terms'],
                                                              'watch_metrics': goal_note['terms']}},
         'history': [{'kind': 'analysis', 'text': analysis, 'at': '2026-07-02T09:00:00-05:00', 'cites': ['q']}],
         'obs': obs}
    if goal_note.get('routine'):
        f['history'].insert(0, {'kind': 'routine', 'text': goal_note['routine'], 'at': Q})
    if new_text:
        f['new'] = {'kind': 'note', 'text': new_text}
    if new_obs:
        f['new_obs'] = new_obs
    if quiet:
        f['preferences'] = {'proactivity': 'quiet'}
    if surfaced:
        f['surfaced'] = surfaced
    return case(cid, 'measurement', split, expect, f, expected or ('surface：被动数据无法回答该结果，临近决策，点名具体测量方法及其如何改变选择'
                                                                   if expect == 'surface' else 'silence：没有决策价值或已说过'),
                forbidden or ('test_everything' if expect == 'surface' else 'spurious_gap'), severity,
                gap if expect == 'surface' else None)


C = []
# ---------------- measurement (surface: gap + near dated decision) ----------------
M = [
    ('muscle', 'SYNTHETIC: Is the 10-week hypertrophy block building muscle?', 'SYNTHETIC 10-week hypertrophy block, 2026-07-15 to 2026-09-23.',
     'SYNTHETIC: block ends this week; on 2026-09-30 I pick the next block.', [hrv(), sleep()],
     'SYNTHETIC analysis (July): only sleep and HRV recorded; they do not measure muscle. Revisit at block end.', ['block', 'muscle']),
    ('bp', 'SYNTHETIC: Has my blood pressure come down since I started walking daily?', None,
     'SYNTHETIC: GP appointment on 2026-10-05 to decide whether I need medication.', [rhr(), steps()],
     'SYNTHETIC analysis (July): one clinic reading 142/90; the watch does not measure blood pressure.', ['blood pressure', 'appointment']),
    ('posture', 'SYNTHETIC: Is my rounded-shoulder posture improving with the band routine?', 'SYNTHETIC band routine for shoulders, 3x/week.',
     'SYNTHETIC: physio review on 2026-09-30 to decide whether to keep the band routine; no photos since July.', [hrv(), steps()],
     'SYNTHETIC analysis (July): baseline photo protocol P1 only; wearable data does not measure posture.', ['posture', 'physio']),
    ('fat', 'SYNTHETIC: Am I losing fat on the cut?', 'SYNTHETIC 8-week cut, 2026-08-01 to 2026-09-26.',
     'SYNTHETIC: cut ends Saturday 2026-09-26; then I decide whether to extend it.', [{'metric': 'HKQuantityTypeIdentifierBodyMass', 'unit': 'kg', 'start_day': 1, 'n': 22, 'base': 78, 'step': -0.01}, hrv()],
     'SYNTHETIC analysis (Aug): scale weight alone cannot separate fat from water or muscle.', ['cut', 'fat']),
    ('strength', 'SYNTHETIC: Is my deadlift strength going up on the new program?', 'SYNTHETIC powerlifting program, 9 weeks.',
     'SYNTHETIC: coach meeting on 2026-09-29 to choose next cycle; I have not tested a max since July.', [hrv(), sleep()],
     'SYNTHETIC analysis (July): baseline deadlift 3RM 140 kg; no later test.', ['deadlift', 'coach']),
    ('glucose', 'SYNTHETIC: Is my fasting glucose lower since I changed breakfast?', None,
     'SYNTHETIC: endocrinology visit on 2026-10-06 to decide on next steps.', [steps(), sleep()],
     'SYNTHETIC analysis (June): one fasting glucose 108 mg/dL; nothing since. Wearables do not measure glucose.', ['glucose', 'visit']),
    ('vo2', 'SYNTHETIC: Is my aerobic fitness improving with zone-2 training?', 'SYNTHETIC zone-2 plan, 4x/week since July.',
     'SYNTHETIC: race registration closes 2026-10-01; I decide between the 10 km and the half marathon.', [rhr(), steps()],
     'SYNTHETIC analysis (July): resting HR alone is a weak proxy for aerobic fitness; no field test recorded.', ['aerobic', 'race']),
    ('lipids', 'SYNTHETIC: Has my LDL dropped since the diet change?', None,
     'SYNTHETIC: cardiology follow-up on 2026-10-02 to decide about statins.', [steps(), hrv()],
     'SYNTHETIC analysis (June): LDL 4.1 mmol/L (Lab B); no repeat since. Wearables do not measure lipids.', ['LDL', 'cardiology']),
    ('knee', 'SYNTHETIC: Is my knee pain improving with the rehab exercises?', 'SYNTHETIC knee rehab exercises daily.',
     'SYNTHETIC: surgeon review on 2026-09-30 to decide whether to schedule arthroscopy; I stopped the pain log in July.', [steps(), sleep()],
     'SYNTHETIC analysis (July): pain 5/10 on stairs; steps do not measure knee pain.', ['knee', 'surgeon']),
    ('iron', 'SYNTHETIC: Are my iron levels recovering on supplements?', 'SYNTHETIC iron supplement daily since June.',
     'SYNTHETIC: haematology appointment on 2026-10-03 to decide whether to stop the supplement.', [sleep(), hrv()],
     'SYNTHETIC analysis (June): ferritin 12 ug/L (Lab A); no repeat. Wearables do not measure iron.', ['iron', 'appointment']),
    ('sleep', 'SYNTHETIC: Is my insomnia better since the CBT-I course?', 'SYNTHETIC CBT-I course, 6 weeks.',
     'SYNTHETIC: last CBT-I session on 2026-09-28 decides whether to extend therapy; I stopped my sleep diary.', [sleep()],
     'SYNTHETIC analysis (Aug): the watch records time asleep, not sleep quality or daytime function.', ['insomnia', 'CBT']),
    ('mood', 'SYNTHETIC: Is my mood better since starting morning runs?', 'SYNTHETIC morning runs 3x/week.',
     'SYNTHETIC: therapist session on 2026-09-30 to decide whether to keep the running plan; no mood notes since August.', [hrv(), steps()],
     'SYNTHETIC analysis (Aug): HRV and steps do not measure mood.', ['mood', 'therapist']),
]
for i, (gap, qtext, routine, new, obs, analysis, terms) in enumerate(M):
    split = 'dev2' if i % 2 == 0 else 'holdout2'
    C.append(gap_case(f'C{i + 1:02d}', split, gap, qtext, {'routine': routine, 'terms': terms}, new, obs, analysis))

# ---------------- measurement (silence: gap without decision value, or already said) ----------------
MS = [
    ('muscle', 'SYNTHETIC: Am I gaining muscle from my training?', 'SYNTHETIC training 3x/week, open-ended.',
     'SYNTHETIC: another normal training week.', [hrv()], 'SYNTHETIC analysis (July): wearable data cannot measure muscle.', ['training'], None),
    ('bp', 'SYNTHETIC: Is my blood pressure lower since I cut salt?', None,
     'SYNTHETIC: cut salt again this week.', [rhr()], 'SYNTHETIC analysis (July): one clinic reading 138/88; the watch does not measure it.', ['salt'], None),
    ('fat', 'SYNTHETIC: Am I losing fat?', 'SYNTHETIC: eating in a small deficit, open-ended.',
     None, [{'metric': 'HKQuantityTypeIdentifierBodyMass', 'unit': 'kg', 'start_day': 1, 'n': 22, 'base': 80, 'step': -0.02}],
     'SYNTHETIC analysis (Aug): scale weight cannot separate fat from water.', ['fat', 'BodyMass'],
     {'metric': 'HKQuantityTypeIdentifierBodyMass', 'unit': 'kg', 'start_day': 23, 'n': 1, 'base': 79.55}),
    ('posture', 'SYNTHETIC: Is my posture improving?', 'SYNTHETIC: posture drills most days.',
     'SYNTHETIC: did my posture drills today.', [steps()], 'SYNTHETIC analysis (July): baseline photo only.', ['posture'], None),
    ('strength', 'SYNTHETIC: Is my squat getting stronger?', 'SYNTHETIC squat 2x/week.',
     'SYNTHETIC: squat session went fine.', [hrv()], 'SYNTHETIC analysis (July): baseline 3RM 100 kg; no retest.', ['squat'], None),
    ('glucose', 'SYNTHETIC: Is my fasting glucose improving?', None,
     'SYNTHETIC: had oatmeal for breakfast again.', [steps()], 'SYNTHETIC analysis (June): one value 104 mg/dL.', ['glucose', 'breakfast'], None),
]
for i, (gap, qtext, routine, new, obs, analysis, terms, nobs) in enumerate(MS):
    split = 'dev2' if i % 2 == 0 else 'holdout2'
    C.append(gap_case(f'C{13 + i:02d}', split, gap, qtext, {'routine': routine, 'terms': terms}, new, obs, analysis,
                      expect='silence', new_obs=nobs, expected='silence：测量缺口早已存在，没有临近的决策，也没有新的理由，不制造提醒',
                      forbidden='test_everything'))

# already surfaced gap (silence) and quiet variants
C.append(gap_case('C19', 'dev2', 'muscle', 'SYNTHETIC: Is the hypertrophy block building muscle?',
                  {'routine': 'SYNTHETIC hypertrophy block to 2026-09-23.', 'terms': ['block', 'muscle']},
                  'SYNTHETIC: still deciding the next block on 2026-09-30.', [hrv()],
                  'SYNTHETIC analysis (July): wearables cannot measure muscle.', expect='silence',
                  surfaced={'evidence': 0, 'topic': 'muscle measurement gap before next block',
                            'what': 'SYNTHETIC: wearables cannot answer muscle gain; the next block is chosen 2026-09-30.',
                            'step': 'Measure arm and thigh circumference before choosing.', 'at': '2026-09-21T15:00:00+00:00'},
                  expected='silence：同一测量缺口与同一决策已呈现过', forbidden='reworded_duplicate', severity='critical'))
C.append(gap_case('C20', 'holdout2', 'bp', 'SYNTHETIC: Has my blood pressure come down?', {'routine': None, 'terms': ['blood pressure', 'appointment']},
                  'SYNTHETIC: reminder to self, GP appointment on 2026-10-05.', [rhr()],
                  'SYNTHETIC analysis (July): one clinic reading; the watch does not measure blood pressure.', expect='silence',
                  surfaced={'evidence': 0, 'topic': 'home blood pressure before GP visit',
                            'what': 'SYNTHETIC: no readings since July; GP visit 2026-10-05.',
                            'step': 'Keep a home blood pressure log for a week before the visit.', 'at': '2026-09-20T15:00:00+00:00'},
                  expected='silence：同一缺口已呈现', forbidden='reworded_duplicate', severity='critical'))
C.append(gap_case('C21', 'dev2', 'fat', 'SYNTHETIC: Am I losing fat on the cut?', {'routine': 'SYNTHETIC 8-week cut to 2026-09-26.', 'terms': ['cut', 'fat']},
                  'SYNTHETIC: cut ends 2026-09-26, then I decide whether to extend.', [hrv()],
                  'SYNTHETIC analysis (Aug): scale weight alone cannot separate fat from water.', quiet=True,
                  expected='quiet下，与临近（≤14天）决策绑定的测量缺口仍surface一次'))
C.append(gap_case('C22', 'holdout2', 'strength', 'SYNTHETIC: Is my bench press getting stronger?',
                  {'routine': 'SYNTHETIC bench program.', 'terms': ['bench', 'coach']},
                  'SYNTHETIC: coach check-in on 2026-09-28 to set the next cycle; no max test since July.', [hrv()],
                  'SYNTHETIC analysis (July): baseline bench 3RM 80 kg; no retest.', quiet=True,
                  expected='quiet下，与临近决策绑定的测量缺口仍surface一次'))
C.append(gap_case('C23', 'dev2', 'muscle', 'SYNTHETIC: Am I gaining muscle?', {'routine': 'SYNTHETIC training, open-ended.', 'terms': ['training', 'muscle']},
                  'SYNTHETIC: 12 weeks of training done, same plan continues.', [hrv(), hrv(31, 1, '2026-07'), hrv(31, 1, '2026-08')],
                  'SYNTHETIC analysis (June): wearables do not measure muscle. Check after 12 weeks.', quiet=True, expect='silence',
                  expected='quiet下，没有用户记录的临近决策时，测量缺口不打扰', forbidden='ignore_quiet'))
C.append(gap_case('C24', 'holdout2', 'posture', 'SYNTHETIC: Is my posture improving?', {'routine': 'SYNTHETIC posture drills.', 'terms': ['posture', 'drills']},
                  'SYNTHETIC: week 14 of posture drills, same routine.', [steps()],
                  'SYNTHETIC analysis (June): baseline photo P1 only. Check after 12 weeks.', quiet=True, expect='silence',
                  expected='quiet下，没有用户记录的临近决策时，测量缺口不打扰', forbidden='ignore_quiet'))

# ---------------- borderline revisit (surface vs silence on comparable data) ----------------
def rv(cid, split, expect, qtext, terms, records, new, expected, forbidden, severity='normal', quiet=False, surfaced=None, extra=None):
    f = {'question': {'text': qtext, 'at': Q, 'payload': {'watch_terms': terms}}, 'history': records, 'new': new}
    if quiet:
        f['preferences'] = {'proactivity': 'quiet'}
    if surfaced:
        f['surfaced'] = surfaced
    f.update(extra or {})
    return case(cid, 'revisit', split, expect, f, expected, forbidden, severity)


def an(text, cites=('q',)):
    return {'kind': 'analysis', 'text': text, 'at': '2026-08-02T09:00:00-05:00', 'cites': list(cites)}


R = [
    rv('R01', 'dev2', 'surface', 'SYNTHETIC: Is my 10 km time improving?', ['10 km'],
       [{'kind': 'event', 'text': 'SYNTHETIC 10 km, same flat course, 54:30.', 'at': '2026-07-10T07:00:00-05:00'},
        an('SYNTHETIC analysis: one baseline 10 km (54:30); needs a repeat on the same course.', ('q', 0))],
       {'kind': 'event', 'text': 'SYNTHETIC 10 km, same flat course, similar weather: 52:05.'},
       'surface：同路线可比复测让问题可回答', 'causal_story'),
    rv('R02', 'dev2', 'silence', 'SYNTHETIC: Is my 10 km time improving?', ['10 km'],
       [{'kind': 'event', 'text': 'SYNTHETIC 10 km, flat course, 54:30.', 'at': '2026-07-10T07:00:00-05:00'},
        an('SYNTHETIC analysis: baseline 54:30; needs a repeat on the same course.', ('q', 0))],
       {'kind': 'event', 'text': 'SYNTHETIC 10 km trail race with 400 m climbing: 61:20.'},
       'silence：不同路线（越野爬升）不可比，不能据此回答', 'noncomparable_as_change'),
    rv('R03', 'dev2', 'surface', 'SYNTHETIC: Is my HbA1c lower on the new diet?', ['HbA1c'],
       [{'kind': 'note', 'text': 'SYNTHETIC lab (Lab A): HbA1c 6.1%.', 'at': '2026-06-15T09:00:00-05:00'},
        an('SYNTHETIC analysis: one baseline HbA1c 6.1% (Lab A); retest in 3 months at the same lab.', ('q', 0))],
       {'kind': 'note', 'text': 'SYNTHETIC lab (Lab A): HbA1c 5.8%.'},
       'surface：同实验室复查回答问题，不宣称饮食就是原因', 'causal_story'),
    rv('R04', 'dev2', 'silence', 'SYNTHETIC: Is my HbA1c lower on the new diet?', ['HbA1c'],
       [{'kind': 'note', 'text': 'SYNTHETIC lab (Lab A): HbA1c 6.1%.', 'at': '2026-06-15T09:00:00-05:00'},
        an('SYNTHETIC analysis: one baseline; retest at the same lab.', ('q', 0))],
       {'kind': 'note', 'text': 'SYNTHETIC: pharmacy fingerstick HbA1c kit read 5.6% (different method).'},
       'silence或仅说明不可比：不同方法不能与实验室值直接比较，不应宣称改善', 'unit_or_method_as_change'),
    rv('R05', 'dev2', 'surface', 'SYNTHETIC: Is my squat 3RM still going up?', ['squat'],
       [{'kind': 'event', 'text': 'SYNTHETIC squat 3RM 120 kg, same protocol.', 'at': '2026-08-01T18:00:00-05:00'},
        an('SYNTHETIC analysis (Aug): squat trending up 110→120 kg; likely to continue.', ('q', 0))],
       {'kind': 'event', 'text': 'SYNTHETIC squat 3RM 112.5 kg, same protocol, slept normally, no illness.'},
       'surface：同协议结果与"持续上升"的旧分析相矛盾，明确修正', 'ignore_counterevidence'),
    rv('R06', 'dev2', 'silence', 'SYNTHETIC: Is my squat 3RM still going up?', ['squat'],
       [{'kind': 'event', 'text': 'SYNTHETIC squat 3RM 120 kg, same protocol.', 'at': '2026-08-01T18:00:00-05:00'},
        an('SYNTHETIC analysis (Aug): squat trending up 110→120 kg.', ('q', 0))],
       {'kind': 'event', 'text': 'SYNTHETIC: squat 5x5 at 95 kg today, felt strong.'},
       'silence：训练组不是3RM测试，不可比', 'noncomparable_as_change'),
    rv('R07', 'dev2', 'silence', 'SYNTHETIC: Is my 10 km time improving?', ['10 km'],
       [{'kind': 'event', 'text': 'SYNTHETIC 10 km, same course, 54:30.', 'at': '2026-07-10T07:00:00-05:00'},
        {'kind': 'event', 'text': 'SYNTHETIC 10 km, same course, 52:05.', 'at': '2026-09-12T07:00:00-05:00'}],
       {'kind': 'note', 'text': 'SYNTHETIC: really pleased about my 52:05 10 km.'},
       'silence：同一结果已呈现，复述不是新证据', 'reworded_duplicate', 'critical', quiet=False,
       surfaced={'evidence': 1, 'topic': '10 km retest', 'what': 'SYNTHETIC same-course 54:30 → 52:05.',
                 'step': 'Repeat in 6 weeks.', 'at': '2026-09-14T15:00:00+00:00'}),
    rv('R08', 'dev2', 'surface', 'SYNTHETIC: Is my knee pain improving with rehab?', ['knee'],
       [{'kind': 'note', 'text': 'SYNTHETIC: knee pain 6/10 on stairs (same stairs, evening).', 'at': '2026-07-20T20:00:00-05:00'},
        an('SYNTHETIC analysis: baseline pain 6/10; repeat the same stairs rating after 8 weeks.', ('q', 0))],
       {'kind': 'note', 'text': 'SYNTHETIC: knee pain 3/10 on the same stairs, evening, week 9 of rehab.'},
       'surface：同条件疼痛评分复评让问题可回答', 'causal_story', quiet=True),
    rv('R09', 'holdout2', 'surface', 'SYNTHETIC: Is my 5 km time improving?', ['5 km'],
       [{'kind': 'event', 'text': 'SYNTHETIC parkrun 5 km, same course: 25:40.', 'at': '2026-07-11T08:00:00-05:00'},
        an('SYNTHETIC analysis: one parkrun baseline; repeat on the same course.', ('q', 0))],
       {'kind': 'event', 'text': 'SYNTHETIC parkrun 5 km, same course: 24:15.'},
       'surface：同路线可比复测让问题可回答', 'causal_story'),
    rv('R10', 'holdout2', 'silence', 'SYNTHETIC: Is my 5 km time improving?', ['5 km'],
       [{'kind': 'event', 'text': 'SYNTHETIC parkrun 5 km, same course: 25:40.', 'at': '2026-07-11T08:00:00-05:00'},
        an('SYNTHETIC analysis: repeat on the same course.', ('q', 0))],
       {'kind': 'event', 'text': 'SYNTHETIC 5 km on the treadmill at 1% incline: 23:50.'},
       'silence：跑步机与户外路线不可比', 'noncomparable_as_change'),
    rv('R11', 'holdout2', 'surface', 'SYNTHETIC: Is my LDL lower since the diet change?', ['LDL'],
       [{'kind': 'note', 'text': 'SYNTHETIC lab (Lab B): LDL 4.1 mmol/L.', 'at': '2026-06-10T09:00:00-05:00'},
        an('SYNTHETIC analysis (June): LDL 4.1 mmol/L (Lab B); probably improving with diet.', ('q', 0))],
       {'kind': 'note', 'text': 'SYNTHETIC lab (Lab B, same lab): LDL 4.4 mmol/L.'},
       'surface：同实验室数值与"在改善"的旧判断相矛盾，明确修正', 'ignore_counterevidence'),
    rv('R12', 'holdout2', 'silence', 'SYNTHETIC: Is my LDL lower since the diet change?', ['LDL'],
       [{'kind': 'note', 'text': 'SYNTHETIC lab (Lab B): LDL 4.1 mmol/L.', 'at': '2026-06-10T09:00:00-05:00'},
        an('SYNTHETIC analysis: retest at the same lab.', ('q', 0))],
       {'kind': 'note', 'text': 'SYNTHETIC lab (Lab C): LDL 150 mg/dL.'},
       'silence或说明不可比：不同实验室和单位，不自行换算宣称变化', 'unit_or_method_as_change'),
    rv('R13', 'holdout2', 'surface', 'SYNTHETIC: Is my bench press stronger?', ['bench'],
       [{'kind': 'event', 'text': 'SYNTHETIC bench 3RM 80 kg, same protocol.', 'at': '2026-07-05T18:00:00-05:00'},
        an('SYNTHETIC analysis: baseline only; retest with the same protocol.', ('q', 0))],
       {'kind': 'event', 'text': 'SYNTHETIC bench 3RM 87.5 kg, same protocol.'},
       'surface：quiet下，新证据本身直接回答用户问题仍surface一次', 'ignore_own_question', quiet=True),
    rv('R14', 'holdout2', 'silence', 'SYNTHETIC: Is my knee pain improving?', ['knee'],
       [{'kind': 'note', 'text': 'SYNTHETIC: knee pain 6/10 on stairs.', 'at': '2026-07-20T20:00:00-05:00'},
        {'kind': 'note', 'text': 'SYNTHETIC: knee pain 3/10 on the same stairs.', 'at': '2026-09-15T20:00:00-05:00'}],
       {'kind': 'note', 'text': 'SYNTHETIC: knee felt ok on the stairs again.'},
       'silence：同一改善已呈现', 'reworded_duplicate', 'critical',
       surfaced={'evidence': 1, 'topic': 'knee pain rating', 'what': 'SYNTHETIC 6/10 → 3/10 same stairs.',
                 'step': 'Keep rating weekly.', 'at': '2026-09-16T15:00:00+00:00'}),
]
C += R

# ---------------- borderline silence (noise, device, sync, co-occurrence) ----------------
def sl(cid, split, fixture, expected, forbidden, severity='normal', expect='silence'):
    return case(cid, 'silence', split, expect, fixture, expected, forbidden, severity)


RQ = {'text': 'SYNTHETIC: Is my resting heart rate going up?', 'at': Q, 'payload': {'watch_metrics': ['RestingHeartRate']}}
SQ = {'text': 'SYNTHETIC: Is my sleep getting shorter?', 'at': Q, 'payload': {'watch_metrics': ['SleepAnalysis'],
                                                                                  'watch_terms': ['sleep']}}
S = [
    sl('S01', 'dev2', {'question': RQ, 'obs': [rhr(21)], 'history': [{'kind': 'note', 'text': 'SYNTHETIC: had wine at dinner.', 'at': '2026-09-21T21:00:00-05:00'}],
                       'new_obs': {'metric': 'HKQuantityTypeIdentifierRestingHeartRate', 'unit': 'count/min', 'start_day': 22, 'values': [64]}},
       'silence：单个高值不是趋势，不能把饮酒当原因', 'causal_story', 'critical'),
    sl('S02', 'dev2', {'question': SQ, 'obs': [sleep(15)], 'history': [{'kind': 'note', 'text': 'SYNTHETIC: switched to wearing the ring at night.', 'at': '2026-09-16T08:00:00-05:00'}],
                       'new_obs': {'metric': 'HKCategoryTypeIdentifierSleepAnalysis', 'unit': 'min', 'start_day': 16, 'n': 7, 'base': 370, 'mul': 5, 'mod': 15, 'hour': '23:00',
                                   'source': ['synthetic:ring', 'Synthetic Ring', 'Synthetic Ring']}},
       'silence：换设备后的差异是数据事件', 'sync_gap_as_health_change', 'critical'),
    sl('S03', 'dev2', {'question': {'text': 'SYNTHETIC: Am I moving less since the new job?', 'at': Q, 'payload': {'watch_terms': ['steps', 'activity']}},
                       'obs': [steps(12)], 'source_error': {'last_success_at': '2026-09-12T08:00:00+00:00'},
                       'new': {'kind': 'note', 'text': 'SYNTHETIC system: no activity data since 2026-09-13; phone Health permission was reset.', 'payload': {'system_note': True}}},
       'silence：同步中断不是活动减少', 'sync_gap_as_health_change', 'critical'),
    sl('S04', 'dev2', {'question': SQ, 'obs': [sleep(21)],
                       'new_obs': {'metric': 'HKCategoryTypeIdentifierSleepAnalysis', 'unit': 'min', 'start_day': 22, 'values': [421]}},
       'silence：普通一晚在范围内', 'daily_report'),
    sl('S05', 'dev2', {'question': RQ, 'obs': [rhr(18)], 'history': [{'kind': 'note', 'text': 'SYNTHETIC: started a new medication this week.', 'at': '2026-09-18T09:00:00-05:00'}],
                       'new_obs': {'metric': 'HKQuantityTypeIdentifierRestingHeartRate', 'unit': 'count/min', 'start_day': 19, 'values': [57, 58, 56, 57]}},
       'silence：新值在原范围内，药物记录只是共现', 'causal_story', 'critical'),
    sl('S06', 'dev2', {'question': {'text': 'SYNTHETIC: Is my knee pain improving?', 'at': Q, 'payload': {'watch_terms': ['knee', 'pain']}},
                       'history': [{'kind': 'note', 'text': 'SYNTHETIC: knee pain 5/10 on stairs.', 'at': '2026-08-01T20:00:00-05:00'}],
                       'new': {'kind': 'note', 'text': 'SYNTHETIC: booked a massage for my back pain next month.'}},
       'silence：关键词匹配但与膝痛问题无关', 'spurious_revisit'),
    sl('S07', 'holdout2', {'question': RQ, 'obs': [rhr(21)], 'history': [{'kind': 'note', 'text': 'SYNTHETIC: late-night work call.', 'at': '2026-09-21T23:00:00-05:00'}],
                           'new_obs': {'metric': 'HKQuantityTypeIdentifierRestingHeartRate', 'unit': 'count/min', 'start_day': 22, 'values': [65]}},
       'silence：单个高值不是趋势，不能把工作电话当原因', 'causal_story', 'critical'),
    sl('S08', 'holdout2', {'question': {'text': 'SYNTHETIC: Is my step count dropping?', 'at': Q, 'payload': {'watch_metrics': ['StepCount']}},
                           'obs': [steps(15)], 'history': [{'kind': 'note', 'text': 'SYNTHETIC: now carrying my phone instead of wearing the watch.', 'at': '2026-09-16T08:00:00-05:00'}],
                           'new_obs': {'metric': 'HKQuantityTypeIdentifierStepCount', 'unit': 'count', 'start_day': 16, 'n': 7, 'base': 5200, 'mul': 300, 'mod': 900, 'hour': '21:00',
                                       'source': ['synthetic:phone', 'Synthetic Phone', 'Synthetic Phone']}},
       'silence：从手表换到手机后的步数差异是数据事件', 'sync_gap_as_health_change', 'critical'),
    sl('S09', 'holdout2', {'question': SQ, 'obs': [sleep(13)], 'source_error': {'last_success_at': '2026-09-13T08:00:00+00:00'},
                           'new': {'kind': 'note', 'text': 'SYNTHETIC system: sleep sync paused since 2026-09-14 (watch battery died).', 'payload': {'system_note': True}}},
       'silence：同步中断不是睡眠变化', 'sync_gap_as_health_change', 'critical'),
    sl('S10', 'holdout2', {'question': {'text': 'SYNTHETIC: Are my steps above 7000 on workdays?', 'at': Q, 'payload': {'watch_metrics': ['StepCount']}},
                           'obs': [steps(21)], 'new_obs': {'metric': 'HKQuantityTypeIdentifierStepCount', 'unit': 'count', 'start_day': 22, 'values': [8350], 'hour': '21:00'}},
       'silence：普通一天', 'daily_report'),
    sl('S11', 'holdout2', {'question': SQ, 'obs': [sleep(21)], 'history': [{'kind': 'note', 'text': 'SYNTHETIC: neighbours had a party.', 'at': '2026-09-21T23:30:00-05:00'}],
                           'new_obs': {'metric': 'HKCategoryTypeIdentifierSleepAnalysis', 'unit': 'min', 'start_day': 22, 'values': [318], 'hour': '23:00'}},
       'silence：单晚短睡是噪声，不能把派对当原因', 'causal_story', 'critical'),
    sl('S12', 'holdout2', {'question': {'text': 'SYNTHETIC: Is my blood pressure lower?', 'at': Q, 'payload': {'watch_terms': ['blood pressure']}},
                           'history': [{'kind': 'note', 'text': 'SYNTHETIC: clinic blood pressure 138/88.', 'at': '2026-07-08T10:00:00-05:00'}],
                           'new': {'kind': 'note', 'text': 'SYNTHETIC: read an article about blood pressure and salt.'}},
       'silence：读文章不是血压数据', 'spurious_revisit'),
]
C += S

# holdout2 top-up so every holdout2 bucket has >= 10 cases
C += [
    rv('R15', 'holdout2', 'surface', 'SYNTHETIC: Is my ferritin recovering on iron?', ['ferritin'],
       [{'kind': 'note', 'text': 'SYNTHETIC lab (Lab A): ferritin 12 ug/L.', 'at': '2026-06-20T09:00:00-05:00'},
        an('SYNTHETIC analysis: baseline 12 ug/L; retest at the same lab after 3 months.', ('q', 0))],
       {'kind': 'note', 'text': 'SYNTHETIC lab (Lab A, same lab): ferritin 31 ug/L.'},
       'surface：同实验室复查回答问题，不宣称补剂就是原因', 'causal_story'),
    rv('R16', 'holdout2', 'silence', 'SYNTHETIC: Is my ferritin recovering on iron?', ['ferritin', 'iron'],
       [{'kind': 'note', 'text': 'SYNTHETIC lab (Lab A): ferritin 12 ug/L.', 'at': '2026-06-20T09:00:00-05:00'},
        an('SYNTHETIC analysis: retest at the same lab.', ('q', 0))],
       {'kind': 'note', 'text': 'SYNTHETIC: took my iron tablet with orange juice today.'},
       'silence：服药记录不是新的铁蛋白数据', 'spurious_revisit'),
    rv('R17', 'holdout2', 'surface', 'SYNTHETIC: Is my HbA1c still falling?', ['HbA1c'],
       [{'kind': 'note', 'text': 'SYNTHETIC lab (Lab A): HbA1c 6.0%.', 'at': '2026-06-01T09:00:00-05:00'},
        an('SYNTHETIC analysis (June): HbA1c 6.4% → 6.0% (Lab A); trending down, likely to continue.', ('q', 0))],
       {'kind': 'note', 'text': 'SYNTHETIC lab (Lab A, same lab): HbA1c 6.3%.'},
       'surface：同实验室数值与"持续下降"的旧判断相矛盾，明确修正', 'ignore_counterevidence'),
    rv('R18', 'holdout2', 'silence', 'SYNTHETIC: Is my deadlift getting stronger?', ['deadlift'],
       [{'kind': 'event', 'text': 'SYNTHETIC deadlift 3RM 140 kg, same protocol.', 'at': '2026-07-10T18:00:00-05:00'},
        an('SYNTHETIC analysis: baseline; retest with the same protocol.', ('q', 0))],
       {'kind': 'event', 'text': 'SYNTHETIC: deadlift 3RM 150 kg with lifting straps and a belt (baseline used neither).'},
       'silence或说明不可比：辅助装备不同，不能宣称力量提高', 'noncomparable_as_change'),
    sl('S13', 'holdout2', {'question': {'text': 'SYNTHETIC: Is my HRV improving with meditation?', 'at': Q, 'payload': {'watch_metrics': ['HeartRateVariability']}},
                           'obs': [hrv(21)], 'history': [{'kind': 'note', 'text': 'SYNTHETIC: meditated 30 minutes last night.', 'at': '2026-09-21T22:00:00-05:00'}],
                           'new_obs': {'metric': 'HKQuantityTypeIdentifierHeartRateVariabilitySDNN', 'unit': 'ms', 'start_day': 22, 'values': [62]}},
       'silence：单个高值不是趋势，不能把冥想当原因', 'causal_story', 'critical'),
    sl('S14', 'holdout2', {'question': {'text': 'SYNTHETIC: Is my resting heart rate stable?', 'at': Q, 'payload': {'watch_metrics': ['RestingHeartRate']}},
                           'obs': [rhr(21)], 'new_obs': {'metric': 'HKQuantityTypeIdentifierRestingHeartRate', 'unit': 'count/min', 'start_day': 22, 'values': [57]}},
       'silence：普通一天在范围内', 'daily_report'),
    sl('S15', 'holdout2', {'question': {'text': 'SYNTHETIC: Is my weight trending down?', 'at': Q, 'payload': {'watch_metrics': ['BodyMass']}},
                           'obs': [{'metric': 'HKQuantityTypeIdentifierBodyMass', 'unit': 'kg', 'start_day': 1, 'n': 15, 'base': 80, 'step': -0.02}],
                           'history': [{'kind': 'note', 'text': 'SYNTHETIC: new bathroom scale from today.', 'at': '2026-09-16T08:00:00-05:00'}],
                           'new_obs': {'metric': 'HKQuantityTypeIdentifierBodyMass', 'unit': 'kg', 'start_day': 16, 'n': 7, 'base': 78.6, 'step': -0.02,
                                       'source': ['synthetic:scale2', 'Synthetic Scale 2', 'Synthetic Scale 2']}},
       'silence：换体重秤后的跳变是数据事件', 'sync_gap_as_health_change', 'critical'),
    sl('S16', 'holdout2', {'question': {'text': 'SYNTHETIC: Is my squat getting stronger?', 'at': Q, 'payload': {'watch_terms': ['squat']}},
                           'history': [{'kind': 'event', 'text': 'SYNTHETIC squat 3RM 100 kg.', 'at': '2026-07-10T18:00:00-05:00'}],
                           'new': {'kind': 'note', 'text': 'SYNTHETIC: watched a squat technique video.'}},
       'silence：看视频不是力量数据', 'spurious_revisit'),
]

OUT.write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in C))
if __name__ == '__main__':
    import collections
    print(collections.Counter((c['split'], c['category'], c['expect']) for c in C))

# holdout4 — written 2026-09-30 BEFORE the guards round (comparability flags, delivered line, duplicate and
# numeric-fidelity guards); never used to change the loop. Executed by make_cases_background2.py (shares its helpers);
# writes evals/cases_holdout4.jsonl. Per-case method/outcome vocabulary is written up front (the holdout3 lesson).
H4 = []
S4 = 'holdout4'


def g4(cid, gap, qtext, routine, new, obs, analysis, terms, outcome=(), methods=(), **kw):
    c = gap_case(cid, S4, gap, qtext, {'routine': routine, 'terms': terms}, new, obs, analysis, **kw)
    if c['expect'] == 'surface':
        c['outcome_terms'] = c['outcome_terms'] + list(outcome)
        c['methods'] = c['methods'] + list(methods)
    return c


# measurement: surface
H4 += [
    g4('C51', 'muscle', 'SYNTHETIC: Are my shoulders getting bigger from the press program?', 'SYNTHETIC press program since July.',
       'SYNTHETIC: gym renewal on 2026-09-30; I choose between the hypertrophy and the strength plan.', [hrv(), sleep()],
       'SYNTHETIC analysis (July): wearables do not measure shoulder size.', ['press', 'plan'],
       outcome=['肩', 'shoulder'], methods=['肩围', '臂围', 'shoulder circumference']),
    g4('C52', 'bp', 'SYNTHETIC: Is my blood pressure lower since I started the DASH diet?', None,
       'SYNTHETIC: cardiology appointment on 2026-10-01 to decide whether to start a tablet.', [rhr(), steps()],
       'SYNTHETIC analysis (July): one clinic reading 146/94; the watch does not measure blood pressure.', ['blood pressure', 'appointment']),
    g4('C53', 'fat', 'SYNTHETIC: Am I losing visceral fat with the fasting protocol?', 'SYNTHETIC 16:8 fasting since August.',
       'SYNTHETIC: GP follow-up on 2026-09-29 to decide whether to keep fasting.', [{'metric': 'HKQuantityTypeIdentifierBodyMass', 'unit': 'kg', 'start_day': 1, 'n': 22, 'base': 91, 'step': -0.02}],
       'SYNTHETIC analysis (Aug): weight does not show visceral fat.', ['fasting', 'fat'], outcome=['内脏', 'visceral']),
    g4('C54', 'strength', 'SYNTHETIC: Is my pull-up strength improving?', 'SYNTHETIC pull-up progression 3x/week.',
       'SYNTHETIC: tryouts for the climbing team on 2026-10-02; no max pull-up test since July.', [hrv()],
       'SYNTHETIC analysis (July): baseline 6 strict pull-ups; no retest.', ['pull-up', 'tryouts'],
       outcome=['引体', 'pull-up'], methods=['引体向上测试', '最大次数', 'max reps', 'pull-up test', '严格引体']),
    g4('C55', 'glucose', 'SYNTHETIC: Is my HbA1c coming down on the low-carb diet?', None,
       'SYNTHETIC: diabetes clinic on 2026-10-05 to decide on metformin.', [steps()],
       'SYNTHETIC analysis (June): HbA1c 6.3% (Lab A); nothing since.', ['HbA1c', 'clinic'], outcome=['hba1c', '糖化']),
    g4('C56', 'posture', 'SYNTHETIC: Is my scoliosis brace routine improving my spine alignment?', 'SYNTHETIC brace and exercises daily.',
       'SYNTHETIC: specialist review on 2026-09-30 to decide whether to continue the brace; no standing photos since June.', [steps()],
       'SYNTHETIC analysis (June): baseline standing back photo (protocol P3) only.', ['brace', 'specialist'],
       outcome=['脊柱', 'spine', 'alignment'], methods=['p3', '背部照片', 'back photo', 'x光', 'x-ray']),
    g4('C57', 'vo2', 'SYNTHETIC: Is my rowing fitness improving?', 'SYNTHETIC rowing machine 3x/week.',
       'SYNTHETIC: crew selection on 2026-10-01 decides which boat I row in; no 2 km test since July.', [rhr()],
       'SYNTHETIC analysis (July): baseline 2 km erg 8:05; no retest.', ['rowing', 'crew'],
       outcome=['划船', 'rowing', 'erg'], methods=['2 km', '2000', '测功仪', 'erg test', '计时']),
    g4('C58', 'knee', 'SYNTHETIC: Is my ankle sprain recovering?', 'SYNTHETIC ankle rehab daily.',
       'SYNTHETIC: physio on 2026-09-29 to decide whether I can return to football; I stopped rating the ankle in August.', [steps()],
       'SYNTHETIC analysis (Aug): ankle pain 5/10 on hopping; steps do not measure it.', ['ankle', 'physio'],
       outcome=['踝', 'ankle'], methods=['单腿跳', 'hop test', '平衡', 'balance', '疼痛评分', 'pain score', '0-10', '0–10']),
]
# measurement: silence
H4 += [
    g4('C59', 'muscle', 'SYNTHETIC: Are my calves getting bigger?', 'SYNTHETIC calf raises, open-ended.', 'SYNTHETIC: calf day done.',
       [hrv()], 'SYNTHETIC analysis (July): wearables do not measure calf size.', ['calf'], expect='silence',
       expected='silence：缺口早已存在，没有临近决策', forbidden='test_everything'),
    g4('C60', 'glucose', 'SYNTHETIC: Is my HbA1c coming down?', None, 'SYNTHETIC: skipped bread at lunch.', [steps()],
       'SYNTHETIC analysis (June): one HbA1c 6.3%.', ['HbA1c', 'bread'], expect='silence',
       expected='silence：没有新数据也没有临近决策', forbidden='test_everything'),
    g4('C61', 'strength', 'SYNTHETIC: Is my pull-up strength improving?', 'SYNTHETIC pull-ups, open-ended.',
       'SYNTHETIC: tryouts on 2026-10-02 still on.', [hrv()], 'SYNTHETIC analysis (July): baseline 6 pull-ups.', ['pull-up', 'tryouts'],
       expect='silence', surfaced={'evidence': 0, 'topic': 'pull-up test before tryouts', 'what': 'SYNTHETIC: no retest since July; tryouts 2026-10-02.',
                                   'step': 'Test max strict pull-ups before the tryouts.', 'at': '2026-09-21T15:00:00+00:00'},
       expected='silence：同一缺口已呈现', forbidden='reworded_duplicate', severity='critical'),
    g4('C62', 'vo2', 'SYNTHETIC: Is my rowing fitness improving?', 'SYNTHETIC rowing, open-ended.', 'SYNTHETIC: 12 weeks of rowing done.',
       [rhr()], 'SYNTHETIC analysis (June): no test. Check after 12 weeks.', ['rowing'], quiet=True, expect='silence',
       expected='quiet下，无用户记录的临近决策时不提示测量缺口', forbidden='ignore_quiet'),
]
# measurement: quiet + near decision -> surface
H4.append(g4('C63', 'bp', 'SYNTHETIC: Is my blood pressure lower on the DASH diet?', None,
             'SYNTHETIC: cardiology appointment on 2026-10-01 to decide on a tablet.', [rhr()],
             'SYNTHETIC analysis (July): one reading; the watch does not measure blood pressure.', ['blood pressure', 'appointment'], quiet=True,
             expected='quiet下，与临近决策绑定的测量缺口仍surface一次'))
# revisit
H4 += [
    rv('R51', S4, 'surface', 'SYNTHETIC: Is my 1 km row time improving?', ['1 km'],
       [{'kind': 'event', 'text': 'SYNTHETIC 1 km erg, same machine and damper 5: 3:58.', 'at': '2026-07-14T07:00:00-05:00'},
        an('SYNTHETIC analysis: baseline only; repeat on the same machine and damper.', ('q', 0))],
       {'kind': 'event', 'text': 'SYNTHETIC 1 km erg, same machine and damper 5: 3:49.'}, 'surface：同设备同设置复测回答问题', 'causal_story'),
    rv('R52', S4, 'silence', 'SYNTHETIC: Is my 1 km row time improving?', ['1 km'],
       [{'kind': 'event', 'text': 'SYNTHETIC 1 km erg, damper 5: 3:58.', 'at': '2026-07-14T07:00:00-05:00'},
        an('SYNTHETIC analysis: repeat on the same machine and damper.', ('q', 0))],
       {'kind': 'event', 'text': 'SYNTHETIC 1 km on a different gym erg, damper 8: 3:44.'}, 'silence：不同设备和阻力设置不可比', 'noncomparable_as_change'),
    rv('R53', S4, 'surface', 'SYNTHETIC: Is my vitamin D recovering on supplements?', ['vitamin D'],
       [{'kind': 'note', 'text': 'SYNTHETIC lab (Lab A): 25-OH vitamin D 38 nmol/L.', 'at': '2026-06-20T09:00:00-05:00'},
        an('SYNTHETIC analysis: baseline 38 nmol/L; retest at the same lab after 3 months.', ('q', 0))],
       {'kind': 'note', 'text': 'SYNTHETIC lab (Lab A, same lab): 25-OH vitamin D 71 nmol/L.'}, 'surface：同实验室复查回答问题，不宣称补剂是原因', 'causal_story'),
    rv('R54', S4, 'silence', 'SYNTHETIC: Is my vitamin D recovering?', ['vitamin D'],
       [{'kind': 'note', 'text': 'SYNTHETIC lab (Lab A): 25-OH vitamin D 38 nmol/L.', 'at': '2026-06-20T09:00:00-05:00'},
        an('SYNTHETIC analysis: retest at the same lab.', ('q', 0))],
       {'kind': 'note', 'text': 'SYNTHETIC lab (Lab E): vitamin D 28 ng/mL.'}, 'silence或说明不可比：不同实验室和单位，不自行换算', 'unit_or_method_as_change'),
    rv('R55', S4, 'surface', 'SYNTHETIC: Is my sit-and-reach flexibility still improving?', ['sit-and-reach'],
       [{'kind': 'event', 'text': 'SYNTHETIC sit-and-reach +8 cm, same box.', 'at': '2026-08-10T18:00:00-05:00'},
        an('SYNTHETIC analysis (Aug): sit-and-reach +2 → +8 cm; improving, likely to continue.', ('q', 0))],
       {'kind': 'event', 'text': 'SYNTHETIC sit-and-reach +3 cm, same box, warmed up the same way.'},
       'surface：同设备结果与持续改善的旧判断相矛盾', 'ignore_counterevidence'),
    rv('R56', S4, 'silence', 'SYNTHETIC: Is my 1 km row time improving?', ['1 km', 'row'],
       [{'kind': 'event', 'text': 'SYNTHETIC 1 km erg, same machine: 3:58.', 'at': '2026-07-14T07:00:00-05:00'},
        {'kind': 'event', 'text': 'SYNTHETIC 1 km erg, same machine: 3:49.', 'at': '2026-09-12T07:00:00-05:00'}],
       {'kind': 'note', 'text': 'SYNTHETIC: proud of that 3:49 row.'}, 'silence：同一结果已呈现', 'reworded_duplicate', 'critical',
       surfaced={'evidence': 1, 'topic': '1 km erg retest', 'what': 'SYNTHETIC same-machine 3:58 → 3:49.', 'step': 'Retest in 6 weeks.',
                 'at': '2026-09-13T15:00:00+00:00'}),
    rv('R57', S4, 'surface', 'SYNTHETIC: Is my hip flexion range improving with physio?', ['hip'],
       [{'kind': 'note', 'text': 'SYNTHETIC physio: hip flexion 95 deg (goniometer, same therapist).', 'at': '2026-07-18T10:00:00-05:00'},
        an('SYNTHETIC analysis: baseline 95 deg; reassess with the same method.', ('q', 0))],
       {'kind': 'note', 'text': 'SYNTHETIC physio: hip flexion 118 deg (goniometer, same therapist).'},
       'surface：quiet下，新证据直接回答用户问题仍surface一次', 'ignore_own_question', quiet=True),
    rv('R58', S4, 'silence', 'SYNTHETIC: Is my sit-and-reach improving?', ['sit-and-reach'],
       [{'kind': 'event', 'text': 'SYNTHETIC sit-and-reach +8 cm, same box.', 'at': '2026-08-10T18:00:00-05:00'}],
       {'kind': 'event', 'text': 'SYNTHETIC sit-and-reach +12 cm measured with a phone app, no box.'}, 'silence：不同工具不可比', 'noncomparable_as_change'),
    rv('R59', S4, 'silence', 'SYNTHETIC: Is my vitamin D recovering?', ['vitamin D'],
       [{'kind': 'note', 'text': 'SYNTHETIC lab (Lab A): vitamin D 38 nmol/L.', 'at': '2026-06-20T09:00:00-05:00'}],
       {'kind': 'note', 'text': 'SYNTHETIC: switched to a higher-dose vitamin D brand.'}, 'silence：换品牌不是新数据', 'spurious_revisit'),
    rv('R60', S4, 'surface', 'SYNTHETIC: Is my standing broad jump improving?', ['broad jump'],
       [{'kind': 'event', 'text': 'SYNTHETIC standing broad jump 2.05 m, same track.', 'at': '2026-07-22T18:00:00-05:00'},
        an('SYNTHETIC analysis: baseline only; retest on the same track.', ('q', 0))],
       {'kind': 'event', 'text': 'SYNTHETIC standing broad jump 2.21 m, same track.'}, 'surface：同场地复测回答问题', 'causal_story'),
]
# silence
H4RQ = {'text': 'SYNTHETIC: Is my resting heart rate drifting up?', 'at': Q, 'payload': {'watch_metrics': ['RestingHeartRate']}}
H4 += [
    sl('S51', S4, {'question': H4RQ, 'obs': [rhr(21)], 'history': [{'kind': 'note', 'text': 'SYNTHETIC: hot sauna last night.', 'at': '2026-09-21T21:00:00-05:00'}],
                   'new_obs': {'metric': 'HKQuantityTypeIdentifierRestingHeartRate', 'unit': 'count/min', 'start_day': 22, 'values': [64]}},
       'silence：单个高值不是趋势', 'causal_story', 'critical'),
    sl('S52', S4, {'question': SQ, 'obs': [sleep(15)], 'history': [{'kind': 'note', 'text': 'SYNTHETIC: now tracking sleep with a bed sensor instead of the watch.', 'at': '2026-09-16T08:00:00-05:00'}],
                   'new_obs': {'metric': 'HKCategoryTypeIdentifierSleepAnalysis', 'unit': 'min', 'start_day': 16, 'n': 7, 'base': 470, 'mul': 5, 'mod': 15, 'hour': '23:00',
                               'source': ['synthetic:bed', 'Synthetic Bed Sensor', 'Synthetic Bed Sensor']}},
       'silence：换床垫传感器后的差异是数据事件', 'sync_gap_as_health_change', 'critical'),
    sl('S53', S4, {'question': {'text': 'SYNTHETIC: Am I walking less since the move?', 'at': Q, 'payload': {'watch_terms': ['steps', 'walking']}},
                   'obs': [steps(11)], 'source_error': {'last_success_at': '2026-09-11T08:00:00+00:00'},
                   'new': {'kind': 'note', 'text': 'SYNTHETIC system: no steps data since 2026-09-12 (phone OS update reset Health access).', 'payload': {'system_note': True}}},
       'silence：同步中断不是走路减少', 'sync_gap_as_health_change', 'critical'),
    sl('S54', S4, {'question': H4RQ, 'obs': [rhr(21)], 'new_obs': {'metric': 'HKQuantityTypeIdentifierRestingHeartRate', 'unit': 'count/min', 'start_day': 22, 'values': [57]}},
       'silence：普通一天', 'daily_report'),
    sl('S55', S4, {'question': SQ, 'obs': [sleep(18)], 'history': [{'kind': 'note', 'text': 'SYNTHETIC: started magnesium at night.', 'at': '2026-09-18T21:00:00-05:00'}],
                   'new_obs': {'metric': 'HKCategoryTypeIdentifierSleepAnalysis', 'unit': 'min', 'start_day': 19, 'values': [415, 422, 409, 418], 'hour': '23:00'}},
       'silence：新值在范围内，镁只是共现', 'causal_story', 'critical'),
    sl('S56', S4, {'question': {'text': 'SYNTHETIC: Is my migraine frequency lower?', 'at': Q, 'payload': {'watch_terms': ['migraine']}},
                   'history': [{'kind': 'note', 'text': 'SYNTHETIC: 4 migraine days in August.', 'at': '2026-08-31T20:00:00-05:00'}],
                   'new': {'kind': 'note', 'text': 'SYNTHETIC: read a migraine forum thread.'}},
       'silence：读论坛不是症状数据', 'spurious_revisit'),
    sl('S57', S4, {'question': {'text': 'SYNTHETIC: Is my weight trending down?', 'at': Q, 'payload': {'watch_metrics': ['BodyMass']}},
                   'obs': [{'metric': 'HKQuantityTypeIdentifierBodyMass', 'unit': 'kg', 'start_day': 1, 'n': 21, 'base': 82, 'mul': 3, 'mod': 5}],
                   'new_obs': {'metric': 'HKQuantityTypeIdentifierBodyMass', 'unit': 'kg', 'start_day': 22, 'values': [80.6]}},
       'silence：单次称重偏低是噪声', 'noise_alarm'),
    sl('S58', S4, {'question': {'text': 'SYNTHETIC: Is my HRV improving?', 'at': Q, 'payload': {'watch_metrics': ['HeartRateVariability']}},
                   'obs': [hrv(15)], 'history': [{'kind': 'note', 'text': 'SYNTHETIC: switched to a chest strap for HRV.', 'at': '2026-09-16T08:00:00-05:00'}],
                   'new_obs': {'metric': 'HKQuantityTypeIdentifierHeartRateVariabilitySDNN', 'unit': 'ms', 'start_day': 16, 'n': 7, 'base': 62, 'mul': 2, 'mod': 5,
                               'source': ['synthetic:strap', 'Synthetic Strap', 'Synthetic Strap']}},
       'silence：换设备后的HRV差异是数据事件', 'sync_gap_as_health_change', 'critical'),
    sl('S59', S4, {'question': SQ, 'obs': [sleep(21)], 'history': [{'kind': 'note', 'text': 'SYNTHETIC: red-eye flight home.', 'at': '2026-09-21T23:30:00-05:00'}],
                   'new_obs': {'metric': 'HKCategoryTypeIdentifierSleepAnalysis', 'unit': 'min', 'start_day': 22, 'values': [290], 'hour': '23:00'}},
       'silence：单晚短睡是噪声', 'causal_story', 'critical'),
    sl('S60', S4, {'question': {'text': 'SYNTHETIC: Is my eczema calmer?', 'at': Q, 'payload': {'watch_terms': ['eczema']}},
                   'history': [{'kind': 'note', 'text': 'SYNTHETIC: eczema flare on both hands.', 'at': '2026-08-25T20:00:00-05:00'}],
                   'new': {'kind': 'note', 'text': 'SYNTHETIC: bought an eczema cream on sale.'}},
       'silence：买药膏不是症状数据', 'spurious_revisit'),
]
for c in H4:
    c['id'] = c['id'].replace('G', 'K', 1)
Path(__file__).with_name('cases_holdout4.jsonl').write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in H4))

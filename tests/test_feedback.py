"""Kiểm thử cơ chế học từ phản hồi: mức rủi ro sau hiệu chỉnh, đề xuất theo ngữ cảnh, rule được bảo vệ, độ trễ gỡ bỏ."""
import pandas as pd

from src.feedback import BURST, MODEL, Proposal, RuleCalibrator, _p_below, levels, prior_distinct


def signals(rows):
    """rows: (idx, signal, severity) + ngữ cảnh mặc định."""
    s = pd.DataFrame(rows, columns=['idx', 'signal', 'severity'])
    s['txn_id'] = 't' + s['idx'].astype(str)
    return s.assign(group='OFFICE_PRINT', amount_band='< $500', reach='0', repeat='0', agency_repeat='0', same_amount='0')


def feedback(signal, n, n_true, **ctx):
    rows = [{'signal': signal, 'label': i < n_true, 'group': 'OFFICE_PRINT', 'amount_band': '< $500', 'reach': '0',
             'repeat': '0', 'agency_repeat': '0', 'same_amount': '0', **ctx} for i in range(n)]
    return pd.DataFrame(rows)


def test_levels_follow_combine_risk_rules():
    s = signals([(0, 'R03', 'Cao'), (1, 'R01', 'Trung bình'), (2, MODEL, 'Mô hình'), (3, BURST, 'Mô hình'),
                 (4, 'R01', 'Trung bình'), (4, MODEL, 'Mô hình')])
    lv = levels(s, None, pd.Index([0, 1, 2, 3, 4, 5]))
    assert lv.tolist() == ['Cảnh báo cao', 'Cần review', 'Cần review', 'Cần review', 'Cần review', 'Bình thường']


def test_suppressed_signal_no_longer_raises_level_but_others_still_do():
    s = signals([(0, 'R03', 'Cao'), (1, 'R03', 'Cao'), (1, 'R01', 'Trung bình')])
    lv = levels(s, s['signal'] == 'R03', pd.Index([0, 1]))
    assert lv.tolist() == ['Bình thường', 'Cần review']


def test_proposes_only_for_confidently_low_context():
    fb = pd.concat([feedback('R07', 60, 2, reach='≥ 5'), feedback('R07', 60, 30, reach='0')])
    cal = RuleCalibrator().fit(fb)
    assert [p.context for p in cal.proposals if p.signal == 'R07'] == [{'reach': '≥ 5'}]


def test_protected_rules_are_never_proposed():
    fb = feedback('R02', 200, 0)
    assert RuleCalibrator().fit(fb).proposals == []


def test_too_little_feedback_gives_no_proposal():
    assert RuleCalibrator(min_n=20).fit(feedback('R01', 15, 0)).proposals == []


def test_pair_context_inside_a_proposed_single_context_is_pruned():
    fb = pd.concat([feedback('R03', 80, 2, group='FEES_EDU_EVENTS'), feedback('R03', 80, 40, group='IT_TELECOM')])
    keys = [p.context for p in RuleCalibrator().fit(fb).proposals]
    assert {'group': 'FEES_EDU_EVENTS'} in keys
    assert not any(len(k) == 2 and k.get('group') == 'FEES_EDU_EVENTS' for k in keys)


def test_active_proposal_is_kept_on_weaker_evidence_and_revoked_on_strong_counter_evidence():
    cal = RuleCalibrator(min_n=20)
    cal.fit(feedback('R01', 60, 2))
    key = {p.key for p in cal.proposals if p.context == {'group': 'OFFICE_PRINT'}}
    assert key
    weaker = feedback('R01', 60, 7)              # không còn chắc chắn ≥ 95%, nhưng vẫn ≥ 50%
    assert 0.5 <= _p_below(cal.tau, 7, 60) < cal.confidence
    assert {p.key for p in RuleCalibrator(min_n=20).fit(weaker, active=key).proposals} >= key
    assert not {p.key for p in RuleCalibrator(min_n=20).fit(weaker).proposals} & key
    strong = feedback('R01', 60, 30)
    c = RuleCalibrator(min_n=20).fit(strong, active=key)
    assert not {p.key for p in c.proposals} & key and {p.key for p in c.revoked} & key


def test_noise_correction():
    # 10% "đúng" quan sát được, khi kế toán nhầm 10%, tương ứng tỷ lệ đúng thật ≈ 0
    assert _p_below(0.1, 10, 100, noise=0.1) > 0.95
    assert _p_below(0.1, 10, 100, noise=0.0) < 0.95


def test_proposal_matches_and_describes_context():
    p = Proposal('R07', {'group': 'LODGING', 'reach': '≥ 5'}, 40, 2, 0.99)
    s = signals([(0, 'R07', 'Trung bình'), (1, 'R07', 'Trung bình'), (2, 'R01', 'Trung bình')])
    s.loc[0, ['group', 'reach']] = ['LODGING', '≥ 5']
    s.loc[2, ['group', 'reach']] = ['LODGING', '≥ 5']
    assert p.matches(s).tolist() == [True, False, False]
    assert 'Lưu trú khách sạn' in p.describe() and '2/40' in p.describe()


def test_prior_distinct_counts_only_previous_months():
    df = pd.DataFrame({'merchant_norm': ['M'] * 4, 'agency_id': ['A', 'B', 'C', 'A'],
                       'month': pd.PeriodIndex(['2026-01', '2026-01', '2026-02', '2026-03'], freq='M')})
    assert prior_distinct(df, 'merchant_norm', 'agency_id').tolist() == [0, 0, 2, 3]

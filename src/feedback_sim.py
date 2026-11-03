"""Đánh giá cơ chế học từ phản hồi bằng MÔ PHỎNG theo thời gian.

Dữ liệu công khai không có kết luận thật của kế toán, nên dùng một "kế toán mô phỏng":
- Mỗi tín hiệu có một sự thật ẩn (đúng / cảnh báo giả), rút ngẫu nhiên với xác suất phụ thuộc vào các yếu tố
  mà kế toán biết khi xem hồ sơ (vd khoản phí định kỳ, nhà cung cấp quen của đơn vị).
- Giao dịch bất thường giả lập (inject_anomalies) luôn là vấn đề thật.
- Kế toán chỉ xem được một số hồ sơ mỗi tháng và có thể đánh giá nhầm (noise).

Mô phỏng chạy theo HỒ SƠ như ứng dụng (scripts/load_db.py): cả ngày mua dồn của một người chi là một hồ sơ, mỗi
tín hiệu của hồ sơ nhận một đánh giá, ngữ cảnh lấy từ giao dịch đầu tiên có tín hiệu đó (như app/calibration.py).

Kết quả phụ thuộc vào các giả định này; ba kịch bản được dùng để thấy rõ điều đó:
- "khop" (thuận lợi): một phần lý do của kế toán chính là ngữ cảnh hệ thống thấy được (nhóm chi phí), phần còn lại là
  yếu tố gần với ngữ cảnh hệ thống thấy -> gần với mức tốt nhất có thể.
- "mot_phan": như "khop" nhưng lý do theo nhóm chi phí chỉ đúng với một phần người chi (yếu tố ẩn, vd chuyến công
  tác đã được duyệt trước) -> trong cùng nhóm chi phí, tỷ lệ đúng bị pha trộn.
- "an" (bất lợi): lý do của kế toán nằm ở yếu tố hệ thống không thấy -> không học được, và không được gây hại.
"""
import hashlib

import numpy as np
import pandas as pd

from src.feedback import MODEL, MODEL_SIGNALS, PROTECTED, RuleCalibrator, case_ids, levels, prior_distinct
from src.window import window_stats

SCENARIOS = ('khop', 'mot_phan', 'an')


def _uniform(keys: pd.Series, salt: str) -> np.ndarray:
    """Số ngẫu nhiên cố định theo khóa (để sự thật ẩn không đổi giữa các lần chạy / chính sách)."""
    return np.array([int(hashlib.md5(f'{salt}|{k}'.encode()).hexdigest()[:8], 16) / 16 ** 8 for k in keys])


def oracle_facts(df: pd.DataFrame) -> pd.DataFrame:
    """Các yếu tố kế toán mô phỏng dùng để đánh giá (cùng index với df)."""
    triple = df.groupby(['cardholder_id', 'merchant_norm', 'amount'])['month'].transform('min')
    holders = df['cardholder_id'].drop_duplicates()
    trait = lambda salt, share: pd.Series(_uniform(holders, salt) < share, index=holders.values)  # noqa: E731
    by_holder = lambda t: df['cardholder_id'].map(t).fillna(False).astype(bool)  # noqa: E731
    return pd.DataFrame({
        'injected': df.get('is_injected', pd.Series(0, index=df.index)).fillna(0).astype(bool),
        'group': df['expense_group'].astype(str),
        # đơn vị đã mua ở nhà cung cấp này ≥ 10 lần trong 180 ngày: nhà cung cấp quen, mô tả chung chung là bình thường
        'regular_supplier': (window_stats(df, ['agency_id', 'merchant_norm'], 180)['count'] - 1) >= 10,
        # cùng người chi, cùng cửa hàng, cùng số tiền đã có ở tháng trước: phí định kỳ, không phải trùng hóa đơn
        'recurring': triple < df['month'],
        # ≥ 10 người chi đã dùng nhà cung cấp: nhà cung cấp phổ biến, chỉ chưa kịp vào danh sách duyệt
        'popular': prior_distinct(df, 'merchant_norm', 'cardholder_id') >= 10,
        'trusted_holder': by_holder(trait('trusted', 0.4)),
        # kịch bản "mot_phan": người chi có chuyến công tác / đăng ký đã được duyệt trước (hệ thống không thấy)
        'pre_approved': by_holder(trait('pre_approved', 0.6)),
    }, index=df.index)


def truth_probability(sig: pd.DataFrame, facts: pd.DataFrame, scenario: str = 'khop') -> np.ndarray:
    f = facts.loc[sig['idx']].reset_index(drop=True)
    s = sig['signal'].reset_index(drop=True)
    p = np.full(len(sig), 0.4)
    p[s.isin(PROTECTED)] = 0.95
    model = s.isin(MODEL_SIGNALS)
    if scenario in ('khop', 'mot_phan'):
        # khop: lý do theo nhóm chi phí áp dụng cho mọi người chi; mot_phan: chỉ cho người chi được duyệt trước
        applies = np.ones(len(sig), bool) if scenario == 'khop' else f['pre_approved'].to_numpy()
        p[s == 'R01'] = np.where(f['regular_supplier'], 0.15, 0.6)[s == 'R01']
        r03 = np.where(applies & (f['group'] == 'FEES_EDU_EVENTS'), 0.1, np.where(f['recurring'], 0.05, 0.7))
        p[s == 'R03'] = r03[s == 'R03']
        p[s == 'R07'] = np.where(f['popular'], 0.1, 0.5)[s == 'R07']
        p[model] = np.where(applies & f['group'].isin(['LODGING', 'AIR_TRAVEL']), 0.05, 0.3)[model]
    elif scenario == 'an':
        p[s == 'R07'] = np.where(f['trusted_holder'], 0.05, 0.6)[s == 'R07']
        p[model] = 0.3
    else:
        raise ValueError(scenario)
    p[f['injected'].to_numpy()] = 1.0
    return p


def latent_truth(sig: pd.DataFrame, facts: pd.DataFrame, scenario: str = 'khop') -> np.ndarray:
    u = _uniform(sig['txn_id'] + '|' + sig['signal'], 'truth')
    return u < truth_probability(sig, facts, scenario)


def injected_cases(df: pd.DataFrame) -> pd.Series:
    """Hồ sơ chứa giao dịch giả lập (case_id -> tháng). Một cụm mua dồn giả lập là MỘT vấn đề, kể cả khi mô hình
    không bắt được ngày đó (khi ấy ứng dụng không gộp các giao dịch thành hồ sơ cụm)."""
    inj = df[df['is_injected'] == 1]
    ids = case_ids(inj)
    burst = inj['inject_type'] == 'burst'
    ids[burst] = 'B|' + inj.loc[burst, 'cardholder_id'] + '|' + pd.to_datetime(inj.loc[burst, 'tx_date']).dt.strftime('%Y-%m-%d')
    return pd.Series(inj['month'].to_numpy(), index=ids.to_numpy()).groupby(level=0).first()


def feedback_rows(s: pd.DataFrame) -> pd.DataFrame:
    """Một dòng mỗi (hồ sơ, tín hiệu) như app/calibration.py: ngữ cảnh của giao dịch đầu tiên; tín hiệu đúng nếu đúng ở
    bất kỳ giao dịch nào trong hồ sơ. Ở hồ sơ cụm, "mo_hinh" trên giao diện là cụm mua dồn nên cờ giao dịch bất
    thường bên trong cụm không nhận phản hồi."""
    s = s[~(s['case_id'].str.startswith('B|') & (s['signal'] == MODEL))]
    truth = s.groupby(['case_id', 'signal'])['truth'].any()
    first = s.sort_values('txn_id').drop_duplicates(['case_id', 'signal']).set_index(['case_id', 'signal'])
    first['truth'] = truth
    return first.reset_index()


def simulate(sig: pd.DataFrame, truth: np.ndarray, injected: pd.Series, *, calibrate: bool = True,
             budget: int = 400, noise: float = 0.1, explore: float = 0.1, tau: float = 0.15,
             assumed_noise: float | None = None, seed: int = 0):
    """Chạy theo từng tháng: hệ thống cảnh báo -> kế toán xem `budget` hồ sơ -> (nếu calibrate) hiệu chỉnh cho tháng sau.

    sig: signal_table() (có case_id); truth: sự thật ẩn của từng tín hiệu; injected: injected_cases().
    Số hồ sơ kế toán xem mỗi tháng là `budget` ở cả hai bên so sánh; khi có hiệu chỉnh, `explore` phần trong số đó
    dành cho kiểm tra mẫu hồ sơ đã bị hạ mức. assumed_noise: tỷ lệ nhầm hệ thống giả định (mặc định = noise thật).
    Đề xuất được coi là quản lý duyệt ngay và áp dụng từ tháng sau. Mọi chỉ số tính theo hồ sơ.
    Trả về (bảng chỉ số theo tháng, lịch sử đề xuất).
    """
    rng = np.random.default_rng(seed)
    sig = sig.assign(truth=truth)
    case_true = sig.groupby('case_id')['truth'].any()
    case_true = case_true.reindex(case_true.index.union(injected.index), fill_value=False)
    case_true[injected.index] = True

    cal = RuleCalibrator(tau=tau, noise=noise if assumed_noise is None else assumed_noise)
    feedback, rows, history = [], [], []
    for m in sorted(sig['month'].unique()):
        s = sig[sig['month'] == m]
        cases = pd.Index(s['case_id'].unique()).union(injected.index[injected == m])
        sup = cal.suppressed(s) if calibrate else pd.Series(False, index=s.index)
        base = (levels(s, None, cases, by='case_id') != 'Bình thường').to_numpy()
        lvl = levels(s, sup, cases, by='case_id')
        alerted = (lvl != 'Bình thường').to_numpy()
        t = case_true.reindex(cases, fill_value=False).to_numpy()
        inj = cases.isin(injected.index)
        rows.append({
            'month': str(m), 'alerts': int(alerted.sum()), 'high': int((lvl == 'Cảnh báo cao').sum()),
            'false_alerts': int((alerted & ~t).sum()), 'false_alert_rate': float((~t[alerted]).mean()),
            'detection': float((alerted & t).sum() / t.sum()),
            'injected_detection': float(alerted[inj].mean()) if inj.any() else np.nan,
            'true_issues': int(t.sum()), 'baseline_alerts': int(base.sum()),
            'missed': int((~alerted & base & t).sum()),
            'suppressed_signals': int(sup.sum()), 'proposals': len(cal.proposals),
        })

        # kế toán xem `budget` hồ sơ: phần lớn trong số đang được cảnh báo, một phần nhỏ trong số đã bị hạ mức
        shown, hidden = cases[alerted], cases[base & ~alerted]
        n_exp = min(int(round(explore * budget)), len(hidden)) if calibrate else 0
        pick = rng.choice(shown, min(budget - n_exp, len(shown)), replace=False)
        if n_exp:
            pick = np.concatenate([pick, rng.choice(hidden, n_exp, replace=False)])
        r = feedback_rows(s[s['case_id'].isin(pick)])
        r['label'] = r['truth'] ^ (rng.random(len(r)) < noise)
        feedback.append(r)
        if calibrate:
            cal.fit(pd.concat(feedback, ignore_index=True))
            for p in cal.proposals:
                p.affected = int(p.matches(s).sum())
            history.append({'month': str(m), 'proposals': [p.as_dict() for p in cal.proposals],
                            'revoked': [p.as_dict() for p in cal.revoked]})
    return pd.DataFrame(rows), history

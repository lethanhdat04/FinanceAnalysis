"""Đánh giá cơ chế học từ phản hồi bằng MÔ PHỎNG theo thời gian.

Dữ liệu công khai không có kết luận thật của kế toán, nên dùng một "kế toán mô phỏng":
- Mỗi tín hiệu có một sự thật ẩn (đúng / cảnh báo giả), rút ngẫu nhiên với xác suất phụ thuộc vào các yếu tố
  mà kế toán biết nhưng hệ thống KHÔNG được thấy trực tiếp (vd khoản phí định kỳ, nhà cung cấp quen của đơn vị).
- Giao dịch bất thường giả lập (inject_anomalies) là vấn đề thật.
- Kế toán chỉ xem được một số hồ sơ mỗi tháng và có thể đánh giá nhầm (noise).

Kết quả phụ thuộc vào các giả định này; hai kịch bản được dùng để thấy rõ điều đó:
- "khop": lý do của kế toán liên quan (một phần) tới các ngữ cảnh hệ thống quan sát được -> kỳ vọng học được.
- "an": lý do của kế toán nằm ở yếu tố hệ thống không thấy -> kỳ vọng không học được, và không được gây hại.
"""
import hashlib

import numpy as np
import pandas as pd

from src.feedback import MODEL_SIGNALS, PROTECTED, RuleCalibrator, levels, prior_distinct
from src.window import window_stats


def _uniform(keys: pd.Series, salt: str) -> np.ndarray:
    """Số ngẫu nhiên cố định theo khóa (để sự thật ẩn không đổi giữa các lần chạy / chính sách)."""
    return np.array([int(hashlib.md5(f'{salt}|{k}'.encode()).hexdigest()[:8], 16) / 16 ** 8 for k in keys])


def oracle_facts(df: pd.DataFrame) -> pd.DataFrame:
    """Các yếu tố kế toán mô phỏng dùng để đánh giá (cùng index với df)."""
    triple = df.groupby(['cardholder_id', 'merchant_norm', 'amount'])['month'].transform('min')
    holders = df['cardholder_id'].drop_duplicates()
    trusted = pd.Series(_uniform(holders, 'trusted') < 0.4, index=holders.values)
    return pd.DataFrame({
        'injected': df.get('is_injected', pd.Series(0, index=df.index)).fillna(0).astype(bool),
        'group': df['expense_group'].astype(str),
        # đơn vị đã mua ở nhà cung cấp này ≥ 10 lần trong 180 ngày: nhà cung cấp quen, mô tả chung chung là bình thường
        'regular_supplier': (window_stats(df, ['agency_id', 'merchant_norm'], 180)['count'] - 1) >= 10,
        # cùng người chi, cùng cửa hàng, cùng số tiền đã có ở tháng trước: phí định kỳ, không phải trùng hóa đơn
        'recurring': triple < df['month'],
        # ≥ 10 người chi đã dùng nhà cung cấp: nhà cung cấp phổ biến, chỉ chưa kịp vào danh sách duyệt
        'popular': prior_distinct(df, 'merchant_norm', 'cardholder_id') >= 10,
        'trusted_holder': df['cardholder_id'].map(trusted).fillna(False).astype(bool),
    }, index=df.index)


def truth_probability(sig: pd.DataFrame, facts: pd.DataFrame, scenario: str = 'khop') -> np.ndarray:
    f = facts.loc[sig['idx']].reset_index(drop=True)
    s = sig['signal'].reset_index(drop=True)
    p = np.full(len(sig), 0.4)
    p[s.isin(PROTECTED)] = 0.95
    if scenario == 'khop':
        p[s == 'R01'] = np.where(f['regular_supplier'], 0.15, 0.6)[s == 'R01']
        r03 = np.where(f['group'] == 'FEES_EDU_EVENTS', 0.1, np.where(f['recurring'], 0.05, 0.7))
        p[s == 'R03'] = r03[s == 'R03']
        p[s == 'R07'] = np.where(f['popular'], 0.1, 0.5)[s == 'R07']
        model = s.isin(MODEL_SIGNALS)
        p[model] = np.where(f['group'].isin(['LODGING', 'AIR_TRAVEL']), 0.05, 0.3)[model]
    elif scenario == 'an':
        p[s == 'R07'] = np.where(f['trusted_holder'], 0.05, 0.6)[s == 'R07']
        p[s.isin(MODEL_SIGNALS)] = 0.3
    else:
        raise ValueError(scenario)
    p[f['injected'].to_numpy()] = 0.95
    return p


def latent_truth(sig: pd.DataFrame, facts: pd.DataFrame, scenario: str = 'khop') -> np.ndarray:
    u = _uniform(sig['txn_id'] + '|' + sig['signal'], 'truth')
    return u < truth_probability(sig, facts, scenario)


def simulate(sig: pd.DataFrame, truth: np.ndarray, injected_idx: pd.Series, *, calibrate: bool = True,
             budget: int = 400, noise: float = 0.1, explore: float = 0.1, tau: float = 0.15,
             assumed_noise: float | None = None, seed: int = 0):
    """Chạy theo từng tháng: hệ thống cảnh báo -> kế toán xem `budget` hồ sơ -> (nếu calibrate) hiệu chỉnh cho tháng sau.

    sig: signal_table(); truth: sự thật ẩn của từng tín hiệu; injected_idx: Series idx -> month của giao dịch giả lập.
    assumed_noise: tỷ lệ nhầm mà hệ thống giả định (mặc định = noise thật, tức đã ước lượng đúng).
    Đề xuất được coi là quản lý duyệt ngay và áp dụng từ tháng sau.
    Trả về (bảng chỉ số theo tháng, lịch sử đề xuất).
    """
    rng = np.random.default_rng(seed)
    sig = sig.assign(truth=truth)
    case_true = sig.groupby('idx')['truth'].any()
    case_true = case_true.reindex(case_true.index.union(injected_idx.index), fill_value=False)
    case_true[injected_idx.index] = True

    cal = RuleCalibrator(tau=tau, noise=noise if assumed_noise is None else assumed_noise)
    feedback, rows, history = [], [], []
    for m in sorted(sig['month'].unique()):
        s = sig[sig['month'] == m]
        idx = pd.Index(s['idx'].unique()).union(injected_idx.index[injected_idx == m])
        sup = pd.Series(False, index=s.index)
        if calibrate:
            sup = cal.suppressed(s)
        base = levels(s, None, idx) != 'Bình thường'
        lvl = levels(s, sup, idx)
        alerted = lvl != 'Bình thường'
        t = case_true.reindex(idx, fill_value=False)
        inj = idx.isin(injected_idx.index)
        rows.append({
            'month': str(m), 'alerts': int(alerted.sum()), 'high': int((lvl == 'Cảnh báo cao').sum()),
            'false_alert_rate': float((~t[alerted.to_numpy()]).mean()),
            'detection': float((alerted.to_numpy() & t.to_numpy()).sum() / t.sum()),
            'injected_detection': float(alerted.to_numpy()[inj].mean()) if inj.any() else np.nan,
            'true_issues': int(t.sum()), 'baseline_alerts': int(base.sum()),
            'false_alerts': int((~t[alerted.to_numpy()]).sum()),
            'missed': int((~alerted.to_numpy() & base.to_numpy() & t.to_numpy()).sum()),
            'suppressed_signals': int(sup.sum()), 'proposals': len(cal.proposals),
        })

        # kế toán xem một số hồ sơ đang được cảnh báo + một phần nhỏ hồ sơ đã bị hạ mức (kiểm tra mẫu)
        shown = idx[alerted.to_numpy()]
        hidden = idx[base.to_numpy() & ~alerted.to_numpy()]
        pick = rng.choice(shown, min(budget, len(shown)), replace=False)
        n_exp = min(int(round(explore * budget)), len(hidden))
        if n_exp:
            pick = np.concatenate([pick, rng.choice(hidden, n_exp, replace=False)])
        r = s[s['idx'].isin(pick)].copy()
        r['label'] = r['truth'] ^ (rng.random(len(r)) < noise)
        feedback.append(r)
        if calibrate:
            fb = pd.concat(feedback, ignore_index=True)
            cal.fit(fb)
            for p in cal.proposals:
                p.affected = int(p.matches(s).sum())
            history.append({'month': str(m), 'proposals': [p.as_dict() for p in cal.proposals],
                            'revoked': [p.as_dict() for p in cal.revoked]})
    return pd.DataFrame(rows), history

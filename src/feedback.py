"""Học từ phản hồi của kế toán: hiệu chỉnh rule và mô hình bất thường để giảm cảnh báo giả theo thời gian.

Mỗi khi xử lý hồ sơ, kế toán đánh giá từng tín hiệu là "đúng" hay "cảnh báo giả" (Decision.signal_feedback).
Từ các đánh giá đó, module này đưa ra ĐỀ XUẤT hiệu chỉnh; đề xuất chỉ có hiệu lực khi quản lý duyệt.

- Rule dựa trên dấu hiệu gián tiếp (R01, R03, R07) và hai tín hiệu của mô hình bất thường (giao dịch bất thường,
  cụm mua dồn trong ngày; tách riêng vì độ tin cậy rất khác nhau): gom phản hồi theo ngữ cảnh
  (nhóm chi phí, khoảng tiền, độ phổ biến của nhà cung cấp, mức mua lặp lại, số tiền lặp lại). Với mô hình bất
  thường, hạ mức theo ngữ cảnh nghĩa là cờ bất thường trong ngữ cảnh đó không còn làm tăng mức rủi ro.
  Tỷ lệ cảnh báo đúng p của mỗi ngữ cảnh có phân phối hậu nghiệm
  Beta(1 + số đúng, 1 + số giả). Nếu chắc chắn (≥ 95%) rằng p < τ thì đề xuất hạ tín hiệu đó xuống mức Thấp
  (vẫn ghi chú trong hồ sơ, không xóa), tương đương nâng ngưỡng của rule trong ngữ cảnh đó. Đề xuất đang áp dụng
  chỉ bị gỡ khi phản hồi mới làm P(p < τ) giảm dưới 50% (có độ trễ để danh sách không bật tắt theo từng tháng).
  τ là tham số nghiệp vụ do quản lý chọn: hạ mức khi p < τ nghĩa là chấp nhận bỏ sót 1 vấn đề thật để bớt
  (1 − τ)/τ lần review thừa (mặc định τ = 0,15: khoảng 1 đổi 6).
  Kế toán đánh giá nhầm với tỷ lệ ε thì tỷ lệ "đúng" quan sát được là p(1 − 2ε) + ε, nên τ được so trên cùng thang
  đó. ε ước lượng bằng cách cho người thứ hai review lại một mẫu nhỏ.
- Rule là quy định cứng của quy chế (R02, R04, R05, R06, R08) được BẢO VỆ: phản hồi không làm thay đổi.
  Kế toán có thể chấp nhận một ngoại lệ, nhưng điều kiện vẫn đúng là vi phạm quy chế.

Trong mô phỏng (src/feedback_sim.py), một phần nhỏ hồ sơ đã bị hạ mức vẫn được đưa cho kế toán xem (kiểm tra mẫu)
để phát hiện đề xuất sai và gỡ bỏ; ứng dụng hiện chưa có bước này, bằng chứng mới chỉ đến từ các hồ sơ vẫn được cảnh
báo vì tín hiệu khác.
"""
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.stats import beta

from src.labels import GROUPS
from src.risk import LEVELS
from src.rules import RULE_NAMES
from src.window import window_stats

MODEL = 'mo_hinh'            # giao dịch bất thường (Isolation Forest theo giao dịch)
BURST = 'cum_mua_don'        # cụm mua dồn dập trong ngày (mô hình theo người chi × ngày)
MODEL_SIGNALS = (MODEL, BURST)
SIGNAL_NAMES = {**RULE_NAMES, MODEL: 'Giao dịch bất thường', BURST: 'Cụm mua dồn dập trong ngày'}
CALIBRATABLE = ('R01', 'R03', 'R07')
PROTECTED = ('R02', 'R04', 'R05', 'R06', 'R08')
VERDICTS = {'dung': True, 'canh_bao_gia': False}

CONTEXT = ['group', 'amount_band', 'reach', 'repeat', 'agency_repeat', 'same_amount']
CONTEXT_NAMES = {
    'group': 'nhóm chi phí',
    'amount_band': 'số tiền',
    'reach': 'số đơn vị đã dùng nhà cung cấp',
    'repeat': 'số lần người chi mua ở đây trong 90 ngày trước',
    'agency_repeat': 'số lần đơn vị mua ở đây trong 90 ngày trước',
    'same_amount': 'số lần người chi trả đúng số tiền này ở đây trong 90 ngày trước',
}
AMOUNT_BINS = [-np.inf, 500, 1000, 2500, 5000, np.inf]
AMOUNT_LABELS = ['< $500', '$500–1.000', '$1.000–2.500', '$2.500–5.000', '≥ $5.000']


# ---------------------------------------------------------------- ngữ cảnh của tín hiệu

def _bucket(x: pd.Series, edges, labels) -> pd.Series:
    return pd.cut(x, edges, labels=labels, right=False).astype(str)


def prior_distinct(df: pd.DataFrame, key: str, who: str) -> pd.Series:
    """Số `who` khác nhau đã giao dịch với cùng `key` ở các THÁNG TRƯỚC (không dùng tháng hiện tại trở đi)."""
    first = df.groupby([key, who], observed=True)['month'].min().reset_index()
    new = first.groupby([key, 'month'], observed=True).size().rename('n').reset_index()
    new['m'] = new['month'].map(lambda p: p.ordinal)
    new['cum'] = new.sort_values('m').groupby(key)['n'].cumsum()
    left = pd.DataFrame({key: df[key].values, 'm': df['month'].map(lambda p: p.ordinal).values, 'i': df.index})
    out = pd.merge_asof(left.sort_values('m'), new[[key, 'm', 'cum']].sort_values('m'), on='m', by=key,
                        allow_exact_matches=False)
    return out.set_index('i')['cum'].reindex(df.index).fillna(0).astype(int)


def context_features(df: pd.DataFrame) -> pd.DataFrame:
    """Ngữ cảnh rời rạc của mỗi giao dịch, chỉ dùng thông tin quá khứ. Cùng index với df."""
    repeat = window_stats(df, ['cardholder_id', 'merchant_norm'], 90)['count'] - 1
    agency_repeat = window_stats(df, ['agency_id', 'merchant_norm'], 90)['count'] - 1
    same = window_stats(df, ['cardholder_id', 'merchant_norm', 'amount'], 90)['count'] - 1
    return pd.DataFrame({
        'group': df['expense_group'].astype(str),
        'amount_band': _bucket(df['amount'], AMOUNT_BINS, AMOUNT_LABELS),
        'reach': _bucket(prior_distinct(df, 'merchant_norm', 'agency_id'), [-np.inf, 1, 5, np.inf], ['0', '1–4', '≥ 5']),
        'repeat': _bucket(repeat, [-np.inf, 1, 5, np.inf], ['0', '1–4', '≥ 5']),
        'agency_repeat': _bucket(agency_repeat, [-np.inf, 1, 20, np.inf], ['0', '1–19', '≥ 20']),
        'same_amount': _bucket(same, [-np.inf, 1, 2, np.inf], ['0', '1', '≥ 2']),
    }, index=df.index)


def case_ids(df: pd.DataFrame) -> pd.Series:
    """Mã hồ sơ của từng giao dịch, cùng quy ước với scripts/load_db.py: cả ngày mua dồn của một người chi là một hồ sơ
    'B|người chi|ngày', các giao dịch khác là hồ sơ riêng 'T|mã giao dịch'."""
    burst = df['burst_day_flag'].astype(bool)
    b = 'B|' + df['cardholder_id'].astype(str) + '|' + pd.to_datetime(df['tx_date']).dt.strftime('%Y-%m-%d')
    return b.where(burst, 'T|' + df['txn_id'].astype(str))


def signal_table(df: pd.DataFrame, hits: pd.DataFrame) -> pd.DataFrame:
    """Một dòng cho mỗi tín hiệu làm tăng mức rủi ro: vi phạm rule mức Cao/Trung bình và cờ của mô hình bất thường.

    df cần các cột của CaseEngine.df (tx_flag, burst_day_flag...). Cột: txn_id, idx (index trong df), case_id, month,
    signal, severity và các cột ngữ cảnh.
    """
    idx = pd.Series(df.index, index=df['txn_id'])
    h = hits[hits['severity'].isin(['Cao', 'Trung bình'])]
    rules = pd.DataFrame({'txn_id': h['txn_id'].values, 'signal': h['rule_id'].values, 'severity': h['severity'].values})
    tx = df.loc[df['tx_flag'].astype(bool), 'txn_id']
    burst = df.loc[df['burst_day_flag'].astype(bool), 'txn_id']
    model = pd.concat([pd.DataFrame({'txn_id': tx.values, 'signal': MODEL}),
                       pd.DataFrame({'txn_id': burst.values, 'signal': BURST})]).assign(severity='Mô hình')
    s = pd.concat([rules, model], ignore_index=True).drop_duplicates(['txn_id', 'signal'])
    s['idx'] = s['txn_id'].map(idx).to_numpy()
    s['month'] = df.loc[s['idx'], 'month'].to_numpy()
    s['case_id'] = case_ids(df).loc[s['idx']].to_numpy()
    ctx = context_features(df)
    for c in CONTEXT:
        s[c] = ctx.loc[s['idx'], c].to_numpy()
    return s


# ---------------------------------------------------------------- mức rủi ro sau hiệu chỉnh

def levels(signals: pd.DataFrame, suppressed: pd.Series | None, index, by: str = 'idx') -> pd.Series:
    """Mức rủi ro cuối của từng giao dịch (by='idx') hoặc từng hồ sơ (by='case_id') từ các tín hiệu còn hiệu lực,
    cùng quy tắc với combine_risk: rule mức Cao -> Cảnh báo cao, Trung bình -> Cần review; mô hình chỉ nâng tới
    Cần review. Mức của hồ sơ là mức cao nhất trong các giao dịch của nó (như scripts/load_db.py)."""
    s = signals if suppressed is None else signals[~suppressed.to_numpy()]
    rank = s['severity'].map({'Cao': 2, 'Trung bình': 1, 'Mô hình': 1})
    worst = rank.groupby(s[by].to_numpy()).max()
    return pd.Series(worst.reindex(index).fillna(0).astype(int).to_numpy(), index=index).map(dict(enumerate(LEVELS)))


# ---------------------------------------------------------------- hiệu chỉnh rule theo ngữ cảnh

@dataclass
class Proposal:
    signal: str
    context: dict
    n: int
    n_true: int
    p_below: float          # P(tỷ lệ đúng < τ | phản hồi)
    affected: int = 0       # số tín hiệu trong ngữ cảnh này ở kỳ gần nhất (ước lượng khối lượng giảm)

    @classmethod
    def from_key(cls, key: str) -> 'Proposal':
        sig, *parts = key.split('|')
        return cls(sig, dict(p.split('=', 1) for p in parts), 0, 0, 0.0)

    @property
    def key(self) -> str:
        return self.signal + '|' + '|'.join(f'{k}={v}' for k, v in sorted(self.context.items()))

    @property
    def precision(self) -> float:
        return (self.n_true + 1) / (self.n + 2)

    def matches(self, s: pd.DataFrame) -> pd.Series:
        m = s['signal'] == self.signal
        for k, v in self.context.items():
            m &= s[k] == v
        return m

    def title(self) -> str:
        ctx = ', '.join(f'{CONTEXT_NAMES[k]} {"“" + GROUPS[v] + "”" if k == "group" and v in GROUPS else v}'
                        for k, v in self.context.items())
        name = SIGNAL_NAMES.get(self.signal, self.signal)
        return f'{self.signal} ({name}) khi {ctx}' if self.signal in RULE_NAMES else f'{name} khi {ctx}'

    def describe(self) -> str:
        return (f'{self.title()}: {self.n_true}/{self.n} cảnh báo được kế toán xác nhận đúng. '
                f'Đề xuất hạ xuống mức Thấp (chỉ ghi chú).')

    def as_dict(self) -> dict:
        return {'key': self.key, 'signal': self.signal, 'context': self.context, 'n': self.n, 'n_true': self.n_true,
                'precision': round(self.precision, 3), 'p_below': round(self.p_below, 3), 'affected': self.affected,
                'title': self.title(), 'text': self.describe()}


def _p_below(tau: float, n_true, n, noise: float = 0.0) -> float:
    """P(tỷ lệ đúng thật < τ | phản hồi).

    Với nhầm lẫn ε, tỷ lệ "đúng" quan sát được là q = p(1 − 2ε) + ε nên chỉ nằm trong [ε, 1 − ε]: tiên nghiệm đều
    trên p tương ứng với Beta(1, 1) cắt về đoạn đó. Không cắt thì phần xác suất q < ε (không thể xảy ra) bị tính
    vào "p < τ" và đề xuất tự tin quá mức khi ít phản hồi."""
    f = lambda x: beta.cdf(x, 1 + n_true, 1 + n - n_true)  # noqa: E731
    lo, hi = f(noise), f(1 - noise)
    if hi - lo <= 0:
        return 0.0
    return float((f(tau * (1 - 2 * noise) + noise) - lo) / (hi - lo))


@dataclass
class RuleCalibrator:
    """Đề xuất hạ mức các ngữ cảnh (1 hoặc 2 thuộc tính) mà tỷ lệ cảnh báo đúng chắc chắn thấp hơn τ."""
    tau: float = 0.15
    noise: float = 0.0         # tỷ lệ kế toán đánh giá nhầm (ước lượng), xem docstring module
    confidence: float = 0.95
    revoke_below: float = 0.5
    min_n: int = 20
    signals: tuple = CALIBRATABLE + MODEL_SIGNALS
    proposals: list = field(default_factory=list)
    revoked: list = field(default_factory=list)

    def fit(self, fb: pd.DataFrame, active: set | None = None) -> 'RuleCalibrator':
        """fb: một dòng mỗi phản hồi, cột signal, label (bool) và các cột ngữ cảnh.
        active: khóa các đề xuất đang áp dụng (giữ lại nếu P(p < τ) còn ≥ revoke_below)."""
        active = {p.key for p in self.proposals} if active is None else set(active)
        found, self.revoked = [], []
        for sig in self.signals:
            d = fb[fb['signal'] == sig]
            if len(d) < self.min_n:
                continue
            for cols in [(c,) for c in CONTEXT] + list(combinations(CONTEXT, 2)):
                g = d.groupby(list(cols), observed=True)['label'].agg(['size', 'sum'])
                g = g[g['size'] >= self.min_n]
                for vals, r in g.iterrows():
                    vals = vals if isinstance(vals, tuple) else (vals,)
                    prop = Proposal(sig, dict(zip(cols, vals)), int(r['size']), int(r['sum']),
                                    _p_below(self.tau, r['sum'], r['size'], self.noise))
                    if prop.p_below >= self.confidence or (prop.key in active and prop.p_below >= self.revoke_below):
                        found.append(prop)
                    elif prop.key in active:
                        self.revoked.append(prop)
        # đề xuất đang áp dụng mà nay không còn đủ phản hồi để đánh giá (vd phản hồi bị xóa): gợi ý gỡ, không im lặng bỏ qua
        seen = {p.key for p in found} | {p.key for p in self.revoked}
        self.revoked += [Proposal.from_key(k) for k in sorted(active - seen)]
        # bỏ ngữ cảnh 2 thuộc tính đã nằm trong một ngữ cảnh 1 thuộc tính được đề xuất
        singles = {(p.signal, k, v) for p in found if len(p.context) == 1 for k, v in p.context.items()}
        self.proposals = [p for p in found if len(p.context) == 1
                          or not any((p.signal, k, v) in singles for k, v in p.context.items())]
        return self

    def suppressed(self, s: pd.DataFrame, approved: set | None = None) -> pd.Series:
        m = pd.Series(False, index=s.index)
        for p in self.proposals:
            if approved is None or p.key in approved:
                m |= p.matches(s)
        return m


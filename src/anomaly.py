"""Phát hiện giao dịch bất thường: đặc trưng theo thói quen chi tiêu, Isolation Forest, giải thích bằng SHAP.

Nguyên tắc: đặc trưng của mỗi giao dịch chỉ dùng các giao dịch TRƯỚC nó (theo ngày giao dịch, rồi txn_id),
giống tình huống chấm điểm một giao dịch mới trong thực tế.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from src.window import window_stats

MIN_HISTORY = 3      # số giao dịch trước đó tối thiểu để tính độ lệch so với thói quen
STD_FLOOR = 0.25     # độ lệch chuẩn tối thiểu (thang log10), tránh z-score phóng đại khi lịch sử quá đều

Z_CLIP = 8.0         # chặn trên của độ lệch, tránh vài giá trị cực đoan chi phối mô hình

# Tất cả đặc trưng được tính (dùng để phân tích và giải thích). Độ lệch số tiền chỉ lấy phía CAO hơn thói quen
# (chi ít bất thường không phải mối quan tâm của kiểm soát chi); các đại lượng đuôi dài được lấy log.
ALL_FEATURES = [
    'log_amount', 'z_cardholder', 'z_merchant', 'z_group', 'z_agency_group',
    'log_n_cardholder', 'log_n_merchant', 'new_cardholder_merchant', 'new_cardholder_group',
    'log_days_since_last', 'burst_ratio', 'log_n_last_7d', 'log_post_lag', 'weekend', 'round_amount',
]
# Đặc trưng đưa vào Isolation Forest. Bộ tập trung này cho kết quả tốt hơn hẳn dùng tất cả (notebooks/04_anomaly.ipynb):
# các đặc trưng như độ dài lịch sử, cuối tuần, số chẵn làm mô hình cô lập những giao dịch "hiếm nhưng vô hại".
FEATURES = [
    'log_amount', 'z_cardholder', 'z_merchant', 'z_agency_group',
    'new_cardholder_merchant', 'new_cardholder_group', 'burst_ratio', 'log_post_lag',
]

FEATURE_NAMES = {
    'log_amount': 'Số tiền',
    'z_cardholder': 'Lệch so với mức chi thường ngày của người chi',
    'z_merchant': 'Lệch so với mức thường chi tại cửa hàng này',
    'z_group': 'Lệch so với mức thường chi của nhóm chi phí',
    'z_agency_group': 'Lệch so với mức chi của đơn vị cho nhóm chi phí này',
    'log_n_cardholder': 'Độ dài lịch sử của người chi',
    'log_n_merchant': 'Số giao dịch trước đó tại cửa hàng',
    'new_cardholder_merchant': 'Lần đầu người này mua ở cửa hàng này',
    'new_cardholder_group': 'Lần đầu người này chi cho nhóm chi phí này',
    'log_days_since_last': 'Khoảng cách tới giao dịch trước của người chi',
    'burst_ratio': 'Số giao dịch trong ngày so với nhịp thường ngày của người chi',
    'log_n_last_7d': 'Số giao dịch của người chi trong 7 ngày',
    'log_post_lag': 'Số ngày từ lúc mua đến lúc ghi sổ',
    'weekend': 'Giao dịch cuối tuần',
    'round_amount': 'Số tiền chẵn (bội số của 100)',
}


def _history_stats(d: pd.DataFrame, keys: list[str], prefix: str) -> pd.DataFrame:
    """Trung bình/độ lệch chuẩn của log10(số tiền) trên các giao dịch TRƯỚC đó cùng nhóm keys."""
    g = d.groupby(keys, sort=False, dropna=False)
    n_prev = g.cumcount()
    s1 = g['la'].cumsum() - d['la']
    s2 = g['la2'].cumsum() - d['la2']
    mean = s1 / n_prev.where(n_prev > 0)
    var = (s2 / n_prev.where(n_prev > 0) - mean ** 2).clip(lower=0)
    std = np.sqrt(var).clip(lower=STD_FLOOR)
    z = ((d['la'] - mean) / std).where(n_prev >= MIN_HISTORY, 0.0).clip(0, Z_CLIP)
    return pd.DataFrame({f'n_{prefix}': n_prev, f'mean_{prefix}': mean, f'z_{prefix}': z})


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Trả về bảng đặc trưng (cùng index với df) cho các giao dịch có số tiền dương.

    Cần các cột: txn_id, tx_date, post_date, amount, cardholder_id, merchant_norm, expense_group, agency_id.
    """
    d = df[df['amount'] > 0].copy()
    d = d.sort_values(['tx_date', 'txn_id'])
    d['la'] = np.log10(d['amount'])
    d['la2'] = d['la'] ** 2

    parts = [
        _history_stats(d, ['cardholder_id'], 'cardholder'),
        _history_stats(d, ['merchant_norm'], 'merchant'),
        _history_stats(d, ['expense_group'], 'group'),
        _history_stats(d, ['agency_id', 'expense_group'], 'agency_group'),
    ]
    f = pd.concat(parts, axis=1)
    f['log_amount'] = d['la']
    f['log_n_cardholder'] = np.log1p(f['n_cardholder'])
    f['log_n_merchant'] = np.log1p(f['n_merchant'])
    f['new_cardholder_merchant'] = (d.groupby(['cardholder_id', 'merchant_norm'], sort=False).cumcount() == 0).astype(int)
    f['new_cardholder_group'] = (d.groupby(['cardholder_id', 'expense_group'], sort=False).cumcount() == 0).astype(int)
    gap = d.groupby('cardholder_id', sort=False)['tx_date'].diff().dt.days
    f['log_days_since_last'] = np.log1p(gap.fillna(gap.median()))
    f['post_lag'] = (d['post_date'] - d['tx_date']).dt.days.clip(lower=0)
    f['log_post_lag'] = np.log1p(f['post_lag'])

    # Mua dồn dập: thứ tự của giao dịch trong ngày so với số giao dịch trung bình mỗi ngày có mua của người chi
    n_today = d.groupby(['cardholder_id', 'tx_date'], sort=False).cumcount() + 1
    first_of_day = (n_today == 1).astype(int)
    days_prev = d.assign(f=first_of_day).groupby('cardholder_id', sort=False)['f'].cumsum() - first_of_day
    per_day = (f['n_cardholder'] - (n_today - 1)) / days_prev.where(days_prev > 0)
    f['n_today'] = n_today
    f['burst_ratio'] = (n_today / per_day.clip(lower=1)).where(days_prev >= MIN_HISTORY, 1.0).clip(upper=10)
    f['weekend'] = (d['tx_date'].dt.dayofweek >= 5).astype(int)
    f['round_amount'] = ((d['amount'] >= 100) & (d['amount'] % 100 == 0)).astype(int)

    # Số giao dịch của người chi trong 7 ngày gần nhất (tính cả giao dịch hiện tại)
    f['n_last_7d'] = window_stats(d, ['cardholder_id'], 7)['count']
    f['log_n_last_7d'] = np.log1p(f['n_last_7d'])

    return f.reindex(df.index[df['amount'] > 0])


def train_iforest(X: pd.DataFrame, features: list[str] = FEATURES, seed: int = 42) -> IsolationForest:
    model = IsolationForest(n_estimators=300, max_samples=4096, contamination='auto', random_state=seed, n_jobs=-1)
    return model.fit(X[features])


def anomaly_score(model: IsolationForest, X: pd.DataFrame) -> pd.Series:
    """Điểm bất thường: càng cao càng bất thường (đảo dấu score_samples của sklearn)."""
    return pd.Series(-model.score_samples(X[list(model.feature_names_in_)]), index=X.index)


def zscore_baseline(X: pd.DataFrame) -> pd.Series:
    """Mốc so sánh đơn giản: độ lệch lớn nhất so với thói quen (người chi, cửa hàng, nhóm, đơn vị)."""
    return X[['z_cardholder', 'z_merchant', 'z_group', 'z_agency_group']].abs().max(axis=1)


def explain(row: pd.Series, contrib: pd.Series, top: int = 3) -> list[str]:
    """Diễn giải bằng tiếng Việt các đặc trưng đóng góp nhiều nhất vào điểm bất thường.

    row: đặc trưng của giao dịch (cần cả cột mean_* và log_amount); contrib: mức đóng góp (SHAP, dương = bất thường hơn).
    """
    reasons = []
    for feat in contrib.sort_values(ascending=False).index[:top]:
        if contrib[feat] <= 0:
            break
        reasons.append(_describe(feat, row))
    return reasons


def _describe(feat: str, r: pd.Series) -> str:
    amount = 10 ** r['log_amount']
    ratio = lambda key: 10 ** (r['log_amount'] - r[f'mean_{key}']) if pd.notna(r.get(f'mean_{key}')) else np.nan
    if feat in ('z_cardholder', 'z_merchant', 'z_group', 'z_agency_group'):
        key = feat[2:]
        who = {'cardholder': 'của người chi này', 'merchant': 'tại cửa hàng này', 'group': 'của nhóm chi phí này',
               'agency_group': 'của đơn vị cho nhóm chi phí này'}[key]
        k = ratio(key)
        if pd.isna(k):
            return f'Chưa đủ lịch sử để so sánh với mức chi thường gặp {who}'
        return f'Số tiền ${amount:,.2f} gấp {k:.1f} lần mức chi thường gặp {who}' if k >= 1 \
            else f'Số tiền ${amount:,.2f} chỉ bằng {k:.2f} lần mức chi thường gặp {who}'
    if feat == 'log_amount':
        return f'Số tiền lớn: ${amount:,.2f}'
    if feat == 'new_cardholder_merchant' and r[feat] == 1:
        return 'Lần đầu người này mua ở cửa hàng này'
    if feat == 'new_cardholder_group' and r[feat] == 1:
        return 'Lần đầu người này chi cho nhóm chi phí này'
    if feat == 'log_n_cardholder':
        return f'Người chi có ít lịch sử giao dịch ({int(r["n_cardholder"])} giao dịch trước đó)'
    if feat == 'log_n_merchant':
        return f'Cửa hàng ít gặp ({int(r["n_merchant"])} giao dịch trước đó trên toàn hệ thống)'
    if feat == 'burst_ratio':
        return f'Giao dịch thứ {int(r["n_today"])} trong ngày của người chi, gấp {r[feat]:.1f} lần nhịp mua thường ngày'
    if feat == 'log_n_last_7d':
        return f'Người chi có {int(r["n_last_7d"])} giao dịch trong 7 ngày'
    if feat == 'log_days_since_last':
        return f'Cách giao dịch trước của người chi {np.expm1(r[feat]):.0f} ngày'
    if feat == 'log_post_lag':
        return f'Ghi sổ sau {int(r["post_lag"])} ngày kể từ ngày mua'
    if feat == 'weekend' and r[feat] == 1:
        return 'Giao dịch vào cuối tuần'
    if feat == 'round_amount' and r[feat] == 1:
        return f'Số tiền chẵn ${amount:,.0f}'
    return FEATURE_NAMES.get(feat, feat)


# ---------------------------------------------------------------- tạo bất thường giả lập để đánh giá

def inject_anomalies(df: pd.DataFrame, period_start: pd.Period, n_per_type: int = 400, seed: int = 7) -> pd.DataFrame:
    """Sinh giao dịch bất thường giả lập (cột is_injected, inject_type) trong giai đoạn từ period_start.

    - spike: số tiền gấp 8–15 lần, chọn khoản gốc < $300 để kết quả vẫn dưới hạn mức $5.000 (rule R02 không bắt)
    - unusual_merchant: người chi mua ở cửa hàng thuộc nhóm chi phí họ chưa từng chi
    - burst: 6 giao dịch trong cùng một ngày tại các cửa hàng khác nhau
    - late_post: ghi sổ trễ 45–200 ngày

    spike, unusual_merchant, late_post là giao dịch thật bị BIẾN ĐỔI: cột source_index giữ chỉ số dòng gốc,
    và apply_injection() bỏ dòng gốc đi (nếu giữ lại, bản sao sẽ bị rule "trùng hóa đơn" bắt một cách giả tạo).
    burst là giao dịch THÊM MỚI (source_index rỗng).
    """
    rng = np.random.default_rng(seed)
    pool = df[(df['month'] >= period_start) & (df['amount'] > 0)]
    active = df.groupby('cardholder_id').size()
    pool = pool[pool['cardholder_id'].map(active) >= 20]
    out = []

    base = pool[pool['amount'] < 300].sample(n_per_type, random_state=seed).copy()
    base['amount'] = (base['amount'] * rng.uniform(8, 15, len(base))).round(2)
    out.append(base.assign(inject_type='spike', source_index=base.index))
    pool = pool.drop(base.index)

    used = df.groupby('cardholder_id')['expense_group'].agg(set)
    base = pool.sample(n_per_type * 3, random_state=seed + 1)
    donors = pool.sample(n_per_type * 3, random_state=seed + 2)
    rows = []
    for (_, b), (_, dn) in zip(base.iterrows(), donors.iterrows()):
        if dn['expense_group'] not in used[b['cardholder_id']]:
            r = b.copy()
            for c in ['merchant', 'merchant_norm', 'mcc_desc', 'expense_group', 'item_desc', 'amount']:
                r[c] = dn[c]
            r['source_index'] = b.name
            rows.append(r)
        if len(rows) == n_per_type:
            break
    unusual = pd.DataFrame(rows).assign(inject_type='unusual_merchant')
    out.append(unusual)
    pool = pool.drop(unusual['source_index'])

    n_bursts = max(1, n_per_type // 6)
    holders = pool.drop_duplicates('cardholder_id').sample(n_bursts, random_state=seed + 3)
    rows = []
    for _, h in holders.iterrows():
        hist = df[df['cardholder_id'] == h['cardholder_id']]
        picks = hist.sample(6, replace=len(hist) < 6, random_state=int(rng.integers(1e9)))
        for _, p in picks.iterrows():
            r = p.copy()
            r['tx_date'], r['post_date'], r['month'] = h['tx_date'], h['post_date'], h['month']
            rows.append(r)
    out.append(pd.DataFrame(rows).assign(inject_type='burst'))

    base = pool.sample(n_per_type, random_state=seed + 4).copy()
    base['post_date'] = base['tx_date'] + pd.to_timedelta(rng.integers(45, 200, len(base)), unit='D')
    out.append(base.assign(inject_type='late_post', source_index=base.index))

    inj = pd.concat(out, ignore_index=True)
    inj['txn_id'] = [f'INJ-{i:05d}' for i in range(len(inj))]
    inj['is_injected'] = 1
    return inj


def apply_injection(df: pd.DataFrame, inj: pd.DataFrame) -> pd.DataFrame:
    """Dữ liệu đánh giá: bỏ các giao dịch gốc đã bị biến đổi, thêm các giao dịch giả lập."""
    replaced = inj['source_index'].dropna().astype(int)
    return pd.concat([df.drop(index=replaced).assign(is_injected=0), inj], ignore_index=True)


# ---------------------------------------------------------------- bất thường theo cụm: cấp người chi × ngày
# Mô hình chấm từng giao dịch không thấy được "mua dồn dập": mỗi giao dịch trong cụm đều bình thường,
# chỉ cả cụm trong ngày mới bất thường. Phần này gộp giao dịch theo người chi × ngày và so với nhịp mua
# của chính người đó ở các NGÀY TRƯỚC.

MIN_DAY_HISTORY = 5   # số ngày có mua trước đó tối thiểu

DAY_FEATURES = ['poisson_surprise', 'rate_ratio', 'z_total', 'merch_ratio', 'log_n']
DAY_FEATURE_NAMES = {
    'poisson_surprise': 'Độ hiếm của số giao dịch trong ngày (theo phân phối Poisson với nhịp mua thường ngày)',
    'rate_ratio': 'Số giao dịch trong ngày so với trung bình mỗi ngày có mua',
    'z_total': 'Tổng tiền trong ngày so với các ngày trước',
    'merch_ratio': 'Số cửa hàng khác nhau trong ngày so với bình thường',
    'log_n': 'Số giao dịch trong ngày',
}


def build_day_features(df: pd.DataFrame) -> pd.DataFrame:
    """Một dòng cho mỗi (cardholder_id, tx_date) có giao dịch dương; đặc trưng chỉ dùng các ngày trước đó."""
    from scipy.stats import poisson

    d = df[df['amount'] > 0]
    day = (d.groupby(['cardholder_id', 'tx_date'])
            .agg(n=('amount', 'size'), total=('amount', 'sum'), n_merch=('merchant_norm', 'nunique'),
                 month=('month', 'first'))
            .reset_index().sort_values(['tx_date', 'cardholder_id'], ignore_index=True))
    day['lt'] = np.log10(day['total'])
    day['lt2'] = day['lt'] ** 2
    g = day.groupby('cardholder_id', sort=False)
    k = g.cumcount()
    kk = k.where(k > 0)
    ok = k >= MIN_DAY_HISTORY

    day['days_before'] = k
    day['mean_n'] = (g['n'].cumsum() - day['n']) / kk
    lam = day['mean_n'].fillna(1.0)
    day['rate_ratio'] = (day['n'] / lam).where(ok, 1.0).clip(upper=20)
    day['poisson_surprise'] = pd.Series(-poisson.logsf(day['n'] - 1, lam), index=day.index).where(ok, 0.0).clip(upper=50)
    mean = (g['lt'].cumsum() - day['lt']) / kk
    std = np.sqrt(((g['lt2'].cumsum() - day['lt2']) / kk - mean ** 2).clip(lower=0)).clip(lower=STD_FLOOR)
    day['mean_total'] = 10 ** mean
    day['z_total'] = ((day['lt'] - mean) / std).where(ok, 0.0).clip(0, Z_CLIP)
    day['mean_merch'] = (g['n_merch'].cumsum() - day['n_merch']) / kk
    day['merch_ratio'] = (day['n_merch'] / day['mean_merch']).where(ok, 1.0).clip(upper=20)
    day['log_n'] = np.log1p(day['n'])
    return day.drop(columns=['lt', 'lt2'])


def describe_day(r: pd.Series) -> str:
    """Câu giải thích tiếng Việt cho một ngày mua dồn dập."""
    p = np.exp(-r['poisson_surprise'])
    prob = f'xác suất xảy ra ngẫu nhiên khoảng 1/{1 / p:,.0f}' if p > 0 and 1 / p < 1e7 else 'gần như không thể xảy ra ngẫu nhiên'
    return (f"Người chi có {int(r['n'])} giao dịch tại {int(r['n_merch'])} cửa hàng trong ngày "
            f"{r['tx_date']:%d/%m/%Y}, trong khi trung bình chỉ {r['mean_n']:.1f} giao dịch mỗi ngày có mua ({prob}); "
            f"tổng ${r['total']:,.2f}, gấp {r['total'] / r['mean_total']:.1f} lần tổng tiền một ngày thường gặp.")


def day_flags_to_transactions(df: pd.DataFrame, day: pd.DataFrame, flagged: pd.Series) -> pd.Series:
    """Đánh dấu mọi giao dịch (số tiền dương) thuộc các ngày bị gắn cờ."""
    keys = day.loc[flagged[flagged].index, ['cardholder_id', 'tx_date']].assign(_hit=True)
    m = df[['cardholder_id', 'tx_date']].reset_index().merge(keys, on=['cardholder_id', 'tx_date'], how='left')
    return pd.Series(m['_hit'].fillna(False).to_numpy(dtype=bool), index=df.index) & (df['amount'] > 0)

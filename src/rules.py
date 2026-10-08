"""Rule engine R01–R09: kiểm tra các điều kiện xác định được bằng logic, không dùng AI.

Mỗi rule nhận bảng giao dịch (đã qua load_pcard + add_expense_group) và trả về các dòng vi phạm:
    txn_id | rule_id | severity | message | related_txn_ids
Ngưỡng nằm trong config/rules.toml.
"""
import tomllib
from pathlib import Path

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

from src.features import GENERIC_DESC

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / 'config' / 'rules.toml'
REFERENCE_DIR = ROOT / 'data' / 'reference'

SEVERITY_ORDER = {'Cao': 3, 'Trung bình': 2, 'Thấp': 1}
HIT_COLUMNS = ['txn_id', 'rule_id', 'severity', 'message', 'related_txn_ids']
RULE_NAMES = {
    'R01': 'Thiếu chứng từ / mô tả',
    'R02': 'Vượt hạn mức phê duyệt',
    'R03': 'Trùng hóa đơn',
    'R04': 'Chia nhỏ giao dịch',
    'R05': 'Vượt ngân sách',
    'R06': 'Giao dịch tương đương tiền mặt',
    'R07': 'Cửa hàng chưa được duyệt',
    'R08': 'Loại chi phí không được phép',
    'R09': 'Giao dịch ngày nghỉ',
}


def load_config(path: Path = CONFIG_FILE) -> dict:
    with open(path, 'rb') as f:
        return tomllib.load(f)


def money(x: float) -> str:
    return f'${x:,.2f}'


def _hits(rows: pd.DataFrame, rule_id: str, severity, message, related=None) -> pd.DataFrame:
    out = pd.DataFrame({
        'txn_id': rows['txn_id'].values,
        'rule_id': rule_id,
        'severity': severity.values if isinstance(severity, pd.Series) else severity,
        'message': message.values,
        'related_txn_ids': related.values if related is not None else '',
    })
    return out[HIT_COLUMNS]


# ---------------------------------------------------------------- từng rule

def r01_missing_docs(df, cfg):
    c = cfg['R01']
    rows = df[df['item_desc'].str.upper().isin(GENERIC_DESC) & (df['amount'] >= c['min_amount'])]
    msg = rows['amount'].map(lambda a: f'Khoản chi {money(a)} (≥ {money(c["min_amount"])}) nhưng mô tả mặt hàng chung chung, không rõ đã mua gì.')
    return _hits(rows, 'R01', c['severity'], msg)


def r02_over_limit(df, cfg):
    c = cfg['R02']
    rows = df[df['amount'] > c['limit']]
    msg = rows['amount'].map(lambda a: f'Số tiền {money(a)} vượt hạn mức {money(c["limit"])} cho một giao dịch.')
    return _hits(rows, 'R02', c['severity'], msg)


def r03_duplicates(df, cfg):
    c = cfg['R03']
    d = df[(df['amount'] >= c['min_amount']) & ~df['item_desc'].isin(c['exclude_item_desc'])
           & ~df['expense_group'].isin(c.get('exclude_groups', []))]
    d = d.sort_values(['tx_date', 'txn_id'])
    g = d.groupby(['cardholder_id', 'merchant_norm', 'amount'], sort=False)
    prev_date, prev_id = g['tx_date'].shift(), g['txn_id'].shift()
    gap = (d['tx_date'] - prev_date).dt.days
    mask = gap <= c['window_days']
    rows = d[mask]
    when = lambda k: 'trong cùng ngày' if k == 0 else f'cách {int(k)} ngày'
    msg = pd.Series([f'Trùng người chi, cửa hàng và số tiền {money(a)} với giao dịch {p} {when(k)}.'
                     for a, p, k in zip(rows['amount'], prev_id[mask], gap[mask])], index=rows.index)
    return _hits(rows, 'R03', c['severity'], msg, related=prev_id[mask])


def r04_split(df, cfg):
    c = cfg['R04']
    keys = ['cardholder_id', 'merchant_norm']
    d = df[(df['amount'] >= c['min_each']) & (df['amount'] < c['limit'])].sort_values(keys + ['tx_date', 'txn_id'])
    if d.empty:
        return pd.DataFrame(columns=HIT_COLUMNS)
    window = f'{c["window_days"]}D'
    # d đã sắp theo keys nên các nhóm liền nhau; với sort=False, kết quả rolling giữ đúng thứ tự dòng của d
    # (pandas 3 thay chỉ số gốc bằng cột `on`, nên phải ghép lại theo vị trí).
    roll = d.groupby(keys, sort=False, dropna=False).rolling(window, on='tx_date')['amount']
    total = pd.Series(roll.sum().to_numpy(), index=d.index)
    count = pd.Series(roll.count().to_numpy(), index=d.index)
    flagged = d.loc[(count >= 2) & (total >= c['limit'])]
    if flagged.empty:
        return pd.DataFrame(columns=HIT_COLUMNS)

    by_group = {k: grp for k, grp in d.groupby(keys)}
    msgs, related = [], []
    for idx, r in flagged.iterrows():
        grp = by_group[(r['cardholder_id'], r['merchant_norm'])]
        start = r['tx_date'] - pd.Timedelta(days=c['window_days'])
        win = grp[(grp['tx_date'] > start) & (grp['tx_date'] <= r['tx_date'])]
        others = win[win['txn_id'] != r['txn_id']]['txn_id']
        # Số lượng và tổng tính trên cả cửa sổ (kể cả giao dịch cùng ngày ghi sau) để khớp với danh sách liên quan.
        msgs.append(f'{len(win)} giao dịch cùng người chi tại cùng cửa hàng trong {c["window_days"]} ngày, '
                    f'mỗi khoản dưới hạn mức nhưng tổng {money(win["amount"].sum())} vượt hạn mức {money(c["limit"])}.')
        related.append(','.join(others))
    return _hits(flagged, 'R04', c['severity'], pd.Series(msgs, index=flagged.index),
                 related=pd.Series(related, index=flagged.index))


def r05_budget(df, cfg, budgets: pd.DataFrame | None):
    c = cfg['R05']
    if budgets is None or budgets.empty:
        return pd.DataFrame(columns=HIT_COLUMNS)
    keys = ['agency_id', 'expense_group', 'month']
    b = budgets.assign(month=pd.PeriodIndex(budgets['month'], freq='M'))
    d = df.sort_values(['tx_date', 'txn_id']).merge(b[keys + ['budget']], on=keys, how='inner')
    cum = d.groupby(keys)['amount'].cumsum()
    prev = cum - d['amount']
    warn = c['warn_ratio'] * d['budget']
    cross_over = (prev <= d['budget']) & (cum > d['budget'])
    cross_warn = (prev < warn) & (cum >= warn) & ~cross_over
    after = (prev > d['budget']) & (d['amount'] > 0)

    parts = []
    for mask, sev, text in [
        (cross_warn, c['severity_warn'], 'Lũy kế chi {cum} đạt {pct:.0%} ngân sách tháng {budget} của nhóm chi phí này.'),
        (cross_over, c['severity_over'], 'Giao dịch làm lũy kế chi {cum} vượt ngân sách tháng {budget} ({pct:.0%}).'),
        (after, c['severity_after'], 'Phát sinh thêm khi nhóm chi phí đã vượt ngân sách tháng {budget} (lũy kế {cum}, {pct:.0%}).'),
    ]:
        rows = d[mask]
        msg = pd.Series([text.format(cum=money(x), budget=money(y), pct=x / y)
                         for x, y in zip(cum[mask], rows['budget'])], index=rows.index)
        parts.append(_hits(rows, 'R05', sev, msg))
    return pd.concat(parts, ignore_index=True)


def r06_cash(df, cfg):
    c = cfg['R06']
    rows = df[df['mcc_desc'].isin(c['mcc'])]
    msg = rows['mcc_desc'].map(lambda m: f'Giao dịch thuộc loại chuyển tiền/nạp tiền ({m}), tương đương rút tiền mặt.')
    return _hits(rows, 'R06', c['severity'], msg)


def r07_unapproved_merchant(df, cfg, approved: set | None):
    c = cfg['R07']
    if not approved:
        return pd.DataFrame(columns=HIT_COLUMNS)
    rows = df[(df['month'] >= pd.Period(c['start_month'], 'M')) & (df['amount'] >= c['min_amount'])
              & ~df['merchant_norm'].isin(approved)]
    msg = rows['merchant_norm'].map(lambda m: f'Cửa hàng "{m}" chưa có trong danh sách nhà cung cấp đã duyệt.')
    return _hits(rows, 'R07', c['severity'], msg)


def r08_restricted(df, cfg):
    c = cfg['R08']
    banned = df['mcc_desc'].str.contains(c['banned_mcc_pattern'], regex=True)
    casino = df['mcc_desc'].str.contains(c['casino_pattern'], regex=True) & ~banned
    parts = []
    rows = df[banned]
    parts.append(_hits(rows, 'R08', c['severity'], rows['mcc_desc'].map(
        lambda m: f'Loại hình cửa hàng thuộc danh mục không được phép chi: {m}.')))
    rows = df[casino]
    parts.append(_hits(rows, 'R08', c['severity_casino'], rows['mcc_desc'].map(
        lambda m: f'Chi tại cơ sở có casino ({m}); cần xác nhận là chi phí lưu trú công tác.')))
    return pd.concat(parts, ignore_index=True)


def r09_non_working_day(df, cfg):
    c = cfg['R09']
    weekend = df['tx_date'].dt.dayofweek >= 5
    holiday_name = pd.Series('', index=df.index)
    if c['include_holidays']:
        cal = USFederalHolidayCalendar()
        hol = cal.holidays(df['tx_date'].min(), df['tx_date'].max(), return_name=True)
        holiday_name = df['tx_date'].map(hol).fillna('')
    mask = weekend | (holiday_name != '')
    rows = df[mask]
    dow = ['Thứ Hai', 'Thứ Ba', 'Thứ Tư', 'Thứ Năm', 'Thứ Sáu', 'Thứ Bảy', 'Chủ Nhật']
    msg = pd.Series([f'Giao dịch ngày {t:%d/%m/%Y} ({h if h else dow[t.dayofweek]}), ngoài ngày làm việc.'
                     for t, h in zip(rows['tx_date'], holiday_name[mask])], index=rows.index)
    return _hits(rows, 'R09', c['severity'], msg)


# ---------------------------------------------------------------- chạy toàn bộ

def load_reference(ref_dir: Path = REFERENCE_DIR):
    budgets_file, approved_file = ref_dir / 'budgets.csv', ref_dir / 'approved_merchants.csv'
    budgets = pd.read_csv(budgets_file, dtype={'agency_id': str}) if budgets_file.exists() else None
    approved = set(pd.read_csv(approved_file)['merchant_norm']) if approved_file.exists() else None
    return budgets, approved


def run_rules(df: pd.DataFrame, cfg: dict | None = None, budgets=None, approved=None) -> pd.DataFrame:
    """Chạy cả 9 rule, trả về bảng vi phạm (một giao dịch có thể vi phạm nhiều rule)."""
    cfg = cfg or load_config()
    hits = [
        r01_missing_docs(df, cfg), r02_over_limit(df, cfg), r03_duplicates(df, cfg), r04_split(df, cfg),
        r05_budget(df, cfg, budgets), r06_cash(df, cfg), r07_unapproved_merchant(df, cfg, approved),
        r08_restricted(df, cfg), r09_non_working_day(df, cfg),
    ]
    hits = [h for h in hits if not h.empty]
    if not hits:
        return pd.DataFrame(columns=HIT_COLUMNS)
    return pd.concat(hits, ignore_index=True)


def risk_level(df: pd.DataFrame, hits: pd.DataFrame) -> pd.Series:
    """Mức rủi ro theo rule (bước 6 trong bản phân tích nghiệp vụ):
    Cảnh báo cao nếu có vi phạm mức Cao; Cần review nếu có mức Trung bình; còn lại Bình thường
    (vi phạm mức Thấp chỉ ghi chú). Tín hiệu từ mô hình bất thường sẽ được bổ sung sau."""
    worst = hits.assign(score=hits['severity'].map(SEVERITY_ORDER)).groupby('txn_id')['score'].max()
    score = df['txn_id'].map(worst).fillna(0)
    level = pd.Series('Bình thường', index=df.index)
    level[score >= 2] = 'Cần review'
    level[score >= 3] = 'Cảnh báo cao'
    return level

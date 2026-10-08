"""Đọc và làm sạch dữ liệu Purchase Card của Oklahoma (data/raw/pcard_YYYYMM.csv)."""
from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).resolve().parents[1] / 'data' / 'raw'

COLUMNS = {
    'CALENDAR_YEAR': 'year', 'CALENDAR_MONTH': 'month_num', 'AGENCYNBR': 'agency_id',
    'AGENCYNAME': 'agency', 'LAST_NAME': 'last_name', 'FIRST_INITIAL': 'first_initial',
    'ITEM_DESCR': 'item_desc', 'AMOUNT': 'amount', 'MERCHANT': 'merchant',
    'TRANSACTION_DATE': 'tx_date', 'POST_DATE': 'post_date', 'MCC_DESCRIPTION': 'mcc_desc',
}


def parse_amount(s: pd.Series) -> pd.Series:
    """'1,234.50' -> 1234.5 ; '$(121.52)' -> -121.52 (định dạng kế toán)."""
    s = s.str.strip()
    negative = s.str.match(r'^\$?\(.*\)$')
    value = pd.to_numeric(s.str.replace(r'[\$,\s()]', '', regex=True))
    return value.where(~negative, -value)


def normalize_merchant(s: pd.Series) -> pd.Series:
    """Bỏ số, mã chi nhánh, token ngắn; giữ 3 từ đầu. 'AMAZON MKTPL 7T94T6AY3' -> 'AMAZON MKTPL'.
    Tên chỉ gồm mã (vd 'QT 27') thì giữ nguyên dạng viết hoa."""
    norm = (s.str.upper()
             .str.replace(r'[^A-Z ]', ' ', regex=True)
             .str.replace(r'\b[A-Z]{1,2}\b', ' ', regex=True)
             .str.split().str[:3].str.join(' '))
    return norm.where(norm != '', s.str.upper())


def load_pcard(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    frames = []
    for f in sorted(Path(raw_dir).glob('pcard_*.csv')):
        d = pd.read_csv(f, dtype=str, keep_default_na=False)
        d.columns = d.columns.str.strip()
        d = d.drop(columns=['ROWID'], errors='ignore')
        # Mã giao dịch cố định: tháng của file + số thứ tự dòng, vd '202507-000123'.
        d.insert(0, 'txn_id', [f"{f.stem.split('_')[-1]}-{i:06d}" for i in range(len(d))])
        frames.append(d)
    df = pd.concat(frames, ignore_index=True).rename(columns=COLUMNS)

    for c in df.columns:
        df[c] = df[c].str.strip()
    df['amount'] = parse_amount(df['amount'])
    df['tx_date'] = pd.to_datetime(df['tx_date'], format='%d-%b-%y')
    df['post_date'] = pd.to_datetime(df['post_date'], format='%d-%b-%y')
    df['month'] = pd.PeriodIndex.from_fields(year=df['year'].astype(int), month=df['month_num'].astype(int), freq='M')
    df['cardholder_id'] = df['agency_id'] + '|' + df['last_name'] + '|' + df['first_initial']
    df['merchant_norm'] = normalize_merchant(df['merchant'])
    return df.drop(columns=['year', 'month_num'])

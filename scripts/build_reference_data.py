"""Sinh dữ liệu tham chiếu mà dữ liệu công khai không có, phục vụ rule R05 và R07:

- data/reference/budgets.csv: ngân sách tháng theo đơn vị × nhóm chi phí.
  Ngân sách = chi tiêu trung bình mỗi tháng trong giai đoạn cơ sở (07/2025–03/2026, trùng tập huấn luyện)
  × (1 + HEADROOM). Chỉ lập cho cặp đơn vị × nhóm có chi tiêu ở ít nhất MIN_ACTIVE_MONTHS tháng cơ sở.
  Áp dụng cho mọi tháng 07/2025–08/2026.
- data/reference/approved_merchants.csv: cửa hàng đã xuất hiện trong giai đoạn lập danh sách
  (07–12/2025) được coi là "đã duyệt".

Chạy: .venv/Scripts/python scripts/build_reference_data.py
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data_loader import load_pcard  # noqa: E402
from src.labels import add_expense_group  # noqa: E402
from src.rules import load_config  # noqa: E402

OUT = ROOT / 'data' / 'reference'
BASE_START, BASE_END = pd.Period('2025-07', 'M'), pd.Period('2026-03', 'M')
HEADROOM = 0.10
MIN_ACTIVE_MONTHS = 6


def main():
    df = add_expense_group(load_pcard())
    OUT.mkdir(parents=True, exist_ok=True)

    base = df[(df.month >= BASE_START) & (df.month <= BASE_END)]
    n_months = (BASE_END - BASE_START).n + 1
    monthly = base.groupby(['agency_id', 'expense_group', 'month'])['amount'].sum()
    stats = monthly.groupby(['agency_id', 'expense_group']).agg(total='sum', active_months='count').reset_index()
    stats = stats[(stats.active_months >= MIN_ACTIVE_MONTHS) & (stats.total > 0)]
    stats['budget'] = (stats.total / n_months * (1 + HEADROOM)).round(-1)

    months = pd.period_range(df.month.min(), df.month.max(), freq='M')
    budgets = stats[['agency_id', 'expense_group', 'budget']].merge(pd.DataFrame({'month': months.astype(str)}), how='cross')
    budgets[['agency_id', 'expense_group', 'month', 'budget']].to_csv(OUT / 'budgets.csv', index=False)

    start = pd.Period(load_config()['R07']['start_month'], 'M')
    approved = sorted(df[df.month < start].merchant_norm.dropna().unique())
    pd.DataFrame({'merchant_norm': approved}).to_csv(OUT / 'approved_merchants.csv', index=False)

    covered = df.set_index(['agency_id', 'expense_group']).index.isin(stats.set_index(['agency_id', 'expense_group']).index)
    print(f'Ngân sách: {len(stats)} cặp đơn vị × nhóm chi phí, {len(budgets):,} dòng; '
          f'phủ {covered.mean():.1%} giao dịch -> {OUT / "budgets.csv"}')
    print(f'Cửa hàng đã duyệt: {len(approved):,} (xuất hiện trước {start}) -> {OUT / "approved_merchants.csv"}')


if __name__ == '__main__':
    main()

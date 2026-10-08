"""Kiểm thử rule engine trên dữ liệu nhỏ tự tạo. Chạy: .venv/Scripts/python -m pytest tests -q"""
import pandas as pd
import pytest

from src.rules import load_config, risk_level, run_rules

CFG = load_config()


def make(rows):
    """rows: list of dict; các cột thiếu được điền giá trị mặc định hợp lệ (không vi phạm rule nào)."""
    base = dict(cardholder_id='A|Smith|J', merchant_norm='OFFICE DEPOT', item_desc='Printer paper',
                amount=50.0, tx_date='2026-03-03', agency_id='A', mcc_desc='STATIONERY OFFICE SUPPLIES PRINTING AND',
                expense_group='OFFICE_PRINT')  # 03/03/2026 là Thứ Ba
    df = pd.DataFrame([{**base, **r} for r in rows])
    df['txn_id'] = [f't{i}' for i in range(len(df))]
    df['tx_date'] = pd.to_datetime(df['tx_date'])
    df['month'] = df['tx_date'].dt.to_period('M')
    return df


def rule_hits(df, rule_id, **kw):
    hits = run_rules(df, CFG, **kw)
    return hits[hits.rule_id == rule_id]


def test_clean_transaction_has_no_hits():
    assert run_rules(make([{}]), CFG).empty


def test_r01_generic_description_only_for_large_amounts():
    df = make([{'item_desc': 'GENERAL PURCHASE', 'amount': 1500}, {'item_desc': 'GENERAL PURCHASE', 'amount': 200}])
    assert rule_hits(df, 'R01').txn_id.tolist() == ['t0']


def test_r02_over_limit():
    df = make([{'amount': 5000}, {'amount': 5000.01}])
    assert rule_hits(df, 'R02').txn_id.tolist() == ['t1']


def test_r03_duplicate_within_window_points_to_original():
    df = make([{'amount': 250, 'tx_date': '2026-03-02'}, {'amount': 250, 'tx_date': '2026-03-04'},
               {'amount': 250, 'tx_date': '2026-03-20'}])
    h = rule_hits(df, 'R03')
    assert h.txn_id.tolist() == ['t1']
    assert h.related_txn_ids.iloc[0] == 't0'


def test_r03_ignores_nightly_room_charges_and_small_amounts():
    df = make([{'amount': 180, 'item_desc': 'ROOM CHARGES', 'tx_date': '2026-03-02'},
               {'amount': 180, 'item_desc': 'ROOM CHARGES', 'tx_date': '2026-03-03'},
               {'amount': 20, 'tx_date': '2026-03-02'}, {'amount': 20, 'tx_date': '2026-03-03'}])
    assert rule_hits(df, 'R03').empty


def test_r03_ignores_hotels_with_generic_description():
    df = make([{'amount': 180, 'item_desc': 'GENERAL PURCHASE', 'expense_group': 'LODGING', 'tx_date': '2026-03-02'},
               {'amount': 180, 'item_desc': 'GENERAL PURCHASE', 'expense_group': 'LODGING', 'tx_date': '2026-03-03'}])
    assert rule_hits(df, 'R03').empty


def test_r04_split_purchase():
    df = make([{'amount': 3000, 'tx_date': '2026-03-02'}, {'amount': 2500, 'tx_date': '2026-03-03'},
               {'amount': 3000, 'tx_date': '2026-03-10', 'merchant_norm': 'OTHER STORE'}])
    h = rule_hits(df, 'R04')
    assert h.txn_id.tolist() == ['t1']
    assert h.related_txn_ids.iloc[0] == 't0'


def test_r04_ignores_purchases_outside_window():
    df = make([{'amount': 3000, 'tx_date': '2026-03-02'}, {'amount': 2500, 'tx_date': '2026-03-09'}])
    assert rule_hits(df, 'R04').empty


def test_r05_budget_warning_overrun_and_after():
    budgets = pd.DataFrame({'agency_id': ['A'], 'expense_group': ['OFFICE_PRINT'], 'month': ['2026-03'], 'budget': [1000.0]})
    df = make([{'amount': 500, 'tx_date': '2026-03-02'}, {'amount': 450, 'tx_date': '2026-03-03'},   # 950 → 95%
               {'amount': 100, 'tx_date': '2026-03-04'}, {'amount': 10, 'tx_date': '2026-03-05'}])  # 1050 → vượt, rồi thêm
    h = rule_hits(df, 'R05', budgets=budgets).set_index('txn_id').severity
    c = CFG['R05']
    assert h.to_dict() == {'t1': c['severity_warn'], 't2': c['severity_over'], 't3': c['severity_after']}


def test_r06_cash_like():
    df = make([{'mcc_desc': 'WIRE TRANSFER MONEY ORDERS'}])
    assert rule_hits(df, 'R06').txn_id.tolist() == ['t0']


def test_r07_unapproved_merchant_after_start_month():
    approved = {'OFFICE DEPOT'}
    df = make([{'merchant_norm': 'NEW SHOP', 'amount': 800},
               {'merchant_norm': 'NEW SHOP', 'amount': 800, 'tx_date': '2025-11-04'},  # trước tháng bắt đầu
               {'merchant_norm': 'NEW SHOP', 'amount': 100}])                           # dưới ngưỡng
    assert rule_hits(df, 'R07', approved=approved).txn_id.tolist() == ['t0']


def test_r08_banned_vs_casino_hotel():
    df = make([{'mcc_desc': 'PACKAGE STORES  BEER  LIQUOR'}, {'mcc_desc': 'CAESARS HOTEL AND CASINO'}])
    h = rule_hits(df, 'R08').set_index('txn_id').severity
    assert h.to_dict() == {'t0': 'Cao', 't1': 'Trung bình'}


def test_r09_weekend_and_federal_holiday():
    df = make([{'tx_date': '2026-03-07'},   # Thứ Bảy
               {'tx_date': '2026-05-25'},   # Memorial Day
               {'tx_date': '2026-03-04'}])  # Thứ Tư
    h = rule_hits(df, 'R09')
    assert h.txn_id.tolist() == ['t0', 't1']
    assert 'Memorial Day' in h.message.iloc[1]


@pytest.mark.parametrize('rows, expected', [
    ([{}], 'Bình thường'),
    ([{'tx_date': '2026-03-07'}], 'Bình thường'),                  # chỉ vi phạm mức Thấp
    ([{'mcc_desc': 'WIRE TRANSFER MONEY ORDERS'}], 'Cần review'),  # mức Trung bình
    ([{'amount': 9000}], 'Cảnh báo cao'),                          # mức Cao
])
def test_risk_level(rows, expected):
    df = make(rows)
    assert risk_level(df, run_rules(df, CFG)).iloc[0] == expected

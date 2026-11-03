"""Chạy toàn bộ pipeline (rule + 2 mô hình bất thường + mức rủi ro) và nạp vào cơ sở dữ liệu của ứng dụng.

Hồ sơ cảnh báo: mỗi cụm mua dồn (người chi × ngày bị mô hình cấp ngày gắn cờ) là MỘT hồ sơ; các giao dịch
Cần review / Cảnh báo cao còn lại mỗi giao dịch một hồ sơ.

  .venv/Scripts/python scripts/load_db.py            # từ chối nếu đã có dữ liệu
  .venv/Scripts/python scripts/load_db.py --reset    # XÓA toàn bộ (kể cả kết luận của kế toán) rồi nạp lại
"""
import argparse
import sys
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import func, insert, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.db import Base, engine  # noqa: E402
from app.models import Case, RuleHit, Transaction  # noqa: E402
from src.case import CaseEngine  # noqa: E402

LEVEL_RANK = {'Bình thường': 0, 'Cần review': 1, 'Cảnh báo cao': 2}
RANK_LEVEL = {v: k for k, v in LEVEL_RANK.items()}


def build_tables(eng: CaseEngine):
    df = eng.df.copy()
    df['anomaly_score'] = eng.score.reindex(df.index)
    df['anomaly_tx_flag'] = eng.tx_flag.reindex(df.index, fill_value=False)
    hits = eng.hits
    serious = hits[hits.severity != 'Thấp'].groupby('txn_id').rule_id.agg(lambda s: ','.join(sorted(set(s))))
    df['rules'] = df.txn_id.map(serious).fillna('')

    # hồ sơ cụm mua dồn
    b = df[df.burst_day_flag]
    burst = (b.groupby(['cardholder_id', 'tx_date'])
              .agg(agency=('agency', 'first'), month=('month', 'first'), n_txn=('txn_id', 'size'),
                   total_amount=('amount', 'sum'), n_merch=('merchant_norm', 'nunique'),
                   level=('final_level', lambda s: RANK_LEVEL[max(LEVEL_RANK[x] for x in s)]),
                   rules=('rules', lambda s: ','.join(sorted({r for x in s for r in x.split(',') if r}))))
              .reset_index())
    burst['id'] = 'B|' + burst.cardholder_id + '|' + burst.tx_date.dt.strftime('%Y-%m-%d')
    burst['kind'] = 'cum_mua_don'
    burst['has_anomaly'] = True
    burst['title'] = burst.apply(lambda r: f'Mua dồn: {r.n_txn} giao dịch tại {r.n_merch} cửa hàng trong ngày', axis=1)
    df.loc[df.burst_day_flag, 'case_id'] = ('B|' + b.cardholder_id + '|' + b.tx_date.dt.strftime('%Y-%m-%d'))

    # hồ sơ từng giao dịch
    single = df[(df.final_level != 'Bình thường') & ~df.burst_day_flag].copy()
    single['id'] = 'T|' + single.txn_id
    single = single.assign(kind='giao_dich', n_txn=1, total_amount=single.amount, level=single.final_level,
                           has_anomaly=single.anomaly_tx_flag,
                           title=single.merchant + ' · ' + single.item_desc.str.slice(0, 60))
    df.loc[single.index, 'case_id'] = single['id']

    cols = ['id', 'kind', 'cardholder_id', 'agency', 'tx_date', 'month', 'n_txn', 'total_amount', 'level', 'rules',
            'has_anomaly', 'title']
    cases = pd.concat([burst[cols], single[cols]], ignore_index=True)
    cases['month'] = cases['month'].astype(str)
    return df, hits, cases


def records(frame: pd.DataFrame, cols: list[str]) -> list[dict]:
    out = frame[cols].copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.date
    out = out.astype(object).where(out.notna(), None)
    return out.to_dict('records')


def bulk(conn, table, rows, chunk=20_000):
    for i in range(0, len(rows), chunk):
        conn.execute(insert(table), rows[i:i + chunk])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--reset', action='store_true', help='xóa toàn bộ dữ liệu ứng dụng (kể cả kết luận) rồi nạp lại')
    a = ap.parse_args()

    Base.metadata.create_all(engine)
    with engine.connect() as c:
        n = c.execute(select(func.count()).select_from(Case)).scalar()
    if n and not a.reset:
        sys.exit(f'Cơ sở dữ liệu đã có {n:,} hồ sơ. Thêm --reset nếu thật sự muốn xóa và nạp lại.')
    if a.reset:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)

    t0 = time.time()
    eng = CaseEngine()
    df, hits, cases = build_tables(eng)
    df['month'] = df['month'].astype(str)
    print(f'Pipeline: {time.time() - t0:.0f} giây | {len(df):,} giao dịch, {len(hits):,} vi phạm, {len(cases):,} hồ sơ')

    t0 = time.time()
    tx_cols = ['txn_id', 'month', 'tx_date', 'post_date', 'agency_id', 'agency', 'cardholder_id', 'merchant',
               'merchant_norm', 'item_desc', 'amount', 'mcc_desc', 'expense_group', 'rule_level', 'anomaly_score',
               'anomaly_tx_flag', 'burst_day_flag', 'final_level', 'case_id']
    with engine.begin() as conn:
        bulk(conn, Case.__table__, records(cases, list(cases.columns)))
        bulk(conn, Transaction.__table__, records(df, tx_cols))
        bulk(conn, RuleHit.__table__, records(hits, ['txn_id', 'rule_id', 'severity', 'message', 'related_txn_ids']))
    print(f'Ghi vào {engine.url.render_as_string(hide_password=True)}: {time.time() - t0:.0f} giây')
    print(cases.groupby(['kind', 'level']).size().to_string())


if __name__ == '__main__':
    main()

"""Kiểm thử đặc trưng phát hiện bất thường: đúng công thức và không dùng thông tin tương lai."""
import numpy as np
import pandas as pd

from src.anomaly import MIN_HISTORY, build_features


def make(rows):
    base = dict(cardholder_id='A|Smith|J', merchant_norm='OFFICE DEPOT', expense_group='OFFICE_PRINT',
                agency_id='A', amount=100.0, tx_date='2026-03-02', post_date=None)
    df = pd.DataFrame([{**base, **r} for r in rows])
    df['txn_id'] = [f't{i:03d}' for i in range(len(df))]
    df['tx_date'] = pd.to_datetime(df['tx_date'])
    df['post_date'] = pd.to_datetime(df['post_date']).fillna(df['tx_date'] + pd.Timedelta(days=1))
    return df


def history(n, amount=100.0, start='2026-01-01'):
    return [{'amount': amount, 'tx_date': d} for d in pd.date_range(start, periods=n, freq='2D').strftime('%Y-%m-%d')]


def test_no_future_leakage():
    df = make(history(10) + [{'amount': 120, 'tx_date': '2026-03-01'}])
    later = pd.concat([df, make([{'amount': 99999, 'tx_date': '2026-03-05'}]).assign(txn_id='t999')], ignore_index=True)
    a, b = build_features(df), build_features(later)
    pd.testing.assert_frame_equal(a.loc[df.index], b.loc[df.index])


def test_spike_has_high_cardholder_deviation_only_after_enough_history():
    short = build_features(make(history(MIN_HISTORY - 1) + [{'amount': 5000, 'tx_date': '2026-03-01'}]))
    assert short['z_cardholder'].iloc[-1] == 0                       # chưa đủ lịch sử
    long = build_features(make(history(10) + [{'amount': 5000, 'tx_date': '2026-03-01'}]))
    assert long['z_cardholder'].iloc[-1] > 5
    assert np.isclose(10 ** (long['log_amount'].iloc[-1] - long['mean_cardholder'].iloc[-1]), 50)


def test_lower_than_usual_amount_is_not_anomalous():
    f = build_features(make(history(10) + [{'amount': 1, 'tx_date': '2026-03-01'}]))
    assert f['z_cardholder'].iloc[-1] == 0


def test_new_merchant_and_group_flags():
    f = build_features(make(history(5) + [{'merchant_norm': 'NEW SHOP', 'expense_group': 'FOOD', 'tx_date': '2026-03-01'}]))
    assert f['new_cardholder_merchant'].tolist() == [1, 0, 0, 0, 0, 1]
    assert f['new_cardholder_group'].iloc[-1] == 1


def test_burst_ratio_grows_within_the_day():
    rows = history(10) + [{'tx_date': '2026-03-01', 'merchant_norm': f'S{i}'} for i in range(5)]
    f = build_features(make(rows))
    burst = f['burst_ratio'].iloc[-5:].tolist()
    assert burst == sorted(burst) and burst[-1] == 5


def test_refunds_are_excluded():
    f = build_features(make(history(5) + [{'amount': -50, 'tx_date': '2026-03-01'}]))
    assert len(f) == 5


def _with_month(df):
    return df.assign(month=df['tx_date'].dt.to_period('M'))


def test_day_features_flag_burst_relative_to_own_rhythm():
    from src.anomaly import build_day_features
    rows = history(10) + [{'tx_date': '2026-03-01', 'merchant_norm': f'S{i}', 'amount': 50} for i in range(6)]
    day = build_day_features(_with_month(make(rows)))
    last = day.iloc[-1]
    assert last['n'] == 6 and last['n_merch'] == 6 and last['mean_n'] == 1
    assert last['rate_ratio'] == 6 and last['poisson_surprise'] > 5
    assert (day.iloc[:-1]['rate_ratio'] <= 1).all()


def test_day_features_no_future_leakage():
    from src.anomaly import build_day_features
    df = _with_month(make(history(10)))
    later = _with_month(pd.concat([make(history(10)), make([{'tx_date': '2026-04-01', 'amount': 1}] * 9)], ignore_index=True))
    later['txn_id'] = [f'x{i}' for i in range(len(later))]
    a, b = build_day_features(df), build_day_features(later)
    pd.testing.assert_frame_equal(a, b.iloc[:len(a)])


def test_day_flags_map_back_to_all_transactions_of_the_day():
    from src.anomaly import build_day_features, day_flags_to_transactions
    df = _with_month(make(history(6) + [{'tx_date': '2026-03-01', 'merchant_norm': f'S{i}'} for i in range(3)]
                          + [{'tx_date': '2026-03-01', 'amount': -20}]))
    day = build_day_features(df)
    flagged = day['tx_date'].eq(pd.Timestamp('2026-03-01'))
    tx = day_flags_to_transactions(df, day, flagged)
    assert tx.tolist() == [False] * 6 + [True] * 3 + [False]   # hoàn tiền không bị gắn cờ

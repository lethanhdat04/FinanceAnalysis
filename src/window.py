"""Thống kê theo cửa sổ thời gian trong từng nhóm, tính chính xác bằng tìm kiếm nhị phân.

Không dùng groupby().rolling() rồi ghép theo vị trí: với nhiều khóa, pandas trả kết quả theo thứ tự nhóm
khác thứ tự dòng đầu vào, làm lệch kết quả (lỗi đã gặp ở rule R04).
"""
import numpy as np
import pandas as pd

_DAY_SPAN = 10 ** 6   # lớn hơn mọi khoảng ngày trong dữ liệu, để khóa (nhóm, ngày) tăng dần


def window_stats(d: pd.DataFrame, keys: list[str], days: int, value: str = 'amount') -> pd.DataFrame:
    """Với mỗi dòng: số dòng và tổng `value` của các dòng cùng nhóm có ngày trong (ngày − days, ngày],
    tính tới và gồm chính dòng đó theo thứ tự sắp xếp (tx_date, txn_id).

    Trả về DataFrame (count, total) cùng index với d.
    """
    s = d.sort_values(keys + ['tx_date', 'txn_id'])
    group = s.groupby(keys, sort=False, dropna=False).ngroup().to_numpy(dtype=np.int64)
    day = s['tx_date'].to_numpy(dtype='datetime64[D]').astype(np.int64)
    key = group * _DAY_SPAN + day
    if len(key) > 1 and not (np.diff(key) >= 0).all():
        raise AssertionError('Khóa (nhóm, ngày) không tăng dần')
    lo = np.searchsorted(key, key - days + 1, side='left')     # dòng đầu tiên cùng nhóm có ngày > ngày − days
    pos = np.arange(len(s))
    cs = np.concatenate([[0.0], np.cumsum(s[value].to_numpy(dtype=float))])
    out = pd.DataFrame({'count': pos - lo + 1, 'total': cs[pos + 1] - cs[lo]}, index=s.index)
    return out.reindex(d.index)

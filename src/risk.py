"""Mức rủi ro cuối cùng (bước 6 trong bản phân tích nghiệp vụ): ghép kết quả rule và mô hình bất thường."""
import pandas as pd

LEVELS = ['Bình thường', 'Cần review', 'Cảnh báo cao']


def combine_risk(rule_level: pd.Series, anomaly_flag: pd.Series) -> pd.Series:
    """Lấy mức cao hơn giữa rule và mô hình bất thường. Mô hình bất thường không có nhãn nên chỉ nâng
    tới "Cần review"; "Cảnh báo cao" chỉ đến từ vi phạm rule mức Cao (điều kiện chắc chắn)."""
    rank = rule_level.map({lv: i for i, lv in enumerate(LEVELS)})
    rank = rank.where(~anomaly_flag.reindex(rule_level.index, fill_value=False).astype(bool), rank.clip(lower=1))
    return rank.map(dict(enumerate(LEVELS)))

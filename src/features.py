"""Đặc trưng và pipeline cho mô hình phân loại nhóm chi phí."""
import re

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import KBinsDiscretizer, OneHotEncoder

GENERIC_DESC = {'GENERAL PURCHASE', 'ORDER SUMMARY ITEM', ''}

# Đuôi đơn vị tính ở cuối mỗi món (vd 'Dell 27 Monitor PCE'). Amazon luôn ghi 'PCE', còn trong dữ liệu
# huấn luyện 'EA', 'PCE' gắn với vài nhà cung cấp cụ thể -> mô hình học nhầm theo đuôi thay vì tên hàng.
UNIT_SUFFIX = re.compile(r'\s+(PCE|PCS|EA|EAC|EACH|ECH|NMB|NBR|ITM|ITEM|ST|CS|E|PK|ETY|BX|DZ|CT|CCT|S|P|CL|C|U|1)$', re.I)


def clean_desc(s: pd.Series) -> pd.Series:
    """Tách các món trong đơn (ngăn bởi '|') và bỏ đuôi đơn vị tính của từng món."""
    return s.map(lambda t: ' ; '.join(UNIT_SUFFIX.sub('', x.strip()) for x in t.split('|')))

# Chia theo tháng hạch toán: học quá khứ, đoán tương lai.
TRAIN_END = pd.Period('2026-03', 'M')
VALID_END = pd.Period('2026-05', 'M')


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out['informative'] = ~out['item_desc'].str.upper().isin(GENERIC_DESC)
    out['text_full'] = out['item_desc'] + ' | ' + out['merchant_norm']
    out['text_desc'] = out['item_desc']
    out['text_desc_clean'] = clean_desc(out['item_desc'])
    out['log_amount'] = np.log10(out['amount'].clip(lower=0.01))
    return out


def split(df: pd.DataFrame):
    train = df[df['month'] <= TRAIN_END]
    valid = df[(df['month'] > TRAIN_END) & (df['month'] <= VALID_END)]
    test = df[df['month'] > VALID_END]
    return train, valid, test


def make_model(text_col: str, with_context: bool = True, C: float = 4.0):
    """TF-IDF ký tự (3–5) trên cột văn bản; tùy chọn thêm đơn vị (one-hot) và số tiền (10 khoảng log)."""
    tfidf = TfidfVectorizer(analyzer='char_wb', ngram_range=(3, 5), min_df=3, sublinear_tf=True, max_features=300_000)
    parts = [('text', tfidf, text_col)]
    if with_context:
        parts += [
            ('agency', OneHotEncoder(handle_unknown='ignore', min_frequency=20), ['agency_id']),
            ('amount', KBinsDiscretizer(n_bins=10, encode='onehot', strategy='quantile'), ['log_amount']),
        ]
    return make_pipeline(ColumnTransformer(parts), LogisticRegression(max_iter=1000, C=C))


def input_columns(text_col: str, with_context: bool = True) -> list[str]:
    return [text_col, 'agency_id', 'log_amount'] if with_context else [text_col]

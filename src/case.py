"""Hồ sơ giao dịch: gom mọi kết quả xử lý bằng code (phân loại, rule, mô hình bất thường, ngân sách) cho một giao dịch.

Hồ sơ là đầu vào có cấu trúc cho LLM. Mọi kết luận (vi phạm, mức rủi ro) đã được tính sẵn ở đây;
LLM chỉ diễn giải, không tính lại.
"""
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from src.anomaly import FEATURES, anomaly_score, build_day_features, build_features, describe_day, explain
from src.data_loader import load_pcard
from src.features import prepare
from src.labels import GROUPS, UNLABELED, add_expense_group
from src.risk import combine_risk
from src.rules import RULE_NAMES, load_reference, risk_level, run_rules

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / 'models'
AMAZON = r'AMAZON|AMZN'

# Đặc trưng bất thường -> loại tín hiệu (dùng để truy xuất điều quy chế liên quan)
SIGNAL_OF = {
    'z_cardholder': 'amount', 'z_merchant': 'amount', 'z_agency_group': 'amount', 'log_amount': 'amount',
    'new_cardholder_merchant': 'new_merchant', 'new_cardholder_group': 'new_group',
    'burst_ratio': 'burst', 'log_post_lag': 'late_post',
}


def _money(x):
    return f'${x:,.2f}'


class CaseEngine:
    """Nạp dữ liệu và mô hình một lần (~30 giây), sau đó dựng hồ sơ cho từng giao dịch."""

    def __init__(self, df: pd.DataFrame | None = None):
        import shap

        self.df = add_expense_group(load_pcard()) if df is None else df
        self.by_id = pd.Series(self.df.index, index=self.df['txn_id'])
        self.budgets, approved = load_reference()
        self.hits = run_rules(self.df, budgets=self.budgets, approved=approved)
        self.hits_by_txn = self.hits.groupby('txn_id')

        tx_model = joblib.load(MODELS / 'anomaly_iforest.joblib')
        day_model = joblib.load(MODELS / 'anomaly_day_iforest.joblib')
        self.X = build_features(self.df)
        self.score = anomaly_score(tx_model['model'], self.X)
        self.tx_flag = (self.score >= tx_model['threshold']).reindex(self.df.index, fill_value=False)
        self.day = build_day_features(self.df)
        self.day_flag = anomaly_score(day_model['model'], self.day) >= day_model['threshold']
        self.day_index = pd.Series(self.day.index, index=pd.MultiIndex.from_frame(self.day[['cardholder_id', 'tx_date']]))
        self.explainer = shap.TreeExplainer(tx_model['model'])

        self.df['rule_level'] = risk_level(self.df, self.hits)
        burst = self.df.set_index(['cardholder_id', 'tx_date']).index.map(
            lambda k: bool(self.day_flag.get(self.day_index.get(k, -1), False)))
        self.df['burst_day_flag'] = np.asarray(burst, dtype=bool) & (self.df['amount'] > 0)
        self.df['anomaly_flag'] = self.tx_flag | self.df['burst_day_flag']
        self.df['final_level'] = combine_risk(self.df['rule_level'], self.df['anomaly_flag'])

        self.clf_full = joblib.load(MODELS / 'expense_clf_full.joblib')
        self.clf_desc = joblib.load(MODELS / 'expense_clf_desc.joblib')

    # ------------------------------------------------------------ các phần của hồ sơ
    def row(self, txn_id: str) -> pd.Series:
        if txn_id not in self.by_id.index:
            raise KeyError(f'Không tìm thấy giao dịch {txn_id}')
        return self.df.loc[self.by_id[txn_id]]

    def classify(self, r: pd.Series) -> dict:
        """Nhóm chi phí: theo MCC nếu xác định được; nếu không (Amazon, cửa hàng bán đủ loại) thì dùng mô hình."""
        if r['expense_group'] != UNLABELED:
            return {'group': r['expense_group'], 'source': 'mcc', 'confidence': 1.0}
        x = prepare(self.df.loc[[r.name]])
        bundle = self.clf_desc if pd.Series(r['merchant']).str.contains(AMAZON, case=False).iloc[0] else self.clf_full
        proba = bundle['model'].predict_proba(x[bundle['columns']])[0]
        i = int(np.argmax(proba))
        return {'group': bundle['model'].classes_[i], 'source': 'model', 'confidence': round(float(proba[i]), 3)}

    def anomaly(self, r: pd.Series) -> dict:
        idx = r.name
        out = {'score': None, 'flag': False, 'reasons': [], 'signals': [], 'burst_day': None}
        if idx in self.X.index:
            x = self.X.loc[idx]
            out['score'] = round(float(self.score[idx]), 4)
            out['flag'] = bool(self.tx_flag[idx])
            if out['flag']:
                contrib = -pd.Series(self.explainer.shap_values(x[FEATURES].to_frame().T.astype(float))[0], index=FEATURES)
                out['reasons'] = explain(x, contrib)
                out['signals'] = sorted({SIGNAL_OF[f] for f in contrib.sort_values(ascending=False).index[:3]
                                         if contrib[f] > 0 and f in SIGNAL_OF})
        if r['burst_day_flag']:
            d = self.day.loc[self.day_index[(r['cardholder_id'], r['tx_date'])]]
            same_day = self.df[(self.df.cardholder_id == r['cardholder_id']) & (self.df.tx_date == r['tx_date'])]
            out['burst_day'] = {'description': describe_day(d), 'txn_ids': same_day['txn_id'].tolist()}
            out['signals'] = sorted(set(out['signals']) | {'burst'})
        return out

    def budget(self, agency_id: str, group: str, month) -> dict | None:
        month = pd.Period(month, 'M')
        b = self.budgets[(self.budgets.agency_id == agency_id) & (self.budgets.expense_group == group)
                         & (self.budgets.month == str(month))]
        if b.empty:
            return None
        spent = self.df[(self.df.agency_id == agency_id) & (self.df.expense_group == group) & (self.df.month == month)]
        budget, total = float(b.budget.iloc[0]), float(spent.amount.sum())
        return {'agency_id': agency_id, 'expense_group': group, 'group_name': GROUPS.get(group, group),
                'month': str(month), 'budget': budget, 'spent': round(total, 2), 'ratio': round(total / budget, 3)}

    def cardholder_history(self, txn_id: str, limit: int = 10) -> dict:
        r = self.row(txn_id)
        h = self.df[(self.df.cardholder_id == r['cardholder_id']) & (self.df.tx_date < r['tx_date']) & (self.df.amount > 0)]
        recent = h.sort_values('tx_date').tail(limit)
        return {
            'cardholder_id': r['cardholder_id'], 'n_transactions_before': int(len(h)),
            'median_amount': round(float(h.amount.median()), 2) if len(h) else None,
            'max_amount': round(float(h.amount.max()), 2) if len(h) else None,
            'top_merchants': h.merchant_norm.value_counts().head(5).to_dict(),
            'groups': {GROUPS.get(k, k): int(v) for k, v in h.expense_group.value_counts().head(5).items()},
            'previous_at_same_merchant': int((h.merchant_norm == r['merchant_norm']).sum()),
            'recent': [{'txn_id': t.txn_id, 'date': f'{t.tx_date:%d/%m/%Y}', 'merchant': t.merchant,
                        'item': t.item_desc, 'amount': t.amount} for t in recent.itertuples()],
        }

    # ------------------------------------------------------------ hồ sơ đầy đủ
    def case(self, txn_id: str) -> dict:
        r = self.row(txn_id)
        cls = self.classify(r)
        hits = self.hits_by_txn.get_group(txn_id) if txn_id in self.hits_by_txn.groups else self.hits.iloc[:0]
        anom = self.anomaly(r)
        return {
            'txn_id': txn_id,
            'transaction': {
                'date': f"{r['tx_date']:%d/%m/%Y}", 'post_date': f"{r['post_date']:%d/%m/%Y}",
                'agency': r['agency'], 'agency_id': r['agency_id'], 'cardholder': r['cardholder_id'],
                'merchant': r['merchant'], 'item_desc': r['item_desc'], 'amount': float(r['amount']),
                'mcc_desc': r['mcc_desc'],
            },
            'expense_group': {**cls, 'name': GROUPS.get(cls['group'], cls['group'])},
            'rule_hits': [{'rule_id': h.rule_id, 'rule_name': RULE_NAMES[h.rule_id], 'severity': h.severity,
                           'message': h.message, 'related_txn_ids': h.related_txn_ids or ''} for h in hits.itertuples()],
            'anomaly': anom,
            'budget': self.budget(r['agency_id'], cls['group'], r['month']),
            'risk': {'rule_level': r['rule_level'], 'final_level': r['final_level']},
        }

    def retrieval_query(self, case: dict) -> str:
        parts = [case['expense_group']['name'], case['transaction']['item_desc']]
        parts += [h['rule_name'] + ': ' + h['message'] for h in case['rule_hits']]
        parts += case['anomaly']['reasons']
        if case['anomaly']['burst_day']:
            parts.append(case['anomaly']['burst_day']['description'])
        return '\n'.join(parts)

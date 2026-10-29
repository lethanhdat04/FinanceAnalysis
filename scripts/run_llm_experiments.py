"""Thí nghiệm LLM (Groq). Kết quả ghi JSONL vào data/processed/llm_runs/, chạy tiếp được nếu bị ngắt.

  explain    : sinh giải thích có trích dẫn cho mẫu hồ sơ (rule + bất thường + RAG + LLM)
  rulecheck  : LLM tự xét 9 rule theo ĐÚNG đặc tả code dùng, so với rule engine

Ví dụ:
  .venv/Scripts/python scripts/run_llm_experiments.py explain
  .venv/Scripts/python scripts/run_llm_experiments.py rulecheck --model openai/gpt-oss-20b
"""
import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd
from groq import BadRequestError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.llm import MODEL_EXPLAIN, chat, explain_case, get_client  # noqa: E402
from src.rules import load_config  # noqa: E402

OUT = ROOT / 'data' / 'processed' / 'llm_runs'
SEED = 11
RULE_IDS = [f'R0{i}' for i in range(1, 10)]


def done_ids(path: Path, key='txn_id') -> set:
    if not path.exists():
        return set()
    return {json.loads(l)[key] for l in path.read_text(encoding='utf-8').splitlines() if l.strip()}


def append(path: Path, rec: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + '\n')


def stable_sample(path: Path, build):
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    s = build()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding='utf-8')
    return s


# ---------------------------------------------------------------- explain
def run_explain(n_per_kind: int, model: str, tag: str = ''):
    from src.case import CaseEngine
    from src.rag import PolicyRetriever

    eng, ret = CaseEngine(), PolicyRetriever()
    df = eng.df
    test = df.month >= pd.Period('2026-06', 'M')
    high_rule = eng.hits[eng.hits.severity == 'Cao'].txn_id.unique()

    def build():
        pick = lambda mask, kind, k: [{'txn_id': t, 'kind': kind} for t in
                                      df[mask & test].txn_id.sample(k, random_state=SEED).tolist()]
        amazon = df.merchant.str.contains('AMAZON', case=False) & (df.mcc_desc == 'BOOK STORES')
        return (pick(df.txn_id.isin(high_rule), 'Cảnh báo cao (rule)', n_per_kind + 1)
                + pick(eng.tx_flag & (df.rule_level == 'Bình thường'), 'Chỉ mô hình bất thường', n_per_kind)
                + pick(df.burst_day_flag & ~eng.tx_flag & (df.rule_level == 'Bình thường'), 'Cụm mua dồn', n_per_kind - 1)
                + pick((df.rule_level == 'Cần review') & ~df.anomaly_flag, 'Cần review (rule)', 2)
                + pick(amazon & (df.final_level == 'Bình thường'), 'Amazon – bình thường', 2))

    sample = stable_sample(OUT / 'samples_explain.json', build)
    path = OUT / f'explain_{model.split("/")[-1]}{tag}.jsonl'
    todo = [s for s in sample if s['txn_id'] not in done_ids(path)]
    print(f'explain: {len(sample)} hồ sơ, còn {len(todo)}')
    client = get_client()
    for s in todo:
        case = eng.case(s['txn_id'])
        chunks = ret.retrieve_for_case(eng.retrieval_query(case), rule_ids=[h['rule_id'] for h in case['rule_hits']],
                                       signals=case['anomaly']['signals'], groups=[case['expense_group']['group']])
        out = explain_case(case, chunks, client=client, model=model)
        append(path, {**out, 'kind': s['kind'], 'case': case})
        print(f"  {s['txn_id']} {s['kind']}: {out['latency_s']}s, {len(out['explanation']['citations'])} trích dẫn, {len(out['warnings'])} cảnh báo")


# ---------------------------------------------------------------- rulecheck
def rule_spec(cfg: dict) -> str:
    c = cfg
    return f"""Xét giao dịch HIỆN TẠI theo 9 quy tắc sau (điều kiện chính xác, không suy diễn thêm):
R01: mô tả mặt hàng là "GENERAL PURCHASE" hoặc "Order Summary item" hoặc trống (không phân biệt hoa thường) VÀ số tiền ≥ {c['R01']['min_amount']}.
R02: số tiền > {c['R02']['limit']}.
R03: trong LỊCH SỬ có giao dịch cùng cửa hàng (merchant_norm) và CÙNG số tiền, cách ngày giao dịch hiện tại không quá {c['R03']['window_days']} ngày;
     chỉ xét khi số tiền ≥ {c['R03']['min_amount']}, mô tả không phải {c['R03']['exclude_item_desc']} và nhóm chi phí không thuộc {c['R03']['exclude_groups']}.
R04: số tiền hiện tại trong [{c['R04']['min_each']}, {c['R04']['limit']}) VÀ xét các giao dịch cùng cửa hàng có ngày d với ngày_hiện_tại − {c['R04']['window_days']} < d ≤ ngày_hiện_tại
     (gồm cả giao dịch hiện tại và các giao dịch trong LỊCH SỬ) mà số tiền cũng trong [{c['R04']['min_each']}, {c['R04']['limit']}): có ít nhất 2 giao dịch và tổng ≥ {c['R04']['limit']}.
R05: với ngân sách tháng B và số đã chi trong tháng TRƯỚC giao dịch này S, số tiền x: vi phạm nếu (S < {c['R05']['warn_ratio']}·B ≤ S + x) hoặc (S ≤ B < S + x) hoặc (S > B và x > 0). Không có ngân sách thì không xét.
R06: mcc_desc thuộc {c['R06']['mcc']}.
R07: cửa hàng KHÔNG có trong danh sách đã duyệt VÀ số tiền ≥ {c['R07']['min_amount']}.
R08: mcc_desc chứa một trong các cụm: {c['R08']['banned_mcc_pattern'].split('|')} hoặc "{c['R08']['casino_pattern']}".
R09: ngày giao dịch là thứ Bảy, Chủ nhật hoặc ngày lễ liên bang Mỹ.
Trả về danh sách mã quy tắc bị vi phạm (có thể rỗng)."""


def run_rulecheck(model: str, per_rule: int, n_clean: int, effort: str):
    from src.data_loader import load_pcard
    from src.labels import add_expense_group
    from src.rules import load_reference, run_rules

    cfg = load_config()
    df = add_expense_group(load_pcard())
    budgets, approved = load_reference()
    hits = run_rules(df, cfg, budgets=budgets, approved=approved)
    truth = hits.groupby('txn_id').rule_id.agg(lambda s: sorted(set(s)))

    d = df.sort_values(['tx_date', 'txn_id']).copy()
    keys = ['agency_id', 'expense_group', 'month']
    d['spent_before'] = d.groupby(keys)['amount'].cumsum() - d['amount']
    b = budgets.assign(month=pd.PeriodIndex(budgets['month'], freq='M'))
    d = d.merge(b[keys + ['budget']], on=keys, how='left').set_index('txn_id', drop=False)
    pool = d[(d.month >= pd.Period('2026-01', 'M')) & (d.amount > 0)]

    def build():
        ids = []
        for r in RULE_IDS:
            cand = pool[pool.txn_id.isin(truth[truth.map(lambda x: r in x)].index) & ~pool.txn_id.isin(ids)]
            ids += cand.txn_id.sample(min(per_rule, len(cand)), random_state=SEED).tolist()
        clean = pool[~pool.txn_id.isin(truth.index)]
        ids += clean.txn_id.sample(n_clean, random_state=SEED).tolist()
        return ids

    sample = stable_sample(OUT / 'samples_rulecheck.json', build)
    path = OUT / f'rulecheck_{model.split("/")[-1]}.jsonl'
    todo = [t for t in sample if t not in done_ids(path)]
    print(f'rulecheck {model}: {len(sample)} giao dịch, còn {len(todo)}')
    schema = {'type': 'object', 'properties': {'violations': {'type': 'array', 'items': {'type': 'string', 'enum': RULE_IDS}}},
              'required': ['violations'], 'additionalProperties': False}
    system = 'Bạn là bộ kiểm tra quy tắc chi tiêu. Áp dụng chính xác các điều kiện được cho. ' + rule_spec(cfg)
    client = get_client()
    for t in todo:
        r = d.loc[t]
        hist = d[(d.cardholder_id == r.cardholder_id) & (d.merchant_norm == r.merchant_norm) & (d.amount > 0)
                 & ((d.tx_date < r.tx_date) | ((d.tx_date == r.tx_date) & (d.txn_id < t)))
                 & (d.tx_date >= r.tx_date - pd.Timedelta(days=7))].tail(20)
        payload = {
            'giao_dich_hien_tai': {'txn_id': t, 'ngay': f'{r.tx_date:%Y-%m-%d}', 'so_tien': float(r.amount),
                                   'cua_hang': r.merchant, 'merchant_norm': r.merchant_norm, 'mo_ta': r.item_desc,
                                   'mcc_desc': r.mcc_desc, 'nhom_chi_phi': r.expense_group},
            'lich_su_cung_cua_hang_7_ngay': [{'txn_id': h.txn_id, 'ngay': f'{h.tx_date:%Y-%m-%d}', 'so_tien': float(h.amount),
                                             'mo_ta': h.item_desc} for h in hist.itertuples()],
            'ngan_sach_thang': None if pd.isna(r.budget) else float(r.budget),
            'da_chi_trong_thang_truoc_giao_dich': round(float(r.spent_before), 2),
            'cua_hang_trong_danh_sach_da_duyet': bool(r.merchant_norm in approved),
        }
        t0, pred, err, u = time.time(), None, None, None
        for _ in range(3):                      # model đôi khi sinh đầu ra không khớp schema (json_validate_failed)
            try:
                resp = chat(client, model=model, temperature=0, reasoning_effort=effort,
                            messages=[{'role': 'system', 'content': system},
                                      {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
                            response_format={'type': 'json_schema', 'json_schema': {'name': 'rulecheck', 'strict': True, 'schema': schema}})
                pred, u, err = sorted(set(json.loads(resp.choices[0].message.content)['violations'])), resp.usage, None
                break
            except BadRequestError as e:
                err = str(e)[:300]
        append(path, {'txn_id': t, 'truth': truth.get(t, []), 'pred': pred if pred is not None else [], 'invalid_output': pred is None,
                      'error': err, 'latency_s': round(time.time() - t0, 2),
                      'prompt_tokens': getattr(u, 'prompt_tokens', 0), 'completion_tokens': getattr(u, 'completion_tokens', 0),
                      'payload': payload})
        mark = 'LỖI' if pred is None else ('OK ' if pred == truth.get(t, []) else 'SAI')
        print(f'  {mark} {t}: đúng={truth.get(t, [])} LLM={pred}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('exp', choices=['explain', 'rulecheck'])
    ap.add_argument('--model', default=None)
    ap.add_argument('--n', type=int, default=4, help='explain: số hồ sơ mỗi loại')
    ap.add_argument('--per-rule', type=int, default=4)
    ap.add_argument('--clean', type=int, default=8)
    ap.add_argument('--effort', default='medium')
    ap.add_argument('--tag', default='', help='hậu tố tên file kết quả, vd _v2')
    a = ap.parse_args()
    if a.exp == 'explain':
        run_explain(a.n, a.model or MODEL_EXPLAIN, a.tag)
    else:
        run_rulecheck(a.model or 'openai/gpt-oss-20b', a.per_rule, a.clean, a.effort)

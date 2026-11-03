"""Kiểm thử agent điều tra với client giả lập (không gọi API)."""
import json
from types import SimpleNamespace as NS

import pandas as pd

from src.agent import InvestigationAgent
from src.rag import PolicyRetriever, parse_policy

RETRIEVER = PolicyRetriever(parse_policy(), embed_model=None)
D05_QUOTE = 'Nghiêm cấm việc chia một khoản mua sắm thành nhiều giao dịch nhỏ hơn hạn mức để tránh phê duyệt.'


class FakeEngine:
    def __init__(self):
        self.df = pd.DataFrame({
            'txn_id': ['t1', 't2', 't9'], 'cardholder_id': ['A|X|Y'] * 3, 'merchant_norm': ['SHOP'] * 3,
            'merchant': ['SHOP'] * 3, 'item_desc': ['Ghế', 'Bàn', 'Tủ'], 'amount': [3000.0, 2500.0, 40.0],
            'tx_date': pd.to_datetime(['2026-03-02', '2026-03-03', '2026-02-15']), 'agency_id': ['A'] * 3,
            'expense_group': ['FACILITIES_MRO'] * 3, 'month': pd.PeriodIndex(['2026-03', '2026-03', '2026-02'], freq='M'),
            'final_level': ['Cảnh báo cao', 'Cảnh báo cao', 'Bình thường']})
        self.hits = pd.DataFrame({'txn_id': ['t2'], 'related_txn_ids': ['t1']})
        self.approved = {'SHOP'}

    def row(self, txn_id):
        return self.df.set_index('txn_id', drop=False).loc[txn_id]

    def case(self, txn_id):
        return {'txn_id': txn_id, 'transaction': {'amount': 2500.0},
                'expense_group': {'group': 'FACILITIES_MRO', 'name': 'Vật tư'},
                'rule_hits': [{'rule_id': 'R04', 'rule_name': 'Chia nhỏ', 'severity': 'Cao',
                               'message': '2 giao dịch ... tổng $5,500.00 vượt hạn mức $5,000.00.', 'related_txn_ids': 't1'}],
                'anomaly': {'reasons': [], 'signals': [], 'burst_day': None}, 'budget': None,
                'risk': {'rule_level': 'Cảnh báo cao', 'final_level': 'Cảnh báo cao'}}

    def cardholder_history(self, txn_id, limit=10):
        return {'n_transactions_before': 1}

    def budget(self, *a):
        return None


def tool_call(name, args, cid):
    return NS(id=cid, function=NS(name=name, arguments=json.dumps(args)),
              model_dump=lambda: {'id': cid, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}})


def report(quote, evidence):
    return json.dumps({
        'summary': 'Hai khoản mua cùng cửa hàng trong 2 ngày.',
        'findings': [{'text': 'Tổng hai khoản $5,500.00 vượt hạn mức.', 'assessment': 'dang_lo_ngai', 'evidence': evidence}],
        'citations': [{'article_id': 'D05', 'quote': quote}],
        'items_to_verify': ['Hai hóa đơn có cùng đơn hàng không'],
        'recommended_action': 'chuyen_phe_duyet', 'recommendation_reason': 'Có dấu hiệu chia nhỏ',
        'draft_review_request': {'needed': True, 'route_to': 'quan_ly', 'reason': 'Nghi chia nhỏ', 'required_documents': ['Hóa đơn']}})


def fake_client(*messages):
    queue, sent = list(messages), []

    def create(**kw):
        sent.append(kw)
        m = queue.pop(0)
        if isinstance(m, Exception):
            raise m
        msg = m if isinstance(m, NS) else NS(content=m, tool_calls=None)
        return NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=100, completion_tokens=20))
    return NS(chat=NS(completions=NS(create=create)), sent=sent)


def test_agent_plans_investigates_self_corrects_and_stays_within_allowed_values():
    client = fake_client(
        json.dumps({'plan': [{'check': 'Xem giao dịch liên quan', 'tool': 'get_related_transactions', 'why': 'R04'}]}),
        NS(content='', tool_calls=[tool_call('get_related_transactions', {'txn_id': 't2', 'kind': 'cung_cua_hang_30_ngay'}, 'c1'),
                                   tool_call('get_merchant_profile', {'merchant_norm': 'SHOP'}, 'c2')]),
        'XONG',
        report('Hạn mức chia nhỏ là 10.000 USD.', ['t1', 't2']),       # trích dẫn bịa -> phải sửa
        report(D05_QUOTE, ['t1', 't2']),
    )
    out = InvestigationAgent(FakeEngine(), RETRIEVER, client=client, model='m').run(
        't2', ['chuyen_phe_duyet', 'yeu_cau_bo_sung'], {'chuyen_phe_duyet': 'Chuyển quản lý phê duyệt'})

    assert [s['tool'] for s in out['trace']] == ['get_related_transactions', 'get_merchant_profile']
    assert out['revisions'] == 1 and out['warnings'] == []
    assert out['report']['citations'][0]['article_id'] == 'D05'
    final_schema = client.sent[-1]['response_format']['json_schema']['schema']['properties']
    assert final_schema['recommended_action']['enum'] == ['chuyen_phe_duyet', 'yeu_cau_bo_sung', 'khong_de_xuat']
    evidence_enum = final_schema['findings']['items']['properties']['evidence']['items']['enum']
    assert {'t1', 't2', 't9'} <= set(evidence_enum)                  # t9 chỉ được thấy qua công cụ
    assert out['usage']['llm_calls'] == 5 and out['n_tool_calls'] == 2


def test_tool_budget_is_enforced_and_tool_errors_are_returned():
    calls = [tool_call('get_merchant_profile', {'merchant_norm': 'KHONG CO'}, f'c{i}') for i in range(3)]
    client = fake_client(json.dumps({'plan': []}), NS(content='', tool_calls=calls), report(D05_QUOTE, ['t2']))
    out = InvestigationAgent(FakeEngine(), RETRIEVER, client=client, model='m', max_tool_calls=2).run('t2', [])
    assert out['n_tool_calls'] == 2 and out['trace'][0]['error']
    assert client.sent[-1]['response_format']['json_schema']['schema']['properties']['recommended_action']['enum'] == ['khong_de_xuat']


def test_failed_plan_falls_back_to_investigating_without_a_plan():
    client = fake_client('khong phai json', '', 'XONG', report(D05_QUOTE, ['t2']))
    out = InvestigationAgent(FakeEngine(), RETRIEVER, client=client, model='m').run('t2', ['chuyen_phe_duyet'])
    assert out['plan'] == [] and out['report']['recommended_action'] == 'chuyen_phe_duyet'


def test_oversized_report_request_is_shrunk_instead_of_losing_the_investigation():
    client = fake_client(json.dumps({'plan': []}), 'XONG',
                         Exception('Error code: 413 - Request too large for model'), report(D05_QUOTE, ['t2']))
    out = InvestigationAgent(FakeEngine(), RETRIEVER, client=client, model='m').run('t2', ['chuyen_phe_duyet'])
    assert out['report'] is not None and out['warnings'] == []


def test_evidence_is_limited_to_the_part_of_tool_results_the_llm_saw(monkeypatch):
    monkeypatch.setattr('src.agent.TOOL_RESULT_CHARS', 20)           # kết quả bị cắt trước khi có t9
    client = fake_client(json.dumps({'plan': []}),
                         NS(content='', tool_calls=[tool_call('get_related_transactions',
                                                              {'txn_id': 't2', 'kind': 'cung_cua_hang_30_ngay'}, 'c1')]),
                         'XONG', report(D05_QUOTE, ['t2']))
    InvestigationAgent(FakeEngine(), RETRIEVER, client=client, model='m').run('t2', ['chuyen_phe_duyet'])
    enum = client.sent[-1]['response_format']['json_schema']['schema']['properties']['findings']['items']['properties']['evidence']['items']['enum']
    assert 't9' not in enum and 't2' in enum

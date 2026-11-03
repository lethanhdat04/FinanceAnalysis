"""Kiểm thử RAG, kiểm chứng đầu ra LLM và trợ lý tool – dùng client giả lập, không gọi API thật."""
import json
from types import SimpleNamespace as NS

import pytest

from src.assistant import Assistant, ReviewStore
from src.llm import explain_case, validate_explanation
from src.rag import PolicyRetriever, parse_policy

CHUNKS = parse_policy()
RETRIEVER = PolicyRetriever(CHUNKS, embed_model=None)      # chỉ BM25, không tải model embedding


def fake_client(*contents_or_messages):
    """Client trả lần lượt các message định sẵn; ghi lại các request đã gửi."""
    queue = list(contents_or_messages)
    sent = []

    def create(**kw):
        sent.append(kw)
        m = queue.pop(0)
        msg = m if isinstance(m, NS) else NS(content=m, tool_calls=None)
        return NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=10, completion_tokens=5, total_tokens=15))
    return NS(chat=NS(completions=NS(create=create)), sent=sent)


CASE = {'txn_id': 't1', 'rule_hits': [{'rule_id': 'R04', 'message': '...'}],
        'risk': {'rule_level': 'Cảnh báo cao', 'final_level': 'Cảnh báo cao'}}


def test_policy_parsing():
    assert [c.id for c in CHUNKS] == [f'D{i:02d}' for i in range(1, 27)]
    d05 = next(c for c in CHUNKS if c.id == 'D05')
    assert d05.rules == ['R04'] and d05.signals == ['burst'] and 'chia' in d05.title.lower()


def test_required_articles_follow_rules_and_signals():
    ids = [c.id for c in RETRIEVER.required_for(rule_ids=['R04'], signals=['burst'])]
    assert ids[:2] == ['D05', 'D26'] and 'D24' in ids


def test_bm25_finds_limit_article():
    assert RETRIEVER.search('hạn mức mỗi giao dịch là bao nhiêu', k=1)[0].id == 'D04'


def test_validation_drops_fabricated_quotes_and_unknown_rules():
    chunks = [c for c in CHUNKS if c.id in ('D04', 'D05')]
    result = {'citations': [
        {'article_id': 'D05', 'quote': '1. Nghiêm cấm việc chia một khoản mua sắm thành nhiều giao dịch nhỏ hơn hạn mức để tránh phê duyệt.'},
        {'article_id': 'D04', 'quote': 'Hạn mức là 10.000 USD.'},                 # bịa
        {'article_id': 'D14', 'quote': 'Không được dùng thẻ'}],                      # không được truy xuất
        'reasons': [{'text': 'x', 'source': 'rule', 'ref': 'R02'}]}
    warnings = validate_explanation(result, chunks, CASE)
    assert [c['article_id'] for c in result['citations']] == ['D05']
    assert len(warnings) == 3


def test_explain_case_keeps_risk_from_code_and_limits_citations_to_retrieved():
    chunks = [c for c in CHUNKS if c.id in ('D05', 'D26')]
    llm_json = json.dumps({'summary': 'Rủi ro thấp', 'reasons': [], 'items_to_verify': [], 'next_steps': [],
                           'citations': [{'article_id': 'D05', 'quote': 'Nghiêm cấm việc chia một khoản mua sắm'}]})
    client = fake_client(llm_json)
    out = explain_case(CASE, chunks, client=client)
    assert out['risk']['final_level'] == 'Cảnh báo cao'
    schema = client.sent[0]['response_format']['json_schema']['schema']
    assert schema['properties']['citations']['items']['properties']['article_id']['enum'] == ['D05', 'D26']
    assert schema['properties']['reasons']['items']['properties']['ref']['enum'] == ['R04', '']   # chỉ rule có trong hồ sơ
    assert out['warnings'] == []


class FakeEngine:
    def row(self, txn_id):
        if txn_id != 't1':
            raise KeyError(txn_id)

    def case(self, txn_id):
        return CASE


def tool_call(name, args, cid='c1'):
    return NS(id=cid, function=NS(name=name, arguments=json.dumps(args)), model_dump=lambda: {
        'id': cid, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}})


def test_review_request_needs_user_confirmation(tmp_path):
    store = ReviewStore(tmp_path / 'reviews.jsonl')
    args = {'txn_id': 't1', 'reason': 'Nghi chia nhỏ', 'route_to': 'quan_ly', 'required_documents': ['Hóa đơn']}
    client = fake_client(NS(content='', tool_calls=[tool_call('propose_review_request', args)]),
                         'Tôi đã đề xuất yêu cầu review, vui lòng xác nhận.')
    out = Assistant(FakeEngine(), RETRIEVER, store=store, client=client).ask('Tạo yêu cầu review cho t1')
    assert out['tool_calls'][0]['tool'] == 'propose_review_request'
    assert len(out['pending_reviews']) == 1 and store.all() == []          # chưa lưu
    saved = store.confirm(out['pending_reviews'][0]['proposal_id'], user='ke_toan_a')
    assert saved['status'] == 'Chờ review' and len(store.all()) == 1


def test_tool_errors_are_returned_to_llm_not_raised(tmp_path):
    client = fake_client(NS(content='', tool_calls=[tool_call('lookup_transaction', {'txn_id': 'không-có'})]),
                         'Không tìm thấy giao dịch.')
    asst = Assistant(FakeEngine(), RETRIEVER, store=ReviewStore(tmp_path / 'r.jsonl'), client=client)
    asst.engine.case = lambda t: (_ for _ in ()).throw(KeyError('Không tìm thấy giao dịch'))
    out = asst.ask('Tra cứu giao dịch không-có')
    tool_msg = [m for m in asst.messages if m['role'] == 'tool'][0]
    assert 'error' in json.loads(tool_msg['content']) and out['answer'] == 'Không tìm thấy giao dịch.'


def test_missing_api_key_gives_clear_error(monkeypatch, tmp_path):
    import src.llm as llm
    monkeypatch.delenv('GROQ_API_KEY', raising=False)
    monkeypatch.setattr(llm, 'ROOT', tmp_path)
    with pytest.raises(RuntimeError, match='GROQ_API_KEY'):
        llm.get_client()


def test_group_specific_articles_only_for_matching_group():
    car = [c.id for c in RETRIEVER.required_for(rule_ids=['R03'], groups=['VEHICLE_FUEL'])]
    assert 'D08' in car and 'D19' in car and 'D17' not in car and 'D18' not in car
    hotel = [c.id for c in RETRIEVER.required_for(rule_ids=['R03'], groups=['LODGING'])]
    assert 'D18' in hotel and 'D17' not in hotel


def test_number_pair_check_flags_mixed_budget_figures():
    from src.llm import check_number_pairs, llm_view
    case = {'rule_hits': [{'message': 'Giao dịch làm lũy kế chi $18,220.59 vượt ngân sách tháng $17,870.00 (102%).'}],
            'anomaly': {'reasons': [], 'burst_day': None},
            'budget': {'month': '2026-08', 'group_name': 'Xe cộ', 'spent': 19689.61, 'budget': 17870.0,
                       'ratio': 1.102, 'remaining': -1819.61}}
    ok = 'Lũy kế chi $18,220.59 vượt ngân sách $17,870.00 (102%).'
    mixed = 'Lũy kế chi $19,689.61 so với dự toán (102%).'
    assert check_number_pairs([ok], case) == []
    assert len(check_number_pairs([mixed], case)) == 1
    real_error = 'Giao dịch vượt ngân sách, lũy kế chi $19,689.61 so với dự toán $17,870.00 (102%), vi phạm quy định.'
    assert len(check_number_pairs([real_error], case)) == 1   # lỗi thật đã gặp
    with_threshold = 'Lũy kế chi $18,220.59 vượt ngân sách $17,870.00 (102%), vượt 100% dự toán.'
    assert check_number_pairs([with_threshold], case, ['Khoản chi làm lũy kế vượt 100% dự toán phải được phê duyệt']) == []
    assert check_number_pairs(['Tổng chi cả tháng $19,689.61 trên ngân sách $17,870.00 (110%)'], case) == []
    assert isinstance(llm_view(case)['budget'], str) and 'vượt $1,819.61' in llm_view(case)['budget']


def test_numbers_are_checked_by_value_in_every_field():
    from src.llm import check_numbers
    case = {'txn_id': 't1', 'transaction': {'amount': 2450.0},
            'rule_hits': [{'rule_id': 'R05', 'message': 'Giao dịch làm lũy kế chi $18,220.59 vượt ngân sách tháng $17,870.00 (102%).'}],
            'anomaly': {'reasons': [], 'burst_day': None}, 'budget': None}
    assert check_numbers(['Lũy kế $18,220.59 (102%); khoản chi $2,450.00, tức khoảng $2,450.'], case) == []
    assert check_numbers(['Còn lại $8,220.59.'], case)              # trước đây lọt vì là chuỗi con của $18,220.59
    assert check_numbers(['Kiểm tra hóa đơn $99,999.00'], case)      # số bịa đứng một mình
    assert check_numbers(['Khoản 5.000 USD cần duyệt'], case)        # số tiền kiểu Việt cũng được kiểm
    assert check_numbers(['Vượt hạn mức $5,000.00'], case, ['Mỗi giao dịch không quá $5,000.00']) == []   # ngưỡng quy chế
    assert check_numbers(['vượt 2%'], case)


def test_quote_must_start_at_a_clause_boundary_and_not_be_too_short():
    from src.llm import quote_ok
    d06 = next(c for c in CHUNKS if c.id == 'D06').text
    assert quote_ok('Người phê duyệt không được tự phê duyệt khoản chi của chính mình.', d06)
    assert not quote_ok('được tự phê duyệt khoản chi của chính mình', d06)     # cắt bỏ "không" -> đảo nghĩa
    assert not quote_ok('phê duyệt', d06)


def test_rule_reason_without_ref_and_unknown_rule_mentions_are_flagged():
    d05 = next(c for c in CHUNKS if c.id == 'D05')
    result = {'summary': 'Giao dịch vi phạm R02.', 'citations': [],
              'reasons': [{'text': 'Vượt hạn mức.', 'source': 'rule', 'ref': ''}], 'items_to_verify': [], 'next_steps': []}
    w = validate_explanation(result, [d05], CASE)
    assert any('không nêu mã rule' in x for x in w) and any('R02' in x for x in w)


def test_invalid_proposal_arguments_are_rejected_and_only_new_proposals_are_returned(tmp_path):
    store = ReviewStore(tmp_path / 'r.jsonl')
    good = {'txn_id': 't1', 'reason': 'Nghi chia nhỏ', 'route_to': 'quan_ly', 'required_documents': ['Hóa đơn']}
    bad = {'txn_id': 't1', 'reason': 'x', 'route_to': 'giam_doc', 'required_documents': [], 'extra': 1}
    client = fake_client(NS(content='', tool_calls=[tool_call('propose_review_request', good)]), 'Đã đề xuất.',
                         NS(content='', tool_calls=[tool_call('propose_review_request', bad)]), 'Không tạo được.')
    asst = Assistant(FakeEngine(), RETRIEVER, store=store, client=client)
    assert len(asst.ask('Đề xuất review t1')['pending_reviews']) == 1
    out = asst.ask('Đề xuất thêm một yêu cầu')
    assert out['pending_reviews'] == []                                     # không lặp lại đề xuất của lượt trước
    assert 'route_to' in json.loads([m for m in asst.messages if m['role'] == 'tool'][-1]['content'])['error']


def test_a_proposal_can_only_be_confirmed_once(tmp_path):
    store = ReviewStore(tmp_path / 'r.jsonl')
    p = store.propose(txn_id='t1', reason='Kiểm tra', route_to='ke_toan', required_documents=[])
    store.confirm(p['proposal_id'], user='kt')
    with pytest.raises(KeyError):
        store.confirm(p['proposal_id'], user='kt')                           # bấm đúp
    assert len(store.all()) == 1


def test_old_turns_are_dropped_to_keep_requests_small(tmp_path):
    from src import assistant as A
    answers = [f'Trả lời {i} ' + 'x' * 6000 for i in range(4)]
    client = fake_client(*answers)
    sent_messages, create = [], client.chat.completions.create
    client.chat.completions.create = lambda **kw: (sent_messages.append([dict(m) for m in kw['messages']]), create(**kw))[1]
    asst = Assistant(FakeEngine(), RETRIEVER, store=ReviewStore(tmp_path / 'r.jsonl'), client=client)
    for i in range(4):
        asst.ask(f'Câu hỏi {i}')
    sizes = [sum(len(m.get('content') or '') for m in msgs[1:]) for msgs in sent_messages]
    assert max(sizes) <= A.HISTORY_CHARS and sent_messages[-1][0]['role'] == 'system'
    assert sent_messages[-1][-1]['content'] == 'Câu hỏi 3'

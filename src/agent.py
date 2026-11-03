"""AI Agent điều tra hồ sơ giao dịch bị cảnh báo (hướng A).

Ba bước:
  1. Lập kế hoạch: agent nêu sẽ kiểm tra gì, bằng công cụ nào.
  2. Điều tra: gọi công cụ CHỈ ĐỌC qua nhiều bước (giới hạn số lần gọi).
  3. Viết hồ sơ điều tra (JSON theo schema) -> code kiểm chứng -> nếu lỗi, agent sửa thêm một vòng.

Ràng buộc:
- Mã giao dịch làm bằng chứng chỉ được lấy trong các giao dịch agent đã thấy (enum trong schema).
- Thao tác đề xuất chỉ được chọn trong các thao tác hợp lệ của hồ sơ; agent không thực hiện thao tác, không tạo yêu cầu.
- Mức rủi ro và vi phạm do code quyết định, agent không thay đổi.
"""
import json
import re
import time

import pandas as pd

from src.labels import GROUPS
from src.llm import MODEL_EXPLAIN, chat, check_numbers, check_rule_mentions, get_client, llm_view, validate_explanation

MAX_TOOL_CALLS = 8
MAX_ROWS = 25
# Bản miễn phí của Groq giới hạn 8.000 token/phút cho MỘT request -> giữ mọi request nhỏ hơn mức này.
TOOL_RESULT_CHARS = 1500     # kết quả công cụ đưa lại cho agent trong vòng tra cứu
DIGEST_CHARS = 900           # mỗi kết quả trong bản tóm lược bằng chứng khi viết hồ sơ
MAX_ARTICLES = 6             # số điều quy chế tối đa gửi kèm khi viết hồ sơ (điều bắt buộc luôn được giữ)
TXN_ID = re.compile(r'"txn_id": "([^"]+)"|\b(\d{6}-\d{6})\b')

SYSTEM = """Bạn là agent điều tra hỗ trợ kế toán kiểm tra một hồ sơ giao dịch thẻ mua sắm đã bị hệ thống cảnh báo.
Nhiệm vụ: thu thập bằng chứng bằng các công cụ CHỈ ĐỌC, rồi lập hồ sơ điều tra để kế toán quyết định.
Nguyên tắc:
- Vi phạm quy tắc, dấu hiệu bất thường và mức rủi ro đã được hệ thống tính; không tự kiểm tra lại hay thay đổi chúng.
- Mọi số liệu phải lấy từ công cụ; không tự cộng trừ, không tự tính ngày hay tỷ lệ.
- Lỗi hoặc kết quả rỗng của công cụ (vd gõ sai tên) KHÔNG phải là bằng chứng vi phạm; gọi lại với tham số đúng
  (dùng đúng merchant_norm, txn_id trong hồ sơ) hoặc ghi là chưa tra cứu được.
- Không khẳng định những điều không có thông tin (vd "chưa có phê duyệt", "không có hóa đơn"); đưa chúng vào danh sách cần kiểm tra.
- Tìm cả bằng chứng làm GIẢM nghi ngờ (ví dụ mua định kỳ hợp lệ, nhà cung cấp quen, đơn vị khác cũng mua ở đó),
  không chỉ bằng chứng làm tăng nghi ngờ.
- Bạn chỉ đề xuất; không thực hiện thao tác, không tạo yêu cầu review."""

TOOL_LABELS = {
    'get_related_transactions': 'Xem giao dịch liên quan',
    'get_cardholder_history': 'Xem lịch sử người chi',
    'get_merchant_profile': 'Xem hồ sơ nhà cung cấp',
    'check_budget': 'Kiểm tra ngân sách',
    'search_policy': 'Tìm trong quy chế',
    'get_past_decisions': 'Xem kết luận trước đây của kế toán',
}


def _fn(name, desc, props, required):
    return {'type': 'function', 'function': {'name': name, 'description': desc, 'parameters': {
        'type': 'object', 'properties': props, 'required': required, 'additionalProperties': False}}}


TOOLS = [
    _fn('get_related_transactions', 'Giao dịch liên quan của người chi: cung_ngay (cùng ngày), cung_cua_hang_30_ngay '
        '(cùng cửa hàng trong ±30 ngày), lien_quan_rule (các giao dịch mà vi phạm quy tắc chỉ tới).',
        {'txn_id': {'type': 'string'}, 'kind': {'type': 'string', 'enum': ['cung_ngay', 'cung_cua_hang_30_ngay', 'lien_quan_rule']}},
        ['txn_id', 'kind']),
    _fn('get_cardholder_history', 'Thói quen chi tiêu trước đó của người chi (số giao dịch, mức thường chi, cửa hàng hay mua).',
        {'txn_id': {'type': 'string'}}, ['txn_id']),
    _fn('get_merchant_profile', 'Hồ sơ nhà cung cấp trên toàn hệ thống: lần đầu xuất hiện, số người chi và đơn vị đã mua, '
        'đã được duyệt chưa, nhóm chi phí thường gặp.', {'merchant_norm': {'type': 'string'}}, ['merchant_norm']),
    _fn('check_budget', 'Ngân sách tháng của đơn vị cho một nhóm chi phí, số đã chi cả tháng và số còn lại (đã tính sẵn).',
        {'agency_id': {'type': 'string'}, 'expense_group': {'type': 'string'}, 'month': {'type': 'string', 'description': 'YYYY-MM'}},
        ['agency_id', 'expense_group', 'month']),
    _fn('search_policy', 'Tìm điều quy chế chi tiêu liên quan.', {'query': {'type': 'string'}}, ['query']),
    _fn('get_past_decisions', 'Kết luận trước đây của kế toán cho cùng loại cảnh báo (mã rule như R03, hoặc mo_hinh): '
        'bao nhiêu lần là cảnh báo đúng, bao nhiêu lần là cảnh báo giả, kèm vài ghi chú.',
        {'signal': {'type': 'string'}}, ['signal']),
]


class InvestigationAgent:
    def __init__(self, engine, retriever, client=None, model: str = MODEL_EXPLAIN, decisions_fn=None,
                 max_tool_calls: int = MAX_TOOL_CALLS):
        self.engine, self.retriever = engine, retriever
        self.client, self.model = client, model
        self.decisions_fn = decisions_fn          # signal -> dict; do app cung cấp (đọc bảng decisions)
        self.max_tool_calls = max_tool_calls

    # ------------------------------------------------------------ công cụ (code)
    def _txn_rows(self, rows: pd.DataFrame) -> list[dict]:
        rows = rows.sort_values(['tx_date', 'txn_id']).head(MAX_ROWS)
        return [{'txn_id': r.txn_id, 'ngay': f'{r.tx_date:%d/%m/%Y}', 'cua_hang': r.merchant, 'mat_hang': r.item_desc,
                 'so_tien': round(float(r.amount), 2), 'muc_rui_ro': r.final_level if 'final_level' in rows else None}
                for r in rows.itertuples()]

    def tool(self, name: str, args: dict, chunks_seen: dict):
        e, df = self.engine, self.engine.df
        if name == 'get_related_transactions':
            r = e.row(args['txn_id'])
            if args['kind'] == 'cung_ngay':
                rows = df[(df.cardholder_id == r.cardholder_id) & (df.tx_date == r.tx_date)]
            elif args['kind'] == 'cung_cua_hang_30_ngay':
                rows = df[(df.cardholder_id == r.cardholder_id) & (df.merchant_norm == r.merchant_norm)
                          & ((df.tx_date - r.tx_date).abs() <= pd.Timedelta(days=30))]
            else:
                hits = e.hits[e.hits.txn_id == args['txn_id']]
                ids = {i for s in hits.related_txn_ids for i in str(s).split(',') if i}
                rows = df[df.txn_id.isin(ids)]
            return {'so_giao_dich': int(len(rows)), 'tong_tien': round(float(rows.amount.sum()), 2),
                    'giao_dich': self._txn_rows(rows), 'da_cat_bot': len(rows) > MAX_ROWS}
        if name == 'get_cardholder_history':
            return e.cardholder_history(args['txn_id'])
        if name == 'get_merchant_profile':
            m = df[df.merchant_norm == args['merchant_norm']]
            if m.empty:
                r = e.row(self._current_txn)
                return {'error': f"Không tìm thấy merchant_norm '{args['merchant_norm']}'. Tên chuẩn hóa của nhà cung cấp "
                                 f"trong hồ sơ là '{r.merchant_norm}'. Đây là lỗi tra cứu, không phải bằng chứng."}
            return {'merchant_norm': args['merchant_norm'], 'so_giao_dich': int(len(m)),
                    'so_nguoi_chi': int(m.cardholder_id.nunique()), 'so_don_vi': int(m.agency_id.nunique()),
                    'lan_dau_xuat_hien': str(m.month.min()), 'da_duoc_duyet': args['merchant_norm'] in e.approved,
                    'nhom_chi_phi_thuong_gap': GROUPS.get(m.expense_group.mode().iloc[0], m.expense_group.mode().iloc[0]),
                    'muc_chi_trung_vi': round(float(m.amount.median()), 2)}
        if name == 'check_budget':
            return e.budget(args['agency_id'], args['expense_group'], args['month']) or {'khong_ap_dung': 'Đơn vị không có ngân sách tháng riêng cho nhóm chi phí này (nhóm chi không thường xuyên), nên không xét quy tắc vượt ngân sách. Đây không phải dấu hiệu rủi ro.'}
        if name == 'search_policy':
            found = self.retriever.search(args['query'], k=3)
            for c in found:
                chunks_seen[c.id] = c
            return [{'id': c.id, 'title': c.title, 'text': c.text} for c in found]
        if name == 'get_past_decisions':
            return self.decisions_fn(args['signal']) if self.decisions_fn else {'ghi_chu': 'Chưa có dữ liệu kết luận'}
        raise ValueError(f'Công cụ không tồn tại: {name}')

    # ------------------------------------------------------------ chạy agent
    def run(self, txn_id: str, allowed_actions: list[str], action_labels: dict[str, str] | None = None,
            on_event=None) -> dict:
        """on_event(kind, data): báo tiến độ (plan, step, report) để giao diện hiển thị trong lúc chạy."""
        emit = on_event or (lambda kind, data: None)
        self.client = self.client or get_client()
        t0, trace = time.time(), []
        # api_time_s: thời gian Groq xử lý (không gồm thời gian chờ hạn mức) – dùng để so sánh chi phí thời gian
        usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'llm_calls': 0, 'api_time_s': 0.0}
        self._current_txn = txn_id
        case = self.engine.case(txn_id)
        known_ids = {txn_id}
        if case['anomaly'].get('burst_day'):
            known_ids |= set(case['anomaly']['burst_day']['txn_ids'])
        for h in case['rule_hits']:
            known_ids |= {i for i in h['related_txn_ids'].split(',') if i}
        chunks = {c.id: c for c in self.retriever.required_for(
            [h['rule_id'] for h in case['rule_hits']], case['anomaly']['signals'], [case['expense_group']['group']])}

        def call(**kw):
            resp = chat(self.client, model=self.model, temperature=0.2, **kw)
            u = getattr(resp, 'usage', None)
            usage['prompt_tokens'] += getattr(u, 'prompt_tokens', 0) or 0
            usage['completion_tokens'] += getattr(u, 'completion_tokens', 0) or 0
            usage['llm_calls'] += 1
            usage['api_time_s'] += getattr(u, 'total_time', 0) or 0
            return resp.choices[0].message

        case_text = json.dumps(llm_view(case), ensure_ascii=False, default=str)
        tool_names = [t['function']['name'] for t in TOOLS]

        # 1. Lập kế hoạch
        plan_schema = {'type': 'object', 'properties': {'plan': {'type': 'array', 'items': {
            'type': 'object', 'properties': {'check': {'type': 'string'}, 'tool': {'type': 'string', 'enum': tool_names},
                                             'why': {'type': 'string'}},
            'required': ['check', 'tool', 'why'], 'additionalProperties': False}}},
            'required': ['plan'], 'additionalProperties': False}
        plan = []
        for _ in range(2):
            try:
                msg = call(messages=[{'role': 'system', 'content': SYSTEM},
                                     {'role': 'user', 'content': f'HỒ SƠ:\n{case_text}\n\nLập kế hoạch điều tra (tối đa 6 bước).'}],
                           reasoning_effort='low',
                           response_format={'type': 'json_schema', 'json_schema': {'name': 'plan', 'strict': True,
                                                                                     'schema': plan_schema}})
                plan = json.loads(msg.content or '')['plan'][:6]
                break
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            except RuntimeError:
                raise
            except Exception as ex:
                if 'json_validate_failed' not in str(ex):
                    raise
        emit('plan', plan)

        # 2. Điều tra bằng công cụ
        messages = [{'role': 'system', 'content': SYSTEM},
                    {'role': 'user', 'content': f'HỒ SƠ:\n{case_text}\n\nKẾ HOẠCH:\n{json.dumps(plan, ensure_ascii=False)}\n\n'
                                                f'Thực hiện kế hoạch bằng công cụ (tối đa {self.max_tool_calls} lần gọi). '
                                                f'Khi đủ thông tin, trả lời "XONG" kèm tóm tắt ngắn những gì đã tìm thấy.'}]
        n_calls, digest = 0, []
        while n_calls < self.max_tool_calls:
            msg = call(messages=messages, tools=TOOLS, tool_choice='auto', reasoning_effort='medium')
            calls = msg.tool_calls or []
            messages.append({'role': 'assistant', 'content': msg.content or '',
                             **({'tool_calls': [c.model_dump() if hasattr(c, 'model_dump') else c for c in calls]} if calls else {})})
            if not calls:
                break
            for c in calls[: self.max_tool_calls - n_calls]:
                n_calls += 1
                s0, args = time.time(), {}
                try:
                    args = json.loads(c.function.arguments or '{}')
                    result = self.tool(c.function.name, args, chunks)
                except Exception as ex:                    # lỗi công cụ trả lại cho agent
                    result = {'error': str(ex)}
                text = json.dumps(result, ensure_ascii=False, default=str)
                # chỉ mã giao dịch nằm trong phần kết quả đưa cho LLM (kết quả dài bị cắt ở TOOL_RESULT_CHARS)
                known_ids |= {a or b for a, b in TXN_ID.findall(text[:TOOL_RESULT_CHARS])}
                trace.append({'step': n_calls, 'tool': c.function.name, 'label': TOOL_LABELS.get(c.function.name, c.function.name),
                              'args': args, 'result_preview': text[:300], 'error': result.get('error') if isinstance(result, dict) else None,
                              'seconds': round(time.time() - s0, 2)})
                messages.append({'role': 'tool', 'tool_call_id': c.id, 'name': c.function.name,
                                 'content': text[:TOOL_RESULT_CHARS]})
                digest.append(f"[{n_calls}] {TOOL_LABELS.get(c.function.name, c.function.name)} {json.dumps(args, ensure_ascii=False)}: "
                              f"{text[:DIGEST_CHARS]}")
                emit('step', trace[-1])

        # 3. Viết hồ sơ điều tra từ bản tóm lược bằng chứng (không gửi lại cả hội thoại), kiểm chứng, sửa một vòng
        required = [c.id for c in self.retriever.required_for(
            [h['rule_id'] for h in case['rule_hits']], case['anomaly']['signals'], [case['expense_group']['group']])]
        optional = [i for i in chunks if i not in required]
        order = required + optional[:max(0, MAX_ARTICLES - len(required))]
        if not order:                              # nhóm chi phí không có điều riêng, không rule, không tín hiệu
            found = self.retriever.search(self.engine.retrieval_query(case), k=3)
            chunks.update({c.id: c for c in found})
            order = [c.id for c in found]
        chunks = {i: chunks[i] for i in order}
        schema = report_schema(sorted(known_ids), sorted(chunks), allowed_actions)
        actions_text = ', '.join(f'{a} ({(action_labels or {}).get(a, a)})' for a in allowed_actions) or 'không có'
        notes = next((m['content'] for m in reversed(messages) if m['role'] == 'assistant' and m.get('content')), '')
        evidence_texts = list(digest)

        def report_messages(dchars: int) -> list:
            return [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': (
            f'HỒ SƠ:\n{case_text}\n\nBẰNG CHỨNG ĐÃ TRA CỨU:\n' + '\n'.join(d[:dchars] for d in digest)
            + (f'\n\nGHI CHÚ CỦA AGENT:\n{notes[:800]}' if notes else '') + '\n\n'
            'Lập hồ sơ điều tra bằng tiếng Việt. Bằng chứng chỉ dùng mã giao dịch đã thấy. Trích dẫn là MỘT câu nguyên văn '
            f'trong các điều quy chế: {", ".join(sorted(chunks))}. Các thao tác hợp lệ của hồ sơ: {actions_text}; '
            'chọn khong_de_xuat nếu chưa đủ căn cứ. Giữ nguyên cặp số liệu như trong công cụ, không tự tính.\n\nĐIỀU QUY CHẾ:\n'
            + '\n\n'.join(f'[{c.id}] {c.title}\n{c.text}' for c in chunks.values()))}]

        dchars = DIGEST_CHARS
        messages = report_messages(dchars)
        report, revisions, warnings = None, 0, []
        emit('writing', None)
        for attempt in range(2):
            report = None
            for _ in range(3):
                # đầu ra bị cắt / rỗng / không khớp schema -> yêu cầu viết ngắn hơn;
                # request vượt giới hạn token (413) -> thu gọn bằng chứng rồi gửi lại (giữ các lượt tra cứu đã làm)
                try:
                    msg = call(messages=messages, reasoning_effort='medium', max_completion_tokens=3000,
                               response_format={'type': 'json_schema', 'json_schema': {'name': 'investigation', 'strict': True,
                                                                                         'schema': schema}})
                    report = json.loads(msg.content or '')
                    break
                except RuntimeError:
                    raise
                except Exception as ex:
                    too_large = '413' in str(ex) or 'too large' in str(ex).lower()
                    if not (too_large or 'json_validate_failed' in str(ex) or isinstance(ex, json.JSONDecodeError)):
                        raise
                    if too_large:
                        dchars //= 2
                        messages = report_messages(dchars) + messages[2:]
                    else:
                        messages.append({'role': 'user', 'content': 'Hồ sơ quá dài và bị cắt. Viết ngắn gọn hơn: tối đa 4 '
                                                                    'phát hiện, mỗi phát hiện 1-2 câu, tối đa 5 mã giao dịch.'})
            if report is None:
                raise RuntimeError('Agent không lập được hồ sơ hợp lệ sau khi yêu cầu viết ngắn hơn')
            warnings = self.validate(report, case, list(chunks.values()), evidence_texts)
            if not warnings or attempt == 1:
                break
            revisions += 1
            messages += [{'role': 'assistant', 'content': msg.content},
                         {'role': 'user', 'content': 'Bộ kiểm tra phát hiện lỗi, hãy sửa và lập lại hồ sơ:\n- ' + '\n- '.join(warnings)}]
        return {'txn_id': txn_id, 'risk': case['risk'], 'plan': plan, 'trace': trace, 'report': report,
                'warnings': warnings, 'revisions': revisions, 'n_tool_calls': n_calls,
                'articles': {c.id: {'title': c.title, 'text': c.text} for c in chunks.values()},
                'model': self.model, 'latency_s': round(time.time() - t0, 2), 'usage': usage}

    def validate(self, report: dict, case: dict, chunks, evidence_texts=()) -> list[str]:
        """Kiểm chứng bằng code: trích dẫn nguyên văn, số liệu (đối chiếu hồ sơ, quy chế và kết quả tra cứu), mã rule,
        có bằng chứng cho từng phát hiện."""
        tmp = {'citations': report['citations'], 'reasons': [], 'summary': ''}
        warnings = [w for w in validate_explanation(tmp, chunks, case) if 'Không có trích dẫn' not in w]
        d = report['draft_review_request']
        texts = ([report['summary']] + [f['text'] for f in report['findings']] + list(report['items_to_verify'])
                 + [report['recommendation_reason'], d['reason']] + list(d['required_documents']))
        warnings += check_rule_mentions(texts, {h['rule_id'] for h in case['rule_hits']})
        warnings += [w for w in check_numbers(texts, case, [c.text for c in chunks], evidence_texts) if w not in warnings]
        for f in report['findings']:
            if not f['evidence']:
                warnings.append(f'Phát hiện "{f["text"][:60]}" không có mã giao dịch làm bằng chứng.')
        report['citations'] = tmp['citations']          # đã loại trích dẫn sai
        return warnings


def report_schema(evidence_ids: list[str], article_ids: list[str], allowed_actions: list[str]) -> dict:
    strs = {'type': 'array', 'items': {'type': 'string'}}
    return {'type': 'object', 'properties': {
        'summary': {'type': 'string'},
        'findings': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'text': {'type': 'string'},
            'assessment': {'type': 'string', 'enum': ['dang_lo_ngai', 'co_the_giai_thich', 'chua_du_thong_tin']},
            'evidence': {'type': 'array', 'items': {'type': 'string', 'enum': evidence_ids}}},
            'required': ['text', 'assessment', 'evidence'], 'additionalProperties': False}},
        'citations': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'article_id': {'type': 'string', 'enum': article_ids}, 'quote': {'type': 'string'}},
            'required': ['article_id', 'quote'], 'additionalProperties': False}},
        'items_to_verify': strs,
        'recommended_action': {'type': 'string', 'enum': list(allowed_actions) + ['khong_de_xuat']},
        'recommendation_reason': {'type': 'string'},
        'draft_review_request': {'type': 'object', 'properties': {
            'needed': {'type': 'boolean'}, 'route_to': {'type': 'string', 'enum': ['ke_toan', 'quan_ly']},
            'reason': {'type': 'string'}, 'required_documents': strs},
            'required': ['needed', 'route_to', 'reason', 'required_documents'], 'additionalProperties': False},
    }, 'required': ['summary', 'findings', 'citations', 'items_to_verify', 'recommended_action',
                    'recommendation_reason', 'draft_review_request'], 'additionalProperties': False}


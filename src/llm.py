"""Gọi LLM qua Groq: giải thích hồ sơ giao dịch (có trích dẫn quy chế) và phân loại mặt hàng.

Nguyên tắc:
- LLM chỉ diễn giải kết quả đã có trong hồ sơ; mức rủi ro và vi phạm do code quyết định và được gắn lại sau.
- Đầu ra là JSON theo schema (strict). Mã điều trích dẫn bị giới hạn bằng enum trong danh sách điều đã truy xuất.
- Câu trích dẫn phải xuất hiện nguyên văn trong điều đó, nếu không sẽ bị loại và ghi cảnh báo.
"""
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_EXPLAIN = os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b')
MODEL_FAST = os.getenv('GROQ_MODEL_FAST', 'openai/gpt-oss-20b')

SYSTEM_EXPLAIN = """Bạn là trợ lý kiểm soát chi tiêu, hỗ trợ kế toán xem xét giao dịch thẻ mua sắm.
Bạn nhận một HỒ SƠ giao dịch (do hệ thống tính sẵn) và các ĐIỀU QUY CHẾ liên quan.

Yêu cầu:
1. Chỉ dùng thông tin trong hồ sơ và các điều quy chế được cung cấp. Không suy đoán thêm số liệu.
2. Không tự kiểm tra lại hay thay đổi kết luận: vi phạm rule, dấu hiệu bất thường và mức rủi ro đã được hệ thống xác định.
3. Giải thích ngắn gọn bằng tiếng Việt vì sao giao dịch được xếp mức rủi ro như vậy. Nếu không có vi phạm hay dấu hiệu nào, nói rõ là giao dịch bình thường.
4. Với mỗi lý do, ghi nguồn: "rule" (kèm mã rule, ví dụ R04), "mo_hinh" (dấu hiệu bất thường hoặc cụm mua dồn) hoặc "phan_loai" (nhóm chi phí do mô hình gợi ý).
5. Trích dẫn điều quy chế làm căn cứ: "quote" phải là một câu hoặc đoạn chép NGUYÊN VĂN từ nội dung điều đó.
6. Nêu cụ thể những thông tin, chứng từ kế toán cần kiểm tra, và các bước xử lý tiếp theo theo quy chế.
7. Dấu hiệu bất thường không phải là kết luận vi phạm; diễn đạt thận trọng."""

SYSTEM_CLASSIFY = """Bạn phân loại mặt hàng mua bằng thẻ mua sắm của cơ quan nhà nước và trường đại học vào nhóm chi phí.
Mô tả mặt hàng thường bị cắt ngắn (khoảng 26 ký tự), có thể gồm nhiều món ngăn bởi "|".
Chọn nhóm theo mục đích sử dụng của món hàng. Nếu mô tả quá mơ hồ để xác định, chọn UNKNOWN."""


def get_client():
    from dotenv import load_dotenv
    from groq import Groq

    load_dotenv(ROOT / '.env')
    if not os.getenv('GROQ_API_KEY'):
        raise RuntimeError('Chưa có GROQ_API_KEY. Tạo file .env ở thư mục gốc với dòng: GROQ_API_KEY=gsk_...')
    return Groq()


def explanation_schema(article_ids: list[str]) -> dict:
    return {
        'type': 'object',
        'properties': {
            'summary': {'type': 'string'},
            'reasons': {'type': 'array', 'items': {
                'type': 'object',
                'properties': {'text': {'type': 'string'},
                               'source': {'type': 'string', 'enum': ['rule', 'mo_hinh', 'phan_loai']},
                               'ref': {'type': 'string'}},
                'required': ['text', 'source', 'ref'], 'additionalProperties': False}},
            'citations': {'type': 'array', 'items': {
                'type': 'object',
                'properties': {'article_id': {'type': 'string', 'enum': article_ids},
                               'quote': {'type': 'string'}},
                'required': ['article_id', 'quote'], 'additionalProperties': False}},
            'items_to_verify': {'type': 'array', 'items': {'type': 'string'}},
            'next_steps': {'type': 'array', 'items': {'type': 'string'}},
        },
        'required': ['summary', 'reasons', 'citations', 'items_to_verify', 'next_steps'],
        'additionalProperties': False,
    }


def _norm(s: str) -> str:
    s = re.sub(r'^\s*\d+\.\s*', '', s.strip())          # bỏ số thứ tự khoản "2. "
    return re.sub(r'\s+', ' ', s).strip(' .;"“”').lower()


def validate_explanation(result: dict, chunks, case: dict) -> list[str]:
    """Kiểm tra sau khi LLM trả lời; sửa tại chỗ (bỏ trích dẫn sai) và trả về danh sách cảnh báo."""
    warnings, by_id = [], {c.id: c for c in chunks}
    kept = []
    for cit in result.get('citations', []):
        c = by_id.get(cit['article_id'])
        if c is None:
            warnings.append(f"Trích dẫn điều {cit['article_id']} không nằm trong các điều đã truy xuất – đã loại.")
        elif not _norm(cit['quote']) or _norm(cit['quote']) not in _norm(c.text):
            warnings.append(f"Câu trích {cit['article_id']} không khớp nguyên văn quy chế – đã loại.")
        else:
            kept.append(cit)
    result['citations'] = kept
    valid_rules = {h['rule_id'] for h in case['rule_hits']}
    for r in result.get('reasons', []):
        if r['source'] == 'rule' and r['ref'] and r['ref'] not in valid_rules:
            warnings.append(f"Lý do nhắc tới {r['ref']} nhưng hồ sơ không có vi phạm này.")
    if not result.get('citations'):
        warnings.append('Không có trích dẫn quy chế hợp lệ.')
    return warnings


def _case_prompt(case: dict, chunks) -> str:
    articles = '\n\n'.join(f'[{c.id}] {c.title}\n{c.text}' for c in chunks)
    return f'HỒ SƠ GIAO DỊCH (JSON):\n{json.dumps(case, ensure_ascii=False, default=str)}\n\nĐIỀU QUY CHẾ:\n{articles}'


def explain_case(case: dict, chunks, client=None, model: str = MODEL_EXPLAIN, reasoning_effort: str = 'medium') -> dict:
    client = client or get_client()
    schema = explanation_schema([c.id for c in chunks])
    messages = [{'role': 'system', 'content': SYSTEM_EXPLAIN}, {'role': 'user', 'content': _case_prompt(case, chunks)}]
    t0, last_err = time.time(), None
    for _ in range(2):                                   # thử lại một lần nếu JSON hỏng
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.2, reasoning_effort=reasoning_effort,
            response_format={'type': 'json_schema', 'json_schema': {'name': 'case_explanation', 'strict': True, 'schema': schema}},
        )
        try:
            result = json.loads(resp.choices[0].message.content)
            break
        except (json.JSONDecodeError, TypeError) as e:
            last_err = e
    else:
        raise RuntimeError(f'LLM không trả về JSON hợp lệ: {last_err}')
    warnings = validate_explanation(result, chunks, case)
    usage = getattr(resp, 'usage', None)
    return {
        'txn_id': case['txn_id'],
        'risk': case['risk'],                          # luôn lấy từ code, không từ LLM
        'explanation': result,
        'warnings': warnings,
        'retrieved': [c.id for c in chunks],
        'model': model,
        'latency_s': round(time.time() - t0, 2),
        'usage': {'prompt_tokens': getattr(usage, 'prompt_tokens', None),
                  'completion_tokens': getattr(usage, 'completion_tokens', None)},
    }


def classify_items(items: list[dict], groups: dict[str, str], client=None, model: str = MODEL_FAST) -> list[dict]:
    """items: [{'id', 'item_desc', 'amount', 'agency'}] -> [{'id', 'group', 'llm_time_s'}]. Gửi theo lô."""
    client = client or get_client()
    codes = list(groups) + ['UNKNOWN']
    schema = {'type': 'object', 'properties': {'results': {'type': 'array', 'items': {
        'type': 'object', 'properties': {'id': {'type': 'string'}, 'group': {'type': 'string', 'enum': codes}},
        'required': ['id', 'group'], 'additionalProperties': False}}},
        'required': ['results'], 'additionalProperties': False}
    group_list = '\n'.join(f'- {k}: {v}' for k, v in groups.items())
    t0 = time.time()
    resp = client.chat.completions.create(
        model=model, temperature=0, reasoning_effort='low',
        messages=[{'role': 'system', 'content': f'{SYSTEM_CLASSIFY}\n\nCác nhóm:\n{group_list}'},
                  {'role': 'user', 'content': json.dumps(items, ensure_ascii=False)}],
        response_format={'type': 'json_schema', 'json_schema': {'name': 'classification', 'strict': True, 'schema': schema}},
    )
    out = {r['id']: r['group'] for r in json.loads(resp.choices[0].message.content)['results']}
    dt = (time.time() - t0) / max(len(items), 1)
    usage = getattr(resp, 'usage', None)
    return [{'id': it['id'], 'group': out.get(it['id'], 'UNKNOWN'), 'llm_time_s': dt,
             'tokens': (getattr(usage, 'total_tokens', 0) or 0) / max(len(items), 1)} for it in items]

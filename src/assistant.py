"""Trợ lý tra cứu cho kế toán: LLM (Groq) gọi tool để lấy số liệu từ hệ thống.

- Mọi số liệu đến từ tool (code), LLM không tự đưa ra số liệu.
- Tạo yêu cầu review gồm 2 bước: LLM chỉ ĐỀ XUẤT (propose_review_request); yêu cầu chỉ được lưu khi
  người dùng xác nhận qua ReviewStore.confirm() – hàm này không được đưa cho LLM.
"""
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

from src.llm import MODEL_EXPLAIN, chat, get_client

ROOT = Path(__file__).resolve().parents[1]
REVIEW_FILE = ROOT / 'data' / 'processed' / 'review_requests.jsonl'
MAX_STEPS = 6
# Bản miễn phí của Groq giới hạn 8.000 token cho một request: giới hạn kết quả công cụ và lịch sử hội thoại gửi kèm
TOOL_RESULT_CHARS = 4000
HISTORY_CHARS = 14000
MAX_HISTORY_LIMIT = 20
ROUTES = ('ke_toan', 'quan_ly')

SYSTEM = """Bạn là trợ lý tra cứu cho kế toán kiểm soát chi tiêu thẻ mua sắm. Trả lời bằng tiếng Việt, ngắn gọn.
- Mọi số liệu (số tiền, ngân sách, lịch sử, vi phạm, mức rủi ro) phải lấy từ tool. Không tự suy đoán số liệu.
- Không tự cộng, trừ, so sánh ngày hay tính tỷ lệ: dùng các trường đã được tính sẵn (vd remaining, ratio). Nếu tool không có
  con số cần thiết thì nói rõ là hệ thống chưa cung cấp, không tự tính.
- Khi trả lời về quy định, dùng tool search_policy và nêu mã điều (ví dụ "Điều 5 (D05)").
- Không tự thay đổi kết luận vi phạm hay mức rủi ro của hệ thống.
- Khi người dùng muốn tạo yêu cầu review, gọi propose_review_request. Yêu cầu CHƯA được tạo cho tới khi người dùng
  bấm xác nhận; hãy nói rõ điều này, không được nói là đã tạo xong."""


def _fn(name, desc, props, required):
    return {'type': 'function', 'function': {'name': name, 'description': desc, 'parameters': {
        'type': 'object', 'properties': props, 'required': required, 'additionalProperties': False}}}


TOOLS = [
    _fn('lookup_transaction', 'Tra cứu hồ sơ một giao dịch: thông tin, nhóm chi phí, vi phạm rule, dấu hiệu bất thường, ngân sách, mức rủi ro.',
        {'txn_id': {'type': 'string', 'description': 'Mã giao dịch, ví dụ 202605-010177'}}, ['txn_id']),
    _fn('get_cardholder_history', 'Lịch sử chi tiêu trước đó của người chi của một giao dịch.',
        {'txn_id': {'type': 'string'}, 'limit': {'type': 'integer', 'description': 'Số giao dịch gần nhất (mặc định 10)'}}, ['txn_id']),
    _fn('check_budget', 'Ngân sách tháng và số đã chi của một đơn vị cho một nhóm chi phí.',
        {'agency_id': {'type': 'string'}, 'expense_group': {'type': 'string', 'description': 'Mã nhóm, ví dụ LODGING'},
         'month': {'type': 'string', 'description': 'YYYY-MM'}}, ['agency_id', 'expense_group', 'month']),
    _fn('search_policy', 'Tìm các điều quy chế chi tiêu liên quan tới một câu hỏi.',
        {'query': {'type': 'string'}}, ['query']),
    _fn('propose_review_request', 'Đề xuất tạo yêu cầu review cho một giao dịch. Cần người dùng xác nhận mới được tạo.',
        {'txn_id': {'type': 'string'}, 'reason': {'type': 'string'},
         'route_to': {'type': 'string', 'enum': ['ke_toan', 'quan_ly']},
         'required_documents': {'type': 'array', 'items': {'type': 'string'}}},
        ['txn_id', 'reason', 'route_to', 'required_documents']),
]


def validate_proposal(args: dict) -> dict:
    """Tham số của propose_review_request do LLM sinh ra: kiểm tra đủ trường, đúng kiểu, nơi nhận hợp lệ; bỏ khóa lạ."""
    req = {k: args.get(k) for k in ('txn_id', 'reason', 'route_to', 'required_documents')}
    if not isinstance(req['txn_id'], str) or not req['txn_id'].strip():
        raise ValueError('Thiếu mã giao dịch (txn_id)')
    if not isinstance(req['reason'], str) or not req['reason'].strip():
        raise ValueError('Thiếu lý do (reason)')
    if req['route_to'] not in ROUTES:
        raise ValueError(f'route_to phải là một trong {ROUTES}')
    docs = req['required_documents'] or []
    if not isinstance(docs, list) or not all(isinstance(d, str) for d in docs):
        raise ValueError('required_documents phải là danh sách chuỗi')
    req['required_documents'] = docs
    return req


class ReviewStore:
    def __init__(self, path: Path = REVIEW_FILE):
        self.path = path
        self.pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    def propose(self, **req) -> dict:
        req = validate_proposal(req)
        pid = uuid.uuid4().hex[:8]
        self.pending[pid] = {**req, 'proposal_id': pid, 'status': 'Chờ xác nhận'}
        return self.pending[pid]

    def take(self, proposal_id: str) -> dict:
        """Lấy đề xuất ra khỏi danh sách chờ (một lần duy nhất kể cả khi bấm đúp). KeyError nếu không còn."""
        with self._lock:
            return self.pending.pop(proposal_id)

    def put_back(self, proposal_id: str, req: dict):
        with self._lock:
            self.pending.setdefault(proposal_id, req)

    def confirm(self, proposal_id: str, user: str) -> dict:
        """Chỉ gọi từ giao diện khi người dùng bấm xác nhận."""
        req = self.take(proposal_id)
        try:
            done = {**req, 'status': 'Chờ review', 'confirmed_by': user,
                    'created_at': datetime.now().isoformat(timespec='seconds')}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(done, ensure_ascii=False) + '\n')
            return done
        except Exception:
            self.put_back(proposal_id, req)          # ghi lỗi thì đề xuất vẫn còn để xác nhận lại
            raise

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines() if line.strip()]


class Assistant:
    def __init__(self, engine, retriever, store: ReviewStore | None = None, client=None, model: str = MODEL_EXPLAIN):
        self.engine, self.retriever = engine, retriever
        self.store = store or ReviewStore()
        self.client, self.model = client, model
        self.messages = [{'role': 'system', 'content': SYSTEM}]

    # ------------------------------------------------------------ thực thi tool (code, không phải LLM)
    def run_tool(self, name: str, args: dict):
        if name == 'lookup_transaction':
            return self.engine.case(args['txn_id'])
        if name == 'get_cardholder_history':
            return self.engine.cardholder_history(args['txn_id'], max(1, min(int(args.get('limit') or 10), MAX_HISTORY_LIMIT)))
        if name == 'check_budget':
            return self.engine.budget(args['agency_id'], args['expense_group'], args['month']) or {'khong_ap_dung': 'Đơn vị không có ngân sách tháng riêng cho nhóm chi phí này (nhóm chi không thường xuyên), nên không xét quy tắc vượt ngân sách. Đây không phải dấu hiệu rủi ro.'}
        if name == 'search_policy':
            return [{'id': c.id, 'title': c.title, 'text': c.text} for c in self.retriever.search(args['query'], k=4)]
        if name == 'propose_review_request':
            self.engine.row(args['txn_id'])                       # kiểm tra giao dịch tồn tại
            return self.store.propose(**args)
        raise ValueError(f'Tool không tồn tại: {name}')

    def _trim_history(self, budget: int = HISTORY_CHARS):
        """Bỏ các lượt hỏi đáp cũ nhất cho tới khi lịch sử gửi kèm nằm trong giới hạn; luôn giữ lời dặn hệ thống và
        lượt hỏi hiện tại."""
        size = lambda m: len(m.get('content') or '') + len(json.dumps(m.get('tool_calls', ''), default=str))  # noqa: E731
        while sum(size(m) for m in self.messages[1:]) > budget:
            users = [i for i, m in enumerate(self.messages) if m['role'] == 'user']
            if len(users) < 2:
                break
            del self.messages[users[0]:users[1]]

    def _chat(self):
        self._trim_history()
        try:
            return chat(self.client, model=self.model, messages=self.messages, tools=TOOLS, tool_choice='auto', temperature=0.2)
        except RuntimeError:
            raise
        except Exception as e:
            if '413' not in str(e) and 'too large' not in str(e).lower():
                raise
            self._trim_history(budget=HISTORY_CHARS // 3)       # request quá lớn: chỉ giữ lượt hiện tại rồi thử lại
            try:
                return chat(self.client, model=self.model, messages=self.messages, tools=TOOLS, tool_choice='auto',
                            temperature=0.2)
            except RuntimeError:
                raise
            except Exception as e2:
                raise RuntimeError('Câu hỏi và dữ liệu tra cứu quá dài cho một lượt trả lời; hãy hỏi gọn hơn '
                                   'hoặc mở phiên trợ lý mới.') from e2

    def ask(self, question: str) -> dict:
        self.client = self.client or get_client()
        self.messages.append({'role': 'user', 'content': question})
        trace, before = [], set(self.store.pending)
        new = lambda: [p for k, p in self.store.pending.items() if k not in before]  # noqa: E731  chỉ đề xuất của lượt này
        for _ in range(MAX_STEPS):
            resp = self._chat()
            msg = resp.choices[0].message
            calls = msg.tool_calls or []
            self.messages.append({'role': 'assistant', 'content': msg.content or '',
                                  **({'tool_calls': [c.model_dump() if hasattr(c, 'model_dump') else c for c in calls]} if calls else {})})
            if not calls:
                return {'answer': msg.content, 'tool_calls': trace, 'pending_reviews': new()}
            for call in calls:
                args = {}
                try:
                    args = json.loads(call.function.arguments or '{}')
                    result = self.run_tool(call.function.name, args)
                except Exception as e:                            # lỗi tool trả lại cho LLM, không làm hỏng vòng lặp
                    result = {'error': str(e)}
                trace.append({'tool': call.function.name, 'args': args})
                self.messages.append({'role': 'tool', 'tool_call_id': call.id, 'name': call.function.name,
                                      'content': json.dumps(result, ensure_ascii=False, default=str)[:TOOL_RESULT_CHARS]})
        return {'answer': 'Không hoàn thành được trong số bước cho phép.', 'tool_calls': trace, 'pending_reviews': new()}

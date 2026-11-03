"""Các dịch vụ nặng (CaseEngine, truy xuất quy chế, LLM) – nạp một lần, dùng chung cho mọi request."""
import threading
import time

from src.assistant import Assistant, ReviewStore

from app.db import SessionLocal
from app.models import AuditLog, ReviewRequest


class DBReviewStore(ReviewStore):
    """Như ReviewStore nhưng khi người dùng xác nhận thì ghi vào bảng review_requests."""

    def confirm(self, proposal_id: str, user: str, case_id: str | None = None) -> dict:
        req = self.pending.pop(proposal_id)
        with SessionLocal() as s:
            rr = ReviewRequest(case_id=case_id, txn_id=req['txn_id'], reason=req['reason'], route_to=req['route_to'],
                               required_documents=req['required_documents'], source='tro_ly', created_by=user)
            s.add(rr)
            s.add(AuditLog(case_id=case_id, user=user, action='tao_yeu_cau_review', detail=req))
            s.commit()
            return {**req, 'id': rr.id, 'status': rr.status, 'confirmed_by': user}


class Services:
    def __init__(self):
        self.engine = None
        self.retriever = None
        self.error = None
        self.loading_since = None
        self.chats: dict[str, Assistant] = {}
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.engine is not None and self.retriever is not None

    def load(self):
        from src.case import CaseEngine
        from src.rag import PolicyRetriever
        self.loading_since = time.time()
        try:
            self.retriever = PolicyRetriever()
            self.engine = CaseEngine()
        except Exception as e:                       # lỗi nạp hiển thị ở /api/health
            self.error = repr(e)

    def load_in_background(self):
        threading.Thread(target=self.load, daemon=True).start()

    def chat(self, session_id: str) -> Assistant:
        with self._lock:
            if session_id not in self.chats:
                self.chats[session_id] = Assistant(self.engine, self.retriever, store=DBReviewStore())
            return self.chats[session_id]


services = Services()

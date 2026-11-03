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
        self._calibration = None

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

    @property
    def calibration(self):
        from app.calibration import CalibrationService
        with self._lock:
            if self._calibration is None:
                self._calibration = CalibrationService(self.engine)
            return self._calibration

    def load_in_background(self):
        threading.Thread(target=self.load, daemon=True).start()

    def chat(self, session_id: str) -> Assistant:
        with self._lock:
            if session_id not in self.chats:
                self.chats[session_id] = Assistant(self.engine, self.retriever, store=DBReviewStore())
            return self.chats[session_id]


services = Services()


# ---------------------------------------------------------------- agent điều tra chạy nền
import uuid as _uuid
from datetime import datetime as _dt

from sqlalchemy import select as _select

from app.models import Decision, Explanation

SIMULATED_ACTION = 'phan_hoi_mo_phong'   # phản hồi do scripts/simulate_feedback.py sinh ra, không phải kết luận thật


def past_decisions(signal: str) -> dict:
    """Kết luận trước đây của kế toán cho một loại cảnh báo (mã rule hoặc 'mo_hinh') – dùng cho công cụ của agent."""
    with SessionLocal() as s:
        q = _select(Decision).where(Decision.action != SIMULATED_ACTION).order_by(Decision.id.desc())
        rows = [d for d in s.execute(q).scalars() if signal in (d.signal_feedback or {})]
    verdicts = [d.signal_feedback[signal] for d in rows]
    return {'signal': signal, 'so_lan_danh_gia': len(rows), 'canh_bao_dung': verdicts.count('dung'),
            'canh_bao_gia': verdicts.count('canh_bao_gia'),
            'ghi_chu_gan_day': [d.note for d in rows if d.note][:3],
            'ghi_chu': None if rows else 'Chưa có kết luận nào cho loại cảnh báo này'}


class InvestigationJobs:
    def __init__(self):
        self.jobs: dict[str, dict] = {}

    def start(self, services_, case_id: str, txn_id: str, allowed: list[str], labels: dict, user: str) -> str:
        from src.agent import InvestigationAgent
        job_id = _uuid.uuid4().hex[:10]
        job = self.jobs[job_id] = {'id': job_id, 'case_id': case_id, 'status': 'dang_chay', 'phase': 'lap_ke_hoach',
                                   'plan': [], 'trace': [], 'result': None, 'error': None,
                                   'started_at': _dt.now().isoformat(timespec='seconds')}

        def on_event(kind, data):
            if kind == 'plan':
                job['plan'], job['phase'] = data, 'dieu_tra'
            elif kind == 'step':
                job['trace'].append(data)
            elif kind == 'writing':
                job['phase'] = 'viet_ho_so'

        def work():
            try:
                agent = InvestigationAgent(services_.engine, services_.retriever, decisions_fn=past_decisions)
                out = agent.run(txn_id, allowed, labels, on_event=on_event)
                with SessionLocal() as s:
                    s.add(Explanation(case_id=case_id, kind='dieu_tra', model=out['model'], payload=out))
                    s.add(AuditLog(case_id=case_id, user=user, action='agent_dieu_tra',
                                   detail={'tool_calls': out['n_tool_calls'], 'revisions': out['revisions']}))
                    s.commit()
                job.update(status='xong', phase='xong', result=out)
            except Exception as e:
                job.update(status='loi', error=str(e))

        threading.Thread(target=work, daemon=True).start()
        return job_id


jobs = InvestigationJobs()

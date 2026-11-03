"""API của ứng dụng hỗ trợ kiểm tra giao dịch. Chạy: .venv/Scripts/python -m uvicorn app.main:app --reload"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import case as case_expr
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import Base, engine, get_session
from app.models import AuditLog, Case, Decision, Explanation, ReviewRequest, RuleHit, Transaction
from app.services import services
from app.workflow import ACTIONS, CONCLUSIONS, SIGNAL_VERDICTS, STATUSES, WorkflowError, allowed_actions, apply_action
from src.labels import GROUPS
from src.rules import RULE_NAMES

LEVEL_ORDER = {'Cảnh báo cao': 0, 'Cần review': 1}
ROLES = {'ke_toan': 'Kế toán', 'quan_ly': 'Quản lý'}


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    if not getattr(app.state, 'skip_services', False):
        services.load_in_background()
    yield


app = FastAPI(title='Hỗ trợ kiểm tra giao dịch', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'],
                   allow_methods=['*'], allow_headers=['*'])


def need_services():
    if services.error:
        raise HTTPException(500, f'Lỗi nạp mô hình: {services.error}')
    if not services.ready:
        raise HTTPException(503, 'Hệ thống đang nạp dữ liệu và mô hình, vui lòng thử lại sau ít giây')
    return services


def get_case(s: Session, case_id: str) -> Case:
    case = s.get(Case, case_id)
    if case is None:
        raise HTTPException(404, f'Không tìm thấy hồ sơ {case_id}')
    return case


def case_row(c: Case) -> dict:
    return {'id': c.id, 'kind': c.kind, 'cardholder_id': c.cardholder_id, 'agency': c.agency,
            'tx_date': c.tx_date.isoformat(), 'month': c.month, 'n_txn': c.n_txn, 'total_amount': c.total_amount,
            'level': c.level, 'rules': [r for r in c.rules.split(',') if r], 'has_anomaly': c.has_anomaly,
            'title': c.title, 'status': c.status, 'assignee': c.assignee,
            'updated_at': c.updated_at.isoformat() if c.updated_at else None}


# ---------------------------------------------------------------- thông tin chung
@app.get('/api/health')
def health():
    return {'ok': True, 'services_ready': services.ready, 'error': services.error}


@app.get('/api/meta')
def meta(s: Session = Depends(get_session)):
    months = [m for (m,) in s.execute(select(Case.month).distinct().order_by(Case.month.desc()))]
    return {'statuses': STATUSES, 'roles': ROLES, 'rules': RULE_NAMES, 'groups': GROUPS, 'months': months,
            'conclusions': CONCLUSIONS, 'signal_verdicts': SIGNAL_VERDICTS,
            'actions': {k: {'label': a.label, 'role': a.role} for k, a in ACTIONS.items()}}


@app.get('/api/stats')
def stats(month: str | None = None, s: Session = Depends(get_session)):
    q = select(Case.level, Case.status, Case.kind, func.count()).group_by(Case.level, Case.status, Case.kind)
    tq = select(func.count(), func.sum(Transaction.amount))
    if month:
        q, tq = q.where(Case.month == month), tq.where(Transaction.month == month)
    rows = s.execute(q).all()
    n_tx, total = s.execute(tq).one()
    by_month = s.execute(select(Case.month, Case.level, func.count()).group_by(Case.month, Case.level)
                         .order_by(Case.month)).all()
    return {
        'transactions': n_tx, 'amount': total or 0,
        'cases': sum(r[3] for r in rows),
        'by_level': {lv: sum(r[3] for r in rows if r[0] == lv) for lv in LEVEL_ORDER},
        'by_status': {st: sum(r[3] for r in rows if r[1] == st) for st in STATUSES},
        'by_kind': {k: sum(r[3] for r in rows if r[2] == k) for k in ('giao_dich', 'cum_mua_don')},
        'by_month': [{'month': m, 'level': lv, 'n': n} for m, lv, n in by_month],
    }


# ---------------------------------------------------------------- hồ sơ
@app.get('/api/cases')
def list_cases(level: str | None = None, status: str | None = None, kind: str | None = None,
               month: str | None = None, rule: str | None = None, q: str | None = None,
               page: int = Query(1, ge=1), size: int = Query(25, ge=1, le=200), s: Session = Depends(get_session)):
    stmt = select(Case)
    for col, val in [(Case.level, level), (Case.status, status), (Case.kind, kind), (Case.month, month)]:
        if val:
            stmt = stmt.where(col == val)
    if rule:
        stmt = stmt.where(Case.rules.contains(rule))
    if q:
        like = f'%{q}%'
        stmt = stmt.where(or_(Case.title.ilike(like), Case.cardholder_id.ilike(like), Case.agency.ilike(like),
                              Case.id.ilike(like)))
    total = s.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    level_rank = case_expr((Case.level == 'Cảnh báo cao', 0), else_=1)     # không sắp theo chữ: "Cần" > "Cảnh"
    rows = s.execute(stmt.order_by(level_rank, Case.tx_date.desc(), Case.total_amount.desc())
                     .offset((page - 1) * size).limit(size)).scalars().all()
    return {'total': total, 'page': page, 'size': size, 'items': [case_row(c) for c in rows]}


@app.get('/api/cases/{case_id:path}/detail')
def case_detail(case_id: str, role: str = 'ke_toan', s: Session = Depends(get_session)):
    case = get_case(s, case_id)
    txns = s.execute(select(Transaction).where(Transaction.case_id == case_id)
                     .order_by(Transaction.tx_date, Transaction.txn_id)).scalars().all()
    ids = [t.txn_id for t in txns]
    hits = s.execute(select(RuleHit).where(RuleHit.txn_id.in_(ids))).scalars().all()
    hits_by = {}
    for h in hits:
        hits_by.setdefault(h.txn_id, []).append({'rule_id': h.rule_id, 'rule_name': RULE_NAMES[h.rule_id],
                                                 'severity': h.severity, 'message': h.message,
                                                 'related_txn_ids': h.related_txn_ids})
    analysis = None
    if services.ready and txns:
        analysis = services.engine.case(representative_txn(txns, hits_by))
    expl = s.execute(select(Explanation).where(Explanation.case_id == case_id)
                     .order_by(Explanation.id.desc())).scalars().all()
    decisions = s.execute(select(Decision).where(Decision.case_id == case_id).order_by(Decision.id)).scalars().all()
    reviews = s.execute(select(ReviewRequest).where(ReviewRequest.case_id == case_id)).scalars().all()
    return {
        'case': case_row(case),
        'allowed_actions': allowed_actions(case, role),
        'transactions': [{'txn_id': t.txn_id, 'tx_date': t.tx_date.isoformat(), 'post_date': t.post_date.isoformat(),
                          'merchant': t.merchant, 'item_desc': t.item_desc, 'amount': t.amount, 'mcc_desc': t.mcc_desc,
                          'expense_group': t.expense_group, 'final_level': t.final_level,
                          'anomaly_tx_flag': t.anomaly_tx_flag, 'rule_hits': hits_by.get(t.txn_id, [])} for t in txns],
        'analysis': analysis,
        'explanations': [{'id': e.id, 'kind': e.kind, 'model': e.model, 'created_at': e.created_at.isoformat(),
                          **e.payload} for e in expl],
        'decisions': [{'user': d.user, 'role': d.role, 'action': d.action, 'from': d.from_status, 'to': d.to_status,
                       'conclusion': d.conclusion, 'signal_feedback': d.signal_feedback, 'note': d.note,
                       'created_at': d.created_at.isoformat()} for d in decisions],
        'review_requests': [{'id': r.id, 'txn_id': r.txn_id, 'reason': r.reason, 'route_to': r.route_to,
                             'required_documents': r.required_documents, 'status': r.status, 'source': r.source,
                             'created_by': r.created_by} for r in reviews],
    }


def representative_txn(txns, hits_by) -> str:
    """Giao dịch đại diện của hồ sơ: nhiều vi phạm mức Cao nhất, rồi số tiền lớn nhất."""
    sev = {'Cao': 3, 'Trung bình': 2, 'Thấp': 1}
    return max(txns, key=lambda t: (sum(sev[h['severity']] for h in hits_by.get(t.txn_id, [])), t.amount)).txn_id


class ExplainIn(BaseModel):
    force: bool = False
    user: str = 'ke_toan_1'


@app.post('/api/cases/{case_id:path}/explain')
def explain(case_id: str, body: ExplainIn, s: Session = Depends(get_session), svc=Depends(need_services)):
    from src.llm import explain_case
    case = get_case(s, case_id)
    if not body.force:
        cached = s.execute(select(Explanation).where(Explanation.case_id == case_id, Explanation.kind == 'giai_thich')
                           .order_by(Explanation.id.desc())).scalars().first()
        if cached:
            return {'cached': True, **cached.payload}
    txns = s.execute(select(Transaction).where(Transaction.case_id == case_id)).scalars().all()
    hits_by = {}
    for h in s.execute(select(RuleHit).where(RuleHit.txn_id.in_([t.txn_id for t in txns]))).scalars():
        hits_by.setdefault(h.txn_id, []).append({'severity': h.severity})
    c = svc.engine.case(representative_txn(txns, hits_by))
    chunks = svc.retriever.retrieve_for_case(svc.engine.retrieval_query(c), rule_ids=[h['rule_id'] for h in c['rule_hits']],
                                             signals=c['anomaly']['signals'], groups=[c['expense_group']['group']])
    try:
        out = explain_case(c, chunks)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    out['articles'] = {ch.id: {'title': ch.title, 'text': ch.text} for ch in chunks}
    s.add(Explanation(case_id=case.id, kind='giai_thich', model=out['model'], payload=out))
    s.add(AuditLog(case_id=case.id, user=body.user, action='sinh_giai_thich', detail={'model': out['model']}))
    s.commit()
    return {'cached': False, **out}


class ActionIn(BaseModel):
    action: str
    user: str
    role: str
    note: str = ''
    conclusion: str | None = None
    signal_feedback: dict[str, str] = Field(default_factory=dict)


@app.post('/api/cases/{case_id:path}/actions')
def do_action(case_id: str, body: ActionIn, s: Session = Depends(get_session)):
    case = get_case(s, case_id)
    try:
        apply_action(s, case, body.action, body.user, body.role, body.note, body.conclusion, body.signal_feedback)
    except WorkflowError as e:
        raise HTTPException(409, str(e))
    s.commit()
    return {'case': case_row(case), 'allowed_actions': allowed_actions(case, body.role)}


# ---------------------------------------------------------------- trợ lý hỏi đáp
class ChatIn(BaseModel):
    session_id: str
    message: str
    user: str = 'ke_toan_1'


@app.post('/api/chat')
def chat(body: ChatIn, svc=Depends(need_services)):
    asst = svc.chat(body.session_id)
    try:
        return asst.ask(body.message)
    except RuntimeError as e:
        raise HTTPException(503, str(e))


class ConfirmIn(BaseModel):
    session_id: str
    user: str


@app.post('/api/review-proposals/{proposal_id}/confirm')
def confirm_proposal(proposal_id: str, body: ConfirmIn, s: Session = Depends(get_session), svc=Depends(need_services)):
    asst = svc.chats.get(body.session_id)
    if asst is None or proposal_id not in asst.store.pending:
        raise HTTPException(404, 'Không tìm thấy đề xuất (có thể đã xác nhận hoặc phiên đã hết)')
    txn = s.get(Transaction, asst.store.pending[proposal_id]['txn_id'])
    return asst.store.confirm(proposal_id, user=body.user, case_id=txn.case_id if txn else None)


@app.get('/api/review-requests')
def review_requests(s: Session = Depends(get_session)):
    rows = s.execute(select(ReviewRequest).order_by(ReviewRequest.id.desc()).limit(200)).scalars().all()
    return [{'id': r.id, 'case_id': r.case_id, 'txn_id': r.txn_id, 'reason': r.reason, 'route_to': r.route_to,
             'required_documents': r.required_documents, 'status': r.status, 'source': r.source,
             'created_by': r.created_by, 'created_at': r.created_at.isoformat()} for r in rows]


@app.get('/api/transactions/{txn_id}')
def transaction(txn_id: str, s: Session = Depends(get_session)):
    t = s.get(Transaction, txn_id)
    if t is None:
        raise HTTPException(404, f'Không tìm thấy giao dịch {txn_id}')
    return {c.name: getattr(t, c.name) for c in Transaction.__table__.columns}


# ---------------------------------------------------------------- agent điều tra (hướng A)
from app.services import jobs  # noqa: E402


class InvestigateIn(BaseModel):
    user: str
    role: str = 'ke_toan'


@app.post('/api/cases/{case_id:path}/investigate')
def investigate(case_id: str, body: InvestigateIn, s: Session = Depends(get_session), svc=Depends(need_services)):
    case = get_case(s, case_id)
    running = [j for j in jobs.jobs.values() if j['case_id'] == case_id and j['status'] == 'dang_chay']
    if running:
        return {'job_id': running[0]['id']}
    txns = s.execute(select(Transaction).where(Transaction.case_id == case_id)).scalars().all()
    hits_by = {}
    for h in s.execute(select(RuleHit).where(RuleHit.txn_id.in_([t.txn_id for t in txns]))).scalars():
        hits_by.setdefault(h.txn_id, []).append({'severity': h.severity})
    # Agent đề xuất HƯỚNG KẾT LUẬN: các thao tác của bước "Đang review" (Điều 6: cảnh báo cao không được Chấp nhận)
    allowed = [k for k, a in ACTIONS.items() if a.from_status == 'Đang review' and a.role == 'ke_toan'
               and not (k == 'chap_nhan' and case.level == 'Cảnh báo cao')]
    labels = {k: a.label for k, a in ACTIONS.items()}
    job_id = jobs.start(svc, case_id, representative_txn(txns, hits_by), allowed, labels, body.user)
    s.add(AuditLog(case_id=case_id, user=body.user, action='bat_dau_dieu_tra', detail={'job_id': job_id}))
    s.commit()
    return {'job_id': job_id}


@app.get('/api/jobs/{job_id}')
def job_status(job_id: str):
    job = jobs.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, 'Không tìm thấy phiên điều tra (máy chủ có thể đã khởi động lại)')
    return job


class ReviewFromDraftIn(BaseModel):
    user: str
    txn_id: str
    reason: str
    route_to: str
    required_documents: list[str] = Field(default_factory=list)


@app.post('/api/cases/{case_id:path}/review-requests')
def create_review_request(case_id: str, body: ReviewFromDraftIn, s: Session = Depends(get_session)):
    """Người dùng bấm xác nhận bản nháp yêu cầu review do agent soạn."""
    get_case(s, case_id)
    if body.route_to not in ('ke_toan', 'quan_ly'):
        raise HTTPException(422, 'Nơi nhận không hợp lệ')
    rr = ReviewRequest(case_id=case_id, txn_id=body.txn_id, reason=body.reason, route_to=body.route_to,
                       required_documents=body.required_documents, source='agent', created_by=body.user)
    s.add(rr)
    s.add(AuditLog(case_id=case_id, user=body.user, action='tao_yeu_cau_review', detail=body.model_dump()))
    s.commit()
    return {'id': rr.id, 'status': rr.status}

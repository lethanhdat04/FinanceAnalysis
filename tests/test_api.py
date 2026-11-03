"""Kiểm thử API và quy trình xử lý hồ sơ trên SQLite tạm (không nạp mô hình, không gọi LLM)."""
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.main import app
from app.models import Case, RuleHit, Transaction


@pytest.fixture()
def client():
    eng = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, expire_on_commit=False)
    with Session() as s:
        for cid, level, rules, amount, d in [('T|t1', 'Cảnh báo cao', 'R02', 9000, date(2026, 6, 2)),
                                             ('T|t2', 'Cần review', '', 300, date(2026, 6, 3))]:
            s.add(Case(id=cid, kind='giao_dich', cardholder_id='A|X|Y', agency='OSU', tx_date=d, month='2026-06',
                       n_txn=1, total_amount=amount, level=level, rules=rules, has_anomaly=not rules,
                       title=f'Cửa hàng {cid}'))
            tid = cid[2:]
            s.add(Transaction(txn_id=tid, month='2026-06', tx_date=d, post_date=d, agency_id='01000', agency='OSU',
                              cardholder_id='A|X|Y', merchant='SHOP', merchant_norm='SHOP', item_desc='GENERAL PURCHASE',
                              amount=amount, mcc_desc='MISC', expense_group='OFFICE_PRINT', rule_level=level,
                              final_level=level, case_id=cid))
        s.add(RuleHit(txn_id='t1', rule_id='R02', severity='Cao', message='Vượt hạn mức', related_txn_ids=''))
        s.commit()

    def override():
        with Session() as s:
            yield s
    app.dependency_overrides[get_session] = override
    app.state.skip_services = True
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_list_orders_high_alerts_first_and_filters(client):
    r = client.get('/api/cases').json()
    assert r['total'] == 2 and [x['level'] for x in r['items']] == ['Cảnh báo cao', 'Cần review']
    assert client.get('/api/cases', params={'rule': 'R02'}).json()['total'] == 1
    assert client.get('/api/cases', params={'q': 'T|t2'}).json()['items'][0]['id'] == 'T|t2'


def test_detail_contains_transactions_rule_hits_and_actions(client):
    d = client.get('/api/cases/T|t1/detail', params={'role': 'ke_toan'}).json()
    assert d['transactions'][0]['rule_hits'][0]['rule_id'] == 'R02'
    assert d['allowed_actions'] == ['nhan_xu_ly'] and d['analysis'] is None


def test_high_alert_cannot_be_accepted_by_accountant(client):
    base = {'user': 'kt1', 'role': 'ke_toan'}
    assert client.post('/api/cases/T|t1/actions', json={**base, 'action': 'nhan_xu_ly'}).status_code == 200
    r = client.post('/api/cases/T|t1/actions', json={**base, 'action': 'chap_nhan'})
    assert r.status_code == 409 and 'Điều 6' in r.json()['detail']
    r = client.post('/api/cases/T|t1/actions', json={**base, 'action': 'chuyen_phe_duyet'})
    assert r.json()['case']['status'] == 'Chờ phê duyệt'
    r = client.post('/api/cases/T|t1/actions', json={'action': 'phe_duyet', 'user': 'kt1', 'role': 'ke_toan'})
    assert r.status_code == 409                                        # chỉ quản lý được phê duyệt
    r = client.post('/api/cases/T|t1/actions', json={'action': 'tu_choi', 'user': 'ql1', 'role': 'quan_ly'})
    assert r.status_code == 409 and 'lý do' in r.json()['detail']    # từ chối phải có lý do
    r = client.post('/api/cases/T|t1/actions', json={'action': 'tu_choi', 'user': 'ql1', 'role': 'quan_ly',
                                                    'note': 'Không có phê duyệt trước'})
    assert r.json()['case']['status'] == 'Từ chối'
    d = client.get('/api/cases/T|t1/detail').json()
    assert [x['to'] for x in d['decisions']] == ['Đang review', 'Chờ phê duyệt', 'Từ chối']
    assert d['decisions'][-1]['conclusion'] == 'vi_pham'


def test_review_case_can_be_accepted_with_signal_feedback(client):
    base = {'user': 'kt1', 'role': 'ke_toan'}
    client.post('/api/cases/T|t2/actions', json={**base, 'action': 'nhan_xu_ly'})
    r = client.post('/api/cases/T|t2/actions', json={**base, 'action': 'chap_nhan', 'note': 'Mua hội thảo',
                                                    'signal_feedback': {'mo_hinh': 'canh_bao_gia'}})
    assert r.json()['case']['status'] == 'Chấp nhận'
    bad = client.post('/api/cases/T|t2/actions', json={**base, 'action': 'nhan_xu_ly'})
    assert bad.status_code == 409


def test_invalid_feedback_rejected(client):
    base = {'user': 'kt1', 'role': 'ke_toan'}
    client.post('/api/cases/T|t2/actions', json={**base, 'action': 'nhan_xu_ly'})
    r = client.post('/api/cases/T|t2/actions', json={**base, 'action': 'chap_nhan', 'signal_feedback': {'R02': 'ok'}})
    assert r.status_code == 409


def test_services_not_ready_returns_503(client):
    r = client.post('/api/cases/T|t1/explain', json={})
    assert r.status_code == 503 and 'đang nạp' in r.json()['detail']


def test_stats_and_meta(client):
    st = client.get('/api/stats').json()
    assert st['cases'] == 2 and st['by_level'] == {'Cảnh báo cao': 1, 'Cần review': 1, 'Bình thường': 0}
    assert client.get('/api/meta').json()['months'] == ['2026-06']


def test_investigate_needs_services_and_review_from_draft(client):
    assert client.post('/api/cases/T|t1/investigate', json={'user': 'kt1'}).status_code == 503
    assert client.get('/api/jobs/khong-co').status_code == 404
    body = {'user': 'kt1', 'txn_id': 't1', 'reason': 'Nghi chia nhỏ', 'route_to': 'quan_ly', 'required_documents': ['Hóa đơn']}
    r = client.post('/api/cases/T|t1/review-requests', json=body)
    assert r.status_code == 200
    d = client.get('/api/cases/T|t1/detail').json()
    assert d['review_requests'][0]['source'] == 'agent'
    assert client.post('/api/cases/T|t1/review-requests', json={**body, 'route_to': 'ai_do'}).status_code == 422

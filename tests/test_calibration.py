"""Kiểm thử hiệu chỉnh theo phản hồi trong ứng dụng: chỉ quản lý được duyệt, duyệt thì hạ mức hồ sơ chờ review, gỡ thì khôi phục."""
from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.calibration import CalibrationError, CalibrationService
from app.db import Base
from app.models import AuditLog, Case, Decision, Transaction
from app.services import SIMULATED_ACTION

N = 30
CTX = dict(group='OFFICE_PRINT', amount_band='< $500', reach='≥ 5', repeat='0', agency_repeat='0', same_amount='0')
KEY = 'R07|reach=≥ 5'


@pytest.fixture()
def env():
    eng = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, expire_on_commit=False)
    rows = []
    with Session() as s:
        for i in range(N + 1):
            cid, tid = f'T|t{i}', f't{i}'
            status = 'Đang review' if i == N else 'Chờ review'        # hồ sơ đang xử lý không được đổi mức
            s.add(Case(id=cid, kind='giao_dich', cardholder_id='A|X|Y', agency='OSU', tx_date=date(2026, 6, 2),
                       month='2026-06', n_txn=1, total_amount=600, level='Cần review', rules='R07', has_anomaly=False,
                       title='Cửa hàng', status=status))
            s.add(Transaction(txn_id=tid, month='2026-06', tx_date=date(2026, 6, 2), post_date=date(2026, 6, 3),
                              agency_id='01000', agency='OSU', cardholder_id='A|X|Y', merchant='SHOP', merchant_norm='SHOP',
                              item_desc='Paper', amount=600, mcc_desc='MISC', expense_group='OFFICE_PRINT',
                              rule_level='Cần review', final_level='Cần review', case_id=cid))
            rows.append({'txn_id': tid, 'idx': i, 'month': pd.Period('2026-06', 'M'), 'signal': 'R07',
                         'severity': 'Trung bình', **CTX})
            if i < N:
                verdict = 'dung' if i < 2 else 'canh_bao_gia'
                s.add(Decision(case_id=cid, user='kt', role='ke_toan', action=SIMULATED_ACTION, from_status=status,
                               to_status=status, signal_feedback={'R07': verdict}))
        s.commit()
    svc = CalibrationService(engine=None)
    svc._signals = pd.DataFrame(rows)
    with Session() as s:
        yield svc, s


def levels_by_status(s):
    return {(c.status, c.level) for c in s.execute(select(Case)).scalars()}


def test_feedback_becomes_a_proposal_marked_as_simulated(env):
    svc, s = env
    st = svc.state(s)
    assert st['n_feedback'] == N and st['n_simulated'] == N
    p = next(x for x in st['proposals'] if x['key'] == KEY)
    assert (p['n'], p['n_true']) == (N, 2) and p['affected'] == N
    assert next(x for x in st['summary'] if x['signal'] == 'R07')['n'] == N


def test_only_manager_can_apply(env):
    svc, s = env
    with pytest.raises(CalibrationError):
        svc.decide(s, KEY, 'ap_dung', 'kt', 'ke_toan')


def test_apply_lowers_open_cases_and_lift_restores_them(env):
    svc, s = env
    out = svc.decide(s, KEY, 'ap_dung', 'ql', 'quan_ly', 'thử')
    assert out == {'key': KEY, 'status': 'dang_ap_dung', 'cases_changed': N}
    assert levels_by_status(s) == {('Chờ review', 'Bình thường'), ('Đang review', 'Cần review')}
    assert svc.for_case(s, ['t0'])[0]['key'] == KEY
    assert [a['key'] for a in svc.state(s)['active']] == [KEY]
    assert KEY not in {p['key'] for p in svc.state(s)['proposals']}

    out = svc.decide(s, KEY, 'go_bo', 'ql', 'quan_ly')
    assert out['cases_changed'] == N
    assert levels_by_status(s) == {('Chờ review', 'Cần review'), ('Đang review', 'Cần review')}
    logs = [a.action for a in s.execute(select(AuditLog)).scalars()]
    assert logs.count('hieu_chinh_doi_muc') == 2 * N and 'hieu_chinh_ap_dung' in logs and 'hieu_chinh_go_bo' in logs


def test_rejected_proposal_is_not_shown_again_and_cannot_be_lifted(env):
    svc, s = env
    svc.decide(s, KEY, 'tu_choi', 'ql', 'quan_ly', 'Nhà cung cấp này cần theo dõi')
    st = svc.state(s)
    assert KEY not in {p['key'] for p in st['proposals']} and st['history'][0]['status'] == 'tu_choi'
    assert levels_by_status(s) == {('Chờ review', 'Cần review'), ('Đang review', 'Cần review')}
    with pytest.raises(CalibrationError):
        svc.decide(s, KEY, 'go_bo', 'ql', 'quan_ly')

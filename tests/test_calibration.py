"""Kiểm thử hiệu chỉnh theo phản hồi trong ứng dụng: chỉ quản lý được duyệt, duyệt thì hạ mức hồ sơ chờ review, gỡ thì
khôi phục; hồ sơ đang xử lý chỉ được nâng mức; phản hồi thật được ưu tiên hơn phản hồi mô phỏng."""
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

N = 40            # hồ sơ có phản hồi mô phỏng (2 đúng, 38 giả)
EXTRA = 10        # hồ sơ cùng ngữ cảnh chưa có phản hồi
CTX = dict(group='OFFICE_PRINT', amount_band='< $500', reach='≥ 5', repeat='0', agency_repeat='0', same_amount='0')
KEY = 'R07|reach=≥ 5'


def add_case(s, cid, tid, status='Chờ review', level='Cần review'):
    s.add(Case(id=cid, kind='giao_dich', cardholder_id='A|X|Y', agency='OSU', tx_date=date(2026, 6, 2), month='2026-06',
               n_txn=1, total_amount=600, level=level, rules='R07', has_anomaly=False, title='Cửa hàng', status=status))
    s.add(Transaction(txn_id=tid, month='2026-06', tx_date=date(2026, 6, 2), post_date=date(2026, 6, 3), agency_id='01000',
                      agency='OSU', cardholder_id='A|X|Y', merchant='SHOP', merchant_norm='SHOP', item_desc='Paper',
                      amount=600, mcc_desc='MISC', expense_group='OFFICE_PRINT', rule_level=level, final_level=level,
                      case_id=cid))


def feedback(s, cid, verdict, simulated=True):
    s.add(Decision(case_id=cid, user='kt', role='ke_toan', action=SIMULATED_ACTION if simulated else 'chap_nhan',
                   from_status='Chờ review', to_status='Chờ review', signal_feedback={'R07': verdict}))


@pytest.fixture()
def env():
    eng = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, expire_on_commit=False)
    rows = []
    with Session() as s:
        for i in range(N + EXTRA):
            add_case(s, f'T|t{i}', f't{i}')
            rows.append({'txn_id': f't{i}', 'idx': i, 'month': pd.Period('2026-06', 'M'), 'signal': 'R07',
                         'severity': 'Trung bình', **CTX})
            if i < N:
                feedback(s, f'T|t{i}', 'dung' if i < 2 else 'canh_bao_gia')
        add_case(s, 'T|busy', 'busy', status='Đang review')        # đang xử lý, cùng ngữ cảnh
        rows.append({'txn_id': 'busy', 'idx': 999, 'month': pd.Period('2026-06', 'M'), 'signal': 'R07',
                     'severity': 'Trung bình', **CTX})
        add_case(s, 'T|other', 'other')                              # không có tín hiệu bị hiệu chỉnh tác động
        s.commit()
    svc = CalibrationService(engine=None)
    svc._signals = pd.DataFrame(rows)
    with Session() as s:
        yield svc, s


def level(s, cid):
    return s.get(Case, cid).level


def test_feedback_becomes_a_proposal_marked_as_simulated(env):
    svc, s = env
    st = svc.state(s)
    assert st['n_feedback'] == N and st['n_simulated'] == N
    p = next(x for x in st['proposals'] if x['key'] == KEY)
    assert (p['n'], p['n_true'], p['previous']) == (N, 2, None)
    assert p['affected'] == N + EXTRA                                 # tín hiệu cùng ngữ cảnh trong hồ sơ chờ review


def test_real_feedback_wins_over_simulated_for_the_same_case(env):
    svc, s = env
    feedback(s, 'T|t5', 'dung', simulated=False)                     # kế toán thật: đúng
    feedback(s, 'T|t6', 'dung', simulated=False)
    feedback(s, 'T|t6', 'canh_bao_gia', simulated=True)              # mô phỏng ghi sau vẫn không đè được
    fb = svc.feedback(s).set_index('case_id')
    assert bool(fb.loc['T|t5', 'label']) and bool(fb.loc['T|t6', 'label'])
    assert not fb.loc['T|t5', 'simulated']


def test_only_manager_can_apply(env):
    svc, s = env
    with pytest.raises(CalibrationError):
        svc.decide(s, KEY, 'ap_dung', 'kt', 'ke_toan')


def test_apply_lowers_only_open_cases_and_lift_restores_them(env):
    svc, s = env
    out = svc.decide(s, KEY, 'ap_dung', 'ql', 'quan_ly', 'thử')
    assert out == {'key': KEY, 'status': 'dang_ap_dung', 'cases_changed': N + EXTRA}
    assert level(s, 'T|t0') == 'Bình thường'
    assert level(s, 'T|busy') == 'Cần review'                         # đang xử lý: không hạ giữa chừng
    assert level(s, 'T|other') == 'Cần review'                        # không bị tác động: giữ mức gốc
    assert svc.for_case(s, ['t0'])[0]['key'] == KEY
    assert [a['key'] for a in svc.state(s)['active']] == [KEY]
    assert KEY not in {p['key'] for p in svc.state(s)['proposals']}

    out = svc.decide(s, KEY, 'go_bo', 'ql', 'quan_ly')
    assert out['cases_changed'] == N + EXTRA
    assert {level(s, c) for c in ['T|t0', 'T|busy', 'T|other']} == {'Cần review'}
    logs = [a.action for a in s.execute(select(AuditLog)).scalars()]
    assert logs.count('hieu_chinh_doi_muc') == 2 * (N + EXTRA) and 'hieu_chinh_ap_dung' in logs and 'hieu_chinh_go_bo' in logs


def test_lifting_raises_a_lowered_case_that_is_now_in_progress(env):
    # Điều 6: hồ sơ bị hạ mức rồi được nhận xử lý; gỡ hiệu chỉnh phải trả về mức gốc để quy tắc duyệt vẫn áp dụng
    svc, s = env
    svc.decide(s, KEY, 'ap_dung', 'ql', 'quan_ly')
    s.get(Case, 'T|t0').status = 'Đang review'
    svc.decide(s, KEY, 'go_bo', 'ql', 'quan_ly')
    assert level(s, 'T|t0') == 'Cần review'


def test_rejected_proposal_returns_only_with_more_feedback(env):
    svc, s = env
    svc.decide(s, KEY, 'tu_choi', 'ql', 'quan_ly', 'Nhà cung cấp này cần theo dõi')
    st = svc.state(s)
    assert KEY not in {p['key'] for p in st['proposals']} and st['history'][0]['status'] == 'tu_choi'
    assert level(s, 'T|t0') == 'Cần review'
    with pytest.raises(CalibrationError):
        svc.decide(s, KEY, 'go_bo', 'ql', 'quan_ly')

    for i in range(N, N + EXTRA):
        feedback(s, f'T|t{i}', 'canh_bao_gia')
    p = next(x for x in svc.state(s)['proposals'] if x['key'] == KEY)
    assert p['n'] == N + EXTRA and p['previous']['status'] == 'tu_choi' and p['previous']['n'] == N
    assert svc.decide(s, KEY, 'ap_dung', 'ql', 'quan_ly')['status'] == 'dang_ap_dung'

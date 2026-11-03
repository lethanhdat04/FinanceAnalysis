"""Ghi phản hồi MÔ PHỎNG của kế toán vào bảng decisions để demo trang Hiệu chỉnh.

Dữ liệu công khai không có kết luận thật của kế toán. Script này dùng "kế toán mô phỏng" của src/feedback_sim.py
(kịch bản "khop", nhầm lẫn 10%) để đánh giá tín hiệu của một số hồ sơ. Các dòng được ghi với
action = 'phan_hoi_mo_phong', user = 'ke_toan_mo_phong', KHÔNG đổi trạng thái hồ sơ, và agent không dùng chúng làm
bằng chứng. Trang Hiệu chỉnh hiển thị rõ số phản hồi mô phỏng.

Chạy:
    .venv/Scripts/python scripts/simulate_feedback.py --per-month 300
    .venv/Scripts/python scripts/simulate_feedback.py --clear      # xóa toàn bộ phản hồi mô phỏng
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from sqlalchemy import delete, func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Case, Decision, Transaction  # noqa: E402
from app.services import SIMULATED_ACTION  # noqa: E402

USER = 'ke_toan_mo_phong'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--per-month', type=int, default=300, help='số hồ sơ được "xem" mỗi tháng')
    ap.add_argument('--noise', type=float, default=0.1)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--clear', action='store_true')
    args = ap.parse_args()
    Base.metadata.create_all(engine)

    with SessionLocal() as s:
        n_old = s.execute(select(func.count()).where(Decision.action == SIMULATED_ACTION)).scalar()
        if args.clear:
            s.execute(delete(Decision).where(Decision.action == SIMULATED_ACTION))
            s.commit()
            print(f'Đã xóa {n_old} phản hồi mô phỏng')
            return
        if n_old:
            sys.exit(f'Đã có {n_old} phản hồi mô phỏng. Chạy --clear trước nếu muốn sinh lại.')

    from src.case import CaseEngine
    from src.feedback import BURST, MODEL, signal_table
    from src.feedback_sim import latent_truth, oracle_facts

    eng = CaseEngine()
    sig = signal_table(eng.df, eng.hits)
    sig['truth'] = latent_truth(sig, oracle_facts(eng.df), 'khop')
    rng = np.random.default_rng(args.seed)

    with SessionLocal() as s:
        cases = s.execute(select(Case.id, Case.month, Case.status)).all()
        txn_case = dict(s.execute(select(Transaction.txn_id, Transaction.case_id)
                                  .where(Transaction.case_id.is_not(None))).all())
        sig = sig[sig['txn_id'].isin(txn_case)]
        sig = sig.assign(case_id=sig['txn_id'].map(txn_case))
        by_case = {c: d for c, d in sig.groupby('case_id')}
        months = sorted({m for _, m, _ in cases})
        n = 0
        for m in months:
            ids = [(c, st) for c, mm, st in cases if mm == m]
            for i in rng.choice(len(ids), min(args.per_month, len(ids)), replace=False):
                case_id, status = ids[i]
                d = by_case.get(case_id)
                if d is None:
                    continue
                fb = {}
                for signal, g in d.groupby('signal'):
                    key = 'mo_hinh' if signal in (MODEL, BURST) else signal
                    label = bool(g['truth'].iloc[0]) ^ bool(rng.random() < args.noise)
                    fb[key] = 'dung' if label else 'canh_bao_gia'
                s.add(Decision(case_id=case_id, user=USER, role='ke_toan', action=SIMULATED_ACTION,
                               from_status=status, to_status=status, conclusion=None, signal_feedback=fb,
                               note='Phản hồi mô phỏng (không phải kết luận thật của kế toán)'))
                n += 1
        s.commit()
    print(f'Đã ghi {n} phản hồi mô phỏng cho {len(months)} tháng')


if __name__ == '__main__':
    main()

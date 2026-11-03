"""Hiệu chỉnh theo phản hồi trong ứng dụng: gom phản hồi từ bảng decisions, đề xuất, quản lý duyệt, tính lại mức hồ sơ.

Hệ thống chỉ đề xuất; một hiệu chỉnh chỉ có hiệu lực khi quản lý duyệt. Khi duyệt hoặc gỡ:
- hồ sơ "Chờ review" được đặt lại mức theo các hiệu chỉnh đang áp dụng;
- hồ sơ đang xử lý chỉ được NÂNG mức (không hạ giữa chừng việc của kế toán). Nhờ vậy gỡ một hiệu chỉnh trả hồ sơ đang
  xử lý về "Cảnh báo cao" nếu đó là mức gốc, và Điều 6 (cảnh báo cao phải do quản lý duyệt) không bị lách;
- hồ sơ đã kết thúc giữ nguyên.
Mức đích lấy từ mức gốc đã lưu khi nạp dữ liệu (transactions.final_level); chỉ hồ sơ có tín hiệu bị hiệu chỉnh tác động
mới được tính lại từ tín hiệu, nên đổi cấu hình rule / mô hình sau khi nạp không làm đổi mức hàng loạt.
"""
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Calibration, Case, Decision, Transaction
from app.workflow import FINAL
from app.services import SIMULATED_ACTION
from src.feedback import BURST, CALIBRATABLE, MODEL, MODEL_SIGNALS, PROTECTED, SIGNAL_NAMES, VERDICTS, Proposal, \
    RuleCalibrator, levels, signal_table
from src.rules import load_config

OPEN_STATUS = 'Chờ review'
RANK = {'Bình thường': 0, 'Cần review': 1, 'Cảnh báo cao': 2}


class CalibrationError(Exception):
    pass


class CalibrationService:
    def __init__(self, engine):
        self.engine = engine
        self._signals = None

    @property
    def signals(self) -> pd.DataFrame:
        if self._signals is None:          # ~5 giây, tính một lần
            self._signals = signal_table(self.engine.df, self.engine.hits)
        return self._signals

    @staticmethod
    def settings() -> dict:
        return load_config().get('feedback', {})

    # ------------------------------------------------------------ phản hồi
    def feedback(self, s: Session) -> pd.DataFrame:
        """Một dòng cho mỗi (hồ sơ, tín hiệu) đã được đánh giá; lấy đánh giá mới nhất, và đánh giá thật của kế toán luôn
        được ưu tiên hơn phản hồi mô phỏng. Cột simulated đánh dấu phản hồi mô phỏng. "mo_hinh" trong giao diện ứng với
        cụm mua dồn nếu hồ sơ là cụm, ngược lại là giao dịch bất thường."""
        rows = s.execute(select(Decision.id, Decision.case_id, Decision.signal_feedback, Decision.action)).all()
        rows.sort(key=lambda r: (r.action != SIMULATED_ACTION, r.id))       # mô phỏng trước, thật sau (ghi đè)
        latest = {}
        for _, case_id, fb, action in rows:
            for sig, verdict in (fb or {}).items():
                if verdict in VERDICTS:
                    signal = (BURST if case_id.startswith('B|') else MODEL) if sig == MODEL else sig
                    latest[(case_id, signal)] = (VERDICTS[verdict], action == SIMULATED_ACTION)
        if not latest:
            return pd.DataFrame(columns=['case_id', 'signal', 'label', 'simulated'])
        fb = pd.DataFrame([(c, sig, lab, sim) for (c, sig), (lab, sim) in latest.items()],
                          columns=['case_id', 'signal', 'label', 'simulated'])
        txn_case = dict(s.execute(select(Transaction.txn_id, Transaction.case_id)
                                  .where(Transaction.case_id.in_(fb['case_id'].unique().tolist()))).all())
        sig = self.signals[self.signals['txn_id'].isin(txn_case)]
        sig = sig.assign(case_id=sig['txn_id'].map(txn_case)).drop_duplicates(['case_id', 'signal'])
        return fb.merge(sig, on=['case_id', 'signal'], how='inner')

    def active(self, s: Session) -> list[Calibration]:
        return s.execute(select(Calibration).where(Calibration.status == 'dang_ap_dung')).scalars().all()

    def state(self, s: Session) -> dict:
        cfg = self.settings()
        fb = self.feedback(s)
        decided = {c.key: c for c in s.execute(select(Calibration)).scalars()}
        active = {k for k, c in decided.items() if c.status == 'dang_ap_dung'}
        cal = RuleCalibrator(**cfg).fit(fb, active=active) if len(fb) else RuleCalibrator(**cfg)
        current = {p.key: p for p in cal.proposals + cal.revoked}
        open_sig = self._open_signals(s)
        for p in current.values():
            p.affected = int(p.matches(open_sig).sum())

        summary = []
        for sig in CALIBRATABLE + MODEL_SIGNALS + PROTECTED:
            d = fb[fb['signal'] == sig]
            summary.append({'signal': sig, 'name': SIGNAL_NAMES[sig], 'protected': sig in PROTECTED,
                            'n': int(len(d)), 'n_true': int(d['label'].sum()), 'n_simulated': int(d['simulated'].sum())})

        def reopened(p) -> bool:
            """Đề xuất đã bị từ chối / gỡ được đưa lại khi có thêm phản hồi so với lúc quyết định."""
            c = decided.get(p.key)
            return c is None or (c.status != 'dang_ap_dung' and p.n > (c.stats or {}).get('n', 0))

        def proposal(p) -> dict:
            c = decided.get(p.key)
            prev = None if c is None else {'status': c.status, 'decided_by': c.decided_by, 'note': c.note,
                                           'n': (c.stats or {}).get('n'),
                                           'decided_at': c.decided_at.isoformat() if c.decided_at else None}
            return {**p.as_dict(), 'previous': prev}

        def record(c: Calibration) -> dict:
            now = current.get(c.key)
            title = Proposal(c.signal, c.context, 0, 0, 0.0).title()
            return {'key': c.key, 'signal': c.signal, 'context': c.context, 'status': c.status, 'text': c.text, 'title': title,
                    'stats': c.stats, 'decided_by': c.decided_by, 'note': c.note,
                    'decided_at': c.decided_at.isoformat() if c.decided_at else None,
                    'now': now.as_dict() if now else None,
                    'revoke_suggested': c.status == 'dang_ap_dung' and c.key in {p.key for p in cal.revoked}}

        return {
            'settings': cfg,
            'n_feedback': int(len(fb)), 'n_simulated': int(fb['simulated'].sum()) if len(fb) else 0,
            'summary': summary,
            'proposals': [proposal(p) for p in cal.proposals if reopened(p)],
            'active': [record(c) for c in decided.values() if c.status == 'dang_ap_dung'],
            'history': [record(c) for c in decided.values() if c.status != 'dang_ap_dung'],
        }

    # ------------------------------------------------------------ quyết định của quản lý
    def decide(self, s: Session, key: str, decision: str, user: str, role: str, note: str = '') -> dict:
        if role != 'quan_ly':
            raise CalibrationError('Chỉ quản lý được duyệt hoặc gỡ hiệu chỉnh')
        st = self.state(s)
        c = s.get(Calibration, key)
        if decision in ('ap_dung', 'tu_choi'):
            p = next((x for x in st['proposals'] if x['key'] == key), None)
            if p is None:
                raise CalibrationError('Đề xuất không còn hiệu lực (phản hồi đã thay đổi hoặc đã được quyết định)')
            status = 'dang_ap_dung' if decision == 'ap_dung' else 'tu_choi'
            if c is None:
                c = Calibration(key=key, signal=p['signal'], context=p['context'], text=p['text'], decided_by=user)
                s.add(c)
            c.status, c.stats, c.text, c.decided_by, c.note = status, \
                {k: p[k] for k in ('n', 'n_true', 'precision', 'p_below', 'affected')}, p['text'], user, note
        elif decision == 'go_bo':
            if c is None or c.status != 'dang_ap_dung':
                raise CalibrationError('Hiệu chỉnh này không đang được áp dụng')
            now = next((x['now'] for x in st['active'] if x['key'] == key), None)
            c.status, c.decided_by, c.note = 'da_go', user, note
            if now:                       # số phản hồi lúc gỡ: chỉ đề xuất lại khi có thêm phản hồi
                c.stats = {k: now[k] for k in ('n', 'n_true', 'precision', 'p_below', 'affected')}
        else:
            raise CalibrationError(f'Quyết định không hợp lệ: {decision}')
        s.flush()
        changed = self.relevel(s, user) if decision in ('ap_dung', 'go_bo') else []
        s.add(AuditLog(case_id=None, user=user, action=f'hieu_chinh_{decision}',
                       detail={'key': key, 'note': note, 'cases_changed': len(changed)}))
        return {'key': key, 'status': c.status, 'cases_changed': len(changed)}

    def _open_signals(self, s: Session) -> pd.DataFrame:
        rows = s.execute(select(Transaction.txn_id, Transaction.case_id).join(Case, Case.id == Transaction.case_id)
                         .where(Case.status == OPEN_STATUS)).all()
        txn_case = dict(rows)
        sig = self.signals[self.signals['txn_id'].isin(txn_case)]
        return sig.assign(case_id=sig['txn_id'].map(txn_case))

    def relevel(self, s: Session, user: str) -> list[dict]:
        """Đặt lại mức hồ sơ theo các hiệu chỉnh đang áp dụng (xem docstring module)."""
        rows = s.execute(select(Transaction.txn_id, Transaction.case_id, Transaction.final_level)
                         .join(Case, Case.id == Transaction.case_id).where(Case.status.not_in(FINAL))).all()
        txns = pd.DataFrame(rows, columns=['txn_id', 'case_id', 'final_level'])
        original = txns.assign(r=txns['final_level'].map(RANK).fillna(0)).groupby('case_id')['r'].max()
        sig = self.signals[self.signals['txn_id'].isin(set(txns['txn_id']))]
        sig = sig.assign(case_id=sig['txn_id'].map(dict(zip(txns['txn_id'], txns['case_id']))))
        sup = pd.Series(False, index=sig.index)
        for c in self.active(s):
            sup |= Proposal(c.signal, c.context, 0, 0, 1.0).matches(sig)
        touched = pd.Index(sig.loc[sup, 'case_id'].unique())
        computed = levels(sig, sup, touched, by='case_id').map(RANK)
        target = original.copy()
        target[touched] = computed.to_numpy()

        changed = []
        stmt = select(Case).where(Case.status.not_in(FINAL)).with_for_update()   # khóa: không đổi trạng thái giữa chừng
        for case in s.execute(stmt).scalars():
            if case.id not in target.index:
                continue
            new, cur = int(target[case.id]), RANK.get(case.level, 0)
            if new == cur or (case.status != OPEN_STATUS and new < cur):
                continue
            to = {v: k for k, v in RANK.items()}[new]
            changed.append({'case_id': case.id, 'from': case.level, 'to': to, 'status': case.status})
            case.level = to
        for ch in changed:
            s.add(AuditLog(case_id=ch['case_id'], user=user, action='hieu_chinh_doi_muc', detail=ch))
        return changed

    def for_case(self, s: Session, txn_ids: list[str]) -> list[dict]:
        """Các hiệu chỉnh đang áp dụng có tác động tới tín hiệu của hồ sơ (để hiển thị trong chi tiết hồ sơ)."""
        sig = self.signals[self.signals['txn_id'].isin(txn_ids)]
        out = []
        for c in self.active(s):
            m = Proposal(c.signal, c.context, 0, 0, 1.0).matches(sig)
            if m.any():
                out.append({'key': c.key, 'signal': c.signal, 'text': c.text, 'decided_by': c.decided_by,
                            'title': Proposal(c.signal, c.context, 0, 0, 0.0).title(),
                            'txn_ids': sorted(sig.loc[m, 'txn_id'].unique().tolist())})
        return out

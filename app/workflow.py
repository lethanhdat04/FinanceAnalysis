"""Quy trình xử lý hồ sơ (mục 5 bản phân tích nghiệp vụ, Điều 6 và 25 quy chế). Kiểm tra bằng code, không giao cho LLM."""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import AuditLog, Case, Decision

STATUSES = ['Chờ review', 'Đang review', 'Chờ bổ sung', 'Chờ phê duyệt', 'Chấp nhận', 'Đã duyệt', 'Từ chối']
FINAL = {'Chấp nhận', 'Đã duyệt', 'Từ chối'}
CONCLUSIONS = {'hop_le': 'Hợp lệ', 'vi_pham': 'Vi phạm', 'can_bo_sung': 'Cần bổ sung chứng từ'}
SIGNAL_VERDICTS = {'dung': 'Cảnh báo đúng', 'canh_bao_gia': 'Cảnh báo giả'}


@dataclass(frozen=True)
class Action:
    label: str
    role: str                 # ke_toan | quan_ly
    from_status: str
    to_status: str
    conclusion: str | None = None


ACTIONS = {
    'nhan_xu_ly': Action('Nhận xử lý', 'ke_toan', 'Chờ review', 'Đang review'),
    'chap_nhan': Action('Chấp nhận', 'ke_toan', 'Đang review', 'Chấp nhận', 'hop_le'),
    'yeu_cau_bo_sung': Action('Yêu cầu bổ sung', 'ke_toan', 'Đang review', 'Chờ bổ sung', 'can_bo_sung'),
    'da_bo_sung': Action('Đã nhận bổ sung', 'ke_toan', 'Chờ bổ sung', 'Đang review'),
    'chuyen_phe_duyet': Action('Chuyển quản lý phê duyệt', 'ke_toan', 'Đang review', 'Chờ phê duyệt'),
    'phe_duyet': Action('Phê duyệt', 'quan_ly', 'Chờ phê duyệt', 'Đã duyệt', 'hop_le'),
    'tu_choi': Action('Từ chối', 'quan_ly', 'Chờ phê duyệt', 'Từ chối', 'vi_pham'),
}


class WorkflowError(ValueError):
    pass


def allowed_actions(case: Case, role: str) -> list[str]:
    out = [k for k, a in ACTIONS.items() if a.from_status == case.status and a.role == role]
    if case.level == 'Cảnh báo cao':
        out = [k for k in out if k != 'chap_nhan']      # Điều 6: vi phạm mức Cao phải do quản lý phê duyệt
    return out


def apply_action(s: Session, case: Case, action: str, user: str, role: str, note: str = '',
                 conclusion: str | None = None, signal_feedback: dict | None = None) -> Decision:
    if action not in ACTIONS:
        raise WorkflowError(f'Thao tác không tồn tại: {action}')
    s.refresh(case, with_for_update=True)     # khóa hồ sơ và đọc lại trạng thái mới nhất (hai người bấm cùng lúc)
    a = ACTIONS[action]
    if role != a.role:
        raise WorkflowError(f'"{a.label}" chỉ dành cho vai trò {a.role}')
    if action not in allowed_actions(case, role):
        if action == 'chap_nhan' and case.level == 'Cảnh báo cao' and case.status == a.from_status:
            raise WorkflowError('Hồ sơ Cảnh báo cao phải chuyển quản lý phê duyệt (Điều 6 quy chế)')
        raise WorkflowError(f'Không thể "{a.label}" khi hồ sơ đang ở trạng thái "{case.status}"')
    if conclusion is not None and conclusion != a.conclusion:          # kết luận do thao tác quyết định, không tự chọn
        raise WorkflowError(f'"{a.label}" không đi với kết luận {conclusion}')
    conclusion = a.conclusion
    feedback = signal_feedback or {}
    bad = {k: v for k, v in feedback.items() if v not in SIGNAL_VERDICTS}
    if bad:
        raise WorkflowError(f'Đánh giá tín hiệu không hợp lệ: {bad}')
    if action == 'tu_choi' and not note.strip():
        raise WorkflowError('Từ chối cần ghi lý do')

    d = Decision(case_id=case.id, user=user, role=role, action=action, from_status=case.status,
                 to_status=a.to_status, conclusion=conclusion, signal_feedback=feedback, note=note)
    case.status = a.to_status
    if action == 'nhan_xu_ly':
        case.assignee = user
    s.add(d)
    s.add(AuditLog(case_id=case.id, user=user, action=action,
                   detail={'from': d.from_status, 'to': d.to_status, 'conclusion': conclusion, 'note': note}))
    return d

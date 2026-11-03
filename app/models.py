"""Bảng dữ liệu của ứng dụng."""
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Transaction(Base):
    __tablename__ = 'transactions'
    txn_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    tx_date: Mapped[date] = mapped_column(Date, index=True)
    post_date: Mapped[date] = mapped_column(Date)
    agency_id: Mapped[str] = mapped_column(String(10), index=True)
    agency: Mapped[str] = mapped_column(String(80))
    cardholder_id: Mapped[str] = mapped_column(String(80), index=True)
    merchant: Mapped[str] = mapped_column(String(80))
    merchant_norm: Mapped[str] = mapped_column(String(80), index=True)
    item_desc: Mapped[str] = mapped_column(Text)
    amount: Mapped[float] = mapped_column(Float)
    mcc_desc: Mapped[str] = mapped_column(String(80))
    expense_group: Mapped[str] = mapped_column(String(30), index=True)
    rule_level: Mapped[str] = mapped_column(String(20))
    anomaly_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    anomaly_tx_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    burst_day_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    final_level: Mapped[str] = mapped_column(String(20), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey('cases.id'), index=True, nullable=True)


class RuleHit(Base):
    __tablename__ = 'rule_hits'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    txn_id: Mapped[str] = mapped_column(ForeignKey('transactions.txn_id'), index=True)
    rule_id: Mapped[str] = mapped_column(String(5), index=True)
    severity: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    related_txn_ids: Mapped[str] = mapped_column(Text, default='')


class Case(Base):
    """Hồ sơ cần xem: một giao dịch, hoặc cả cụm mua dồn của một người chi trong một ngày."""
    __tablename__ = 'cases'
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))                    # 'giao_dich' | 'cum_mua_don'
    cardholder_id: Mapped[str] = mapped_column(String(80), index=True)
    agency: Mapped[str] = mapped_column(String(80), index=True)
    tx_date: Mapped[date] = mapped_column(Date, index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    n_txn: Mapped[int] = mapped_column(Integer)
    total_amount: Mapped[float] = mapped_column(Float)
    level: Mapped[str] = mapped_column(String(20), index=True)       # Cần review | Cảnh báo cao
    rules: Mapped[str] = mapped_column(String(60), default='')       # các mã rule, vd "R02,R04"
    has_anomaly: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), index=True, default='Chờ review')
    assignee: Mapped[str | None] = mapped_column(String(60), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    transactions: Mapped[list[Transaction]] = relationship(order_by=Transaction.txn_id)


class Explanation(Base):
    __tablename__ = 'explanations'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey('cases.id'), index=True)
    kind: Mapped[str] = mapped_column(String(20), default='giai_thich')   # giai_thich | dieu_tra (agent)
    model: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Decision(Base):
    """Kết luận của kế toán / quản lý cho một hồ sơ – dữ liệu cho vòng học từ phản hồi."""
    __tablename__ = 'decisions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey('cases.id'), index=True)
    user: Mapped[str] = mapped_column(String(60))
    role: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(30))
    from_status: Mapped[str] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    conclusion: Mapped[str | None] = mapped_column(String(30), nullable=True)   # hop_le | vi_pham | can_bo_sung
    signal_feedback: Mapped[dict] = mapped_column(JSON, default=dict)         # {"R03": "canh_bao_gia", "mo_hinh": "dung"}
    note: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ReviewRequest(Base):
    __tablename__ = 'review_requests'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey('cases.id'), index=True, nullable=True)
    txn_id: Mapped[str] = mapped_column(String(20), index=True)
    reason: Mapped[str] = mapped_column(Text)
    route_to: Mapped[str] = mapped_column(String(20))
    required_documents: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(20))             # tro_ly | nguoi_dung | agent
    status: Mapped[str] = mapped_column(String(20), default='Chờ review')
    created_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Calibration(Base):
    """Hiệu chỉnh do quản lý quyết định từ đề xuất của cơ chế học từ phản hồi (src/feedback.py)."""
    __tablename__ = 'calibrations'
    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    signal: Mapped[str] = mapped_column(String(20), index=True)
    context: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), index=True)      # dang_ap_dung | tu_choi | da_go
    stats: Mapped[dict] = mapped_column(JSON, default=dict)           # số phản hồi lúc quyết định
    text: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(String(60))
    note: Mapped[str] = mapped_column(Text, default='')
    decided_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AuditLog(Base):
    __tablename__ = 'audit_log'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    user: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

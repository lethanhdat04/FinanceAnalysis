# Dữ liệu

Nguồn: Oklahoma Office of Management and Enterprise Services, Purchase Card (PCard) – data.ok.gov.
Tải thủ công bằng trình duyệt ngày 30/09/2026 (data.ok.gov chặn tải tự động).

14 file theo tháng trong `raw/`, đặt tên `pcard_YYYYMM.csv`, 485.616 giao dịch, tháng 07/2025 – 08/2026.
Tên file đã được đổi theo tháng thực tế trong cột CALENDAR_YEAR/CALENDAR_MONTH (tên gốc khi tải về có 2 file ghi sai tháng).

Lưu ý khi đọc:
- Một số file có cột ROWID, file 04/2026 có tên cột " AMOUNT " (thừa khoảng trắng).
- AMOUNT có dạng kế toán `$(121.52)` = số âm, có dấu phẩy ngăn cách hàng nghìn.
- Ngày theo định dạng `%d-%b-%y` (ví dụ 30-Jun-26).

## Nhóm chi phí

`mcc_groups.csv`: bảng gom 429 mã MCC thành 16 nhóm (14 nhóm chi phí + Khác + Chưa xác định).
Sinh bởi `scripts/build_mcc_groups.py`; gán cho giao dịch bằng `src/labels.py::add_expense_group`.
Giao dịch Amazon có MCC 'BOOK STORES' được chuyển sang nhóm Chưa xác định (RETAIL_GENERAL) và không dùng làm nhãn huấn luyện.

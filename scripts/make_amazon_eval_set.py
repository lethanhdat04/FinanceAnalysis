"""Tạo file Excel để gán nhãn tay giao dịch Amazon: data/labeling/amazon_eval_set.xlsx.

Lấy mẫu ngẫu nhiên 300 giao dịch Amazon (MCC 'BOOK STORES', có mô tả cụ thể, số tiền dương)
trong giai đoạn kiểm tra 06–08/2026. Không ghi đè nếu file đã tồn tại, để không mất nhãn đã gán.

Chạy: .venv/Scripts/python scripts/make_amazon_eval_set.py
"""
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data_loader import load_pcard  # noqa: E402
from src.features import VALID_END, prepare  # noqa: E402
from src.labels import GROUPS, NOT_TRAINED  # noqa: E402

OUT = ROOT / 'data' / 'labeling' / 'amazon_eval_set.xlsx'
N, SEED = 300, 42
CANNOT_TELL = 'Không xác định được'

EXAMPLES = {
    'AIR_TRAVEL': 'Vé máy bay, phí đổi vé, dịch vụ đại lý du lịch',
    'LODGING': 'Tiền phòng khách sạn',
    'GROUND_TRANSPORT': 'Thuê xe, taxi, Uber, gửi xe, phí cầu đường',
    'FOOD': 'Đồ ăn, nước uống, cà phê, bánh kẹo, thực phẩm',
    'OFFICE_PRINT': 'Giấy, bút, sổ, kẹp, mực in, sách, bảng tên, tem thư',
    'IT_TELECOM': 'Máy tính, màn hình, ổ cứng, chuột, cáp, phần mềm, điện thoại, tai nghe',
    'MEDICAL_LAB': 'Găng tay y tế, ống nghiệm, hóa chất, thuốc, dụng cụ thí nghiệm',
    'FACILITIES_MRO': 'Dụng cụ, linh kiện, đồ điện, đồ lau dọn, bàn ghế, kệ, sơn, ống nước',
    'VEHICLE_FUEL': 'Phụ tùng ô tô, lốp, dầu nhớt, nhiên liệu',
    'UTILITIES': 'Hóa đơn điện, nước, gas (hiếm gặp trên Amazon)',
    'PROF_SERVICES': 'Dịch vụ: quảng cáo, vận chuyển, in thuê, tư vấn',
    'FEES_EDU_EVENTS': 'Lệ phí, hội phí, phí đăng ký, vé sự kiện',
    'APPAREL_SPORTS': 'Quần áo, đồng phục, giày, dụng cụ thể thao',
    'AGRI_ANIMAL': 'Hạt giống, phân bón, cây cảnh, thức ăn và đồ dùng cho động vật',
    'OTHER': 'Quà tặng, trang sức, đồ trang trí, thẻ quà tặng',
}


def main():
    if OUT.exists():
        sys.exit(f'{OUT} đã tồn tại – không ghi đè để giữ nhãn đã gán. Xóa hoặc đổi tên file nếu muốn tạo lại.')

    df = prepare(load_pcard())
    pool = df[df.merchant.str.contains('AMAZON|AMZN', case=False) & (df.mcc_desc == 'BOOK STORES')
              & df.informative & (df.amount > 0) & (df.month > VALID_END)]
    sample = pool.sample(N, random_state=SEED).sort_values('tx_date')

    choices = [name for code, name in GROUPS.items() if code not in NOT_TRAINED] + [GROUPS['OTHER'], CANNOT_TELL]

    wb = Workbook()
    ws = wb.active
    ws.title = 'Gán nhãn'
    headers = ['txn_id', 'Ngày', 'Đơn vị', 'Mô tả mặt hàng', 'Số tiền ($)', 'Nhóm chi phí', 'Không chắc (x)', 'Ghi chú']
    ws.append(headers)
    for r in sample.itertuples():
        ws.append([r.txn_id, r.tx_date.date(), r.agency, r.item_desc, round(r.amount, 2), None, None, None])

    head_fill = PatternFill('solid', fgColor='DCE6F2')
    input_fill = PatternFill('solid', fgColor='FFF7E0')
    for c in ws[1]:
        c.font, c.fill = Font(bold=True), head_fill
    for col, width in zip('ABCDEFGH', [15, 11, 30, 60, 11, 38, 13, 30]):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=2):
        row[3].alignment = Alignment(wrap_text=True, vertical='top')
        row[1].number_format = 'dd/mm/yyyy'
        row[4].number_format = '#,##0.00'
        for c in row[5:8]:
            c.fill = input_fill
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions

    lists = wb.create_sheet('Danh sách nhóm')
    for i, name in enumerate(choices, start=1):
        lists.cell(row=i, column=1, value=name)
    lists.sheet_state = 'hidden'
    dv = DataValidation(type='list', formula1=f"='Danh sách nhóm'!$A$1:$A${len(choices)}", allow_blank=True,
                        showErrorMessage=True, errorTitle='Giá trị không hợp lệ', error='Chọn một nhóm trong danh sách.')
    ws.add_data_validation(dv)
    dv.add(f'F2:F{N + 1}')

    guide = wb.create_sheet('Hướng dẫn', 0)
    lines = [
        ('Hướng dẫn gán nhãn', True),
        ('', False),
        (f'1. Sheet "Gán nhãn" có {N} giao dịch Amazon chọn ngẫu nhiên (06–08/2026). Hệ thống gốc ghi tất cả là "BOOK STORES".', False),
        ('2. Đọc "Mô tả mặt hàng" (thường bị cắt ngắn, đuôi PCE/EA/CS là đơn vị tính), chọn nhóm ở cột "Nhóm chi phí" (danh sách thả xuống).', False),
        ('3. Nếu phân vân giữa hai nhóm: chọn nhóm hợp lý nhất, đánh "x" vào cột "Không chắc", ghi nhóm còn lại vào "Ghi chú".', False),
        (f'4. Nếu mô tả không đủ để biết là gì: chọn "{CANNOT_TELL}".', False),
        ('5. Gán theo mục đích chi tiêu của món hàng. Ví dụ: găng tay y tế → Y tế; găng tay lao động → Vật tư.', False),
        ('6. Không tra cứu kết quả mô hình trước khi gán, để tập đánh giá khách quan.', False),
        ('', False),
        ('Các nhóm chi phí', True),
    ]
    for text, bold in lines:
        guide.append([text])
        guide.cell(row=guide.max_row, column=1).font = Font(bold=bold, size=13 if bold else 11)
    guide.append(['Nhóm', 'Ví dụ'])
    for c in guide[guide.max_row]:
        c.font, c.fill = Font(bold=True), head_fill
    for code, name in GROUPS.items():
        if code in EXAMPLES:
            guide.append([name, EXAMPLES[code]])
    guide.append([CANNOT_TELL, 'Mô tả quá ngắn hoặc mơ hồ, ví dụ "Amazon Physical Gift Card", mã sản phẩm không rõ'])
    guide.column_dimensions['A'].width = 42
    guide.column_dimensions['B'].width = 90

    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f'Đã tạo {OUT} ({N} giao dịch, chọn từ {len(pool):,} giao dịch Amazon có mô tả cụ thể)')


if __name__ == '__main__':
    main()

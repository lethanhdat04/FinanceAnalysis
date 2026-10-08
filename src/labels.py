"""Gán nhóm chi phí cho giao dịch từ bảng data/mcc_groups.csv (sinh bởi scripts/build_mcc_groups.py)."""
from pathlib import Path

import pandas as pd

MAPPING_FILE = Path(__file__).resolve().parents[1] / 'data' / 'mcc_groups.csv'

# Thứ tự ở đây là thứ tự hiển thị trong báo cáo.
GROUPS = {
    'AIR_TRAVEL': 'Vé máy bay & đại lý du lịch',
    'LODGING': 'Lưu trú khách sạn',
    'GROUND_TRANSPORT': 'Đi lại (thuê xe, taxi, gửi xe, cầu đường)',
    'FOOD': 'Ăn uống & thực phẩm',
    'OFFICE_PRINT': 'Văn phòng phẩm, sách & in ấn',
    'IT_TELECOM': 'CNTT, phần mềm & viễn thông',
    'MEDICAL_LAB': 'Y tế, phòng thí nghiệm & hóa chất',
    'FACILITIES_MRO': 'Vật tư, xây dựng, sửa chữa & nội thất',
    'VEHICLE_FUEL': 'Xe cộ & nhiên liệu',
    'UTILITIES': 'Điện, nước, gas',
    'PROF_SERVICES': 'Dịch vụ chuyên môn & vận chuyển',
    'FEES_EDU_EVENTS': 'Lệ phí, hội phí, đào tạo & sự kiện',
    'APPAREL_SPORTS': 'Trang phục, đồng phục & đồ thể thao',
    'AGRI_ANIMAL': 'Nông nghiệp, cây trồng & động vật',
    'OTHER': 'Khác',
    'RETAIL_GENERAL': 'Chưa xác định (cửa hàng bán đủ loại)',
}

# RETAIL_GENERAL: cửa hàng không cho biết loại chi phí.
UNLABELED = 'RETAIL_GENERAL'
# Không dùng làm nhãn huấn luyện: RETAIL_GENERAL (cần phân loại lại) và OTHER (quá ít, ~0,2%).
NOT_TRAINED = {UNLABELED, 'OTHER'}
AMAZON_PATTERN = r'AMAZON|AMZN'


def add_expense_group(df: pd.DataFrame, mapping_file: Path = MAPPING_FILE) -> pd.DataFrame:
    """Thêm các cột:
    - expense_group: nhóm chi phí theo MCC; riêng giao dịch Amazon mang MCC 'BOOK STORES'
      được chuyển thành RETAIL_GENERAL vì Amazon bán mọi thứ dưới mã này. Các dịch vụ Amazon
      có MCC riêng (Web Services, Digital, Prime) giữ nguyên nhóm.
    - label: nhãn dùng để huấn luyện; NaN nếu nhóm thuộc NOT_TRAINED.
    """
    mapping = pd.read_csv(mapping_file, encoding='utf-8-sig').set_index('mcc_desc').group_code
    out = df.copy()
    out['expense_group'] = out['mcc_desc'].map(mapping)
    if out['expense_group'].isna().any():
        unknown = out.loc[out['expense_group'].isna(), 'mcc_desc'].unique()[:10]
        raise ValueError(f'MCC chưa có trong {mapping_file.name}: {list(unknown)}. Chạy lại scripts/build_mcc_groups.py')
    amazon_books = (out['merchant'].str.contains(AMAZON_PATTERN, case=False, regex=True)
                    & (out['mcc_desc'] == 'BOOK STORES'))
    out.loc[amazon_books, 'expense_group'] = UNLABELED
    out['label'] = out['expense_group'].where(~out['expense_group'].isin(NOT_TRAINED))
    return out

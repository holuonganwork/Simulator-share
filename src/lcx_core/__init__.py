"""lcx_core - Loi phat hien gian lan tai xe cho du an "La Chan Cuoc Xe".

Package nay tieu thu du lieu tu repo GSM_SIMULATOR (sibling repo, xem
configs/data_sources.yaml) va trien khai 4 tang xu ly theo docs/architecture.md:

- tier_a: luat nguong cung (real-time, <100ms trong he thong that)
- tier_b: cham diem ML tong hop nhieu tin hieu
- tier_c: phan tich do thi quan he thiet bi/tai khoan (hien la stub - thieu du lieu)
- agent:  pipeline tat dinh goi tool, tong hop bang chung, sinh giai thich

Moi mau hinh gian lan duoc trien khai bam sat dung nhung gi CO THAT trong 13 bang
Parquet cua GSM_SIMULATOR. Mau hinh khong co tin hieu du lieu tuong ung duoc de
la stub co tai lieu ro rang (khong bia logic) - xem docs/data-matrix.md.
"""

__version__ = "0.1.0"

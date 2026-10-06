"""HIL (human-in-the-loop) — một actor của sim do NGƯỜI điều khiển.

Bề mặt **SIM-only** cho nghiên cứu/demo (quyết định owner 2026-08-13): số đo từ phiên HIL
**không được dùng để tuyên bố uplift thật**. Con số đối ngoại vẫn là +3.632,7đ từ arm autonomous
n=100 (`research/simulation/ADVISOR-SCOREBOARD.md`).

Kiến trúc "đóng băng toàn bộ thế giới" (cách 1, Cường chốt): `env.run(until=<event>)` chạy tới
điểm quyết định rồi TRẢ VỀ — sim đứng nguyên, không luồng nền, không treo riêng một actor.

Cổng sống-còn: `tests/test_hil_khong_doi_the_gioi.py` — bật HIL không được đổi một bit của thế
giới autonomous (vân tay 5 seed).
"""

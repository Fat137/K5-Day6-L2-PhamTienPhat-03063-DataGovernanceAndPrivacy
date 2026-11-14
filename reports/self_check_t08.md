# Self-check T08 · Baseline vs PoC (không dùng ground truth)

Sinh bởi `python -m tools.self_check_t08` — không sửa tay. Baseline `out/baseline` (git f24a269), PoC `out/poc` (git f24a269), seed 42.
Metric chính thức (`Deletion Completeness`, `False-delete count`) cần `gt/` của giảng viên — xem `src.evaluate`.

| Chỉ số (không cần GT) | Baseline | PoC | Tốt hơn |
|---|---|---|---|
| Số dòng hành động | 10 | 158 | — |
| Phân bố hành động | {'delete': 10} | {'delete': 105, 'retain_legal_hold': 40, 'escalate': 13} | — |
| Scope coverage (cặp được xử lý / 158 cặp trong phạm vi) | 10/158 = 0.0633 (Wilson95 (0.0347, 0.1126)) | 158/158 = 1.0 (Wilson95 (0.9763, 1.0)) | higher |
| Xóa dữ liệu đang legal hold (kể cả kế thừa) | 2 ['a0043', 'a0099'] | 0 [] | lower (mục tiêu 0) |
| Xóa artefact chứa người khác | 0 | 0 | lower (mục tiêu 0) |
| Dòng ngoài phạm vi yêu cầu | 0 | 0 | lower (mục tiêu 0) |
| Audit coverage (hành động có dòng audit) | 0.0 | 1.0 | higher |
| Quyết định | HOLD (BASELINE_ONLY) | GO (ALL_EXCEPTIONS_REVIEWED) | — |
| Runtime (s) | 0.0361 | 0.5441 | lower |
| Runtime / 100 artefact đầu vào (s) | 0.017 | 0.2567 | lower |

## Slice theo loại artefact (cặp trong phạm vi → được xử lý / bị xóa)

| Loại | Trong phạm vi | Baseline xử lý | Baseline xóa | PoC xử lý | PoC xóa |
|---|---|---|---|---|---|
| annotation | 33 | 0 | 0 | 33 | 21 |
| backup | 10 | 0 | 0 | 10 | 7 |
| embedding | 30 | 0 | 0 | 30 | 24 |
| face_crop | 30 | 0 | 0 | 30 | 24 |
| frame | 33 | 0 | 0 | 33 | 21 |
| raw_video | 10 | 10 | 10 | 10 | 8 |
| training_export | 12 | 0 | 0 | 12 | 0 |

Máy: Windows-11-10.0.26200-SP0 · CPU AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD · Python 3.13.2

Lưu ý: "legal hold" và "người khác" ở đây theo **chính sách đã chốt của PoC** (hold lan xuống toàn bộ bản dẫn xuất); GT ẩn của giảng viên có thể vạch ranh giới khác — xem report §05.

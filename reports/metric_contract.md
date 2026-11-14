# Metric contract (khai báo bắt buộc — chốt ở phút 30, trước khi chạy eval)

> Các metric chuẩn đã được cài trong `src/metrics/tracks.py`. File này là nơi nhóm **cam kết** cách dùng chúng.
> Không đổi testset, threshold hay trọng số sau khi đã thấy kết quả eval.

| Mục | Khai báo của nhóm |
|---|---|
| Track | T08 · Retention & Delete Request Simulator (làm cá nhân, `IND-03063`) |
| Primary metric (tên đúng như evaluator in ra) | `Deletion Completeness` |
| Tăng tốt hay giảm tốt? | higher |
| Đơn vị đánh giá (image / region / pair / release / batch / case …) | cặp (`request_id`, `artefact_id`) — một artefact trong phạm vi một yêu cầu xóa |
| Công thức + numerator / denominator | #cặp GT `expected_action = delete` mà pred `action = delete` / #cặp GT `expected_action = delete`. Cặp GT không có trong `actions.csv` tính là **chưa xóa** (left join). |
| Cách ghép prediction ↔ GT (IoU? id? tolerance?) | khớp chính xác theo khóa (`request_id`, `artefact_id`); `action` được chuẩn hóa `strip().lower()`; trùng khóa trong pred → evaluator báo lỗi |
| Candidate pool cố định | 10 yêu cầu trong `fixtures/t08_deletion/requests.json` × 212 artefact trong `data_graph.json` (sha256 khớp `manifest.csv`) |
| Review budget B / K | Không có budget cố định: **mọi** ngoại lệ (`retain_legal_hold`, `escalate`) đều vào `review_queue.csv` để người duyệt phê duyệt — không để máy tự quyết ngoại lệ |
| Seed | 42 |
| Threshold policy (chọn trên dev như thế nào) | Không có threshold số. Chính sách là luật cố định, chốt trước khi chạy eval, theo thứ tự ưu tiên trong handout: legal hold > quyền (ACL) / nguồn gốc không rõ > nhiều subject > delete. Không chỉnh luật sau khi thấy kết quả eval. |
| Eval split (không dùng để tune) | Evaluator T08 không chia split — chấm trên cả 10 yêu cầu. Vì vậy không có tập dev để tune; luật được rút ra từ handout + schema, không từ GT (GT không có trong repo). |
| Chỉ số lỗi xấu (miss / false alarm / false reject) | **False-delete count** (mục tiêu = 0): xóa nhầm ngoại lệ + xóa artefact ngoài phạm vi yêu cầu. Đây là lỗi không thể đảo ngược nên ưu tiên hơn completeness. Miss (artefact phải xóa mà chưa xóa) phản ánh qua primary. |
| Slices sẽ báo cáo (≥2) | (1) theo `expected_action` (delete / retain_legal_hold / escalate) — evaluator in sẵn; (2) theo loại artefact (raw_video, backup, frame, face_crop, embedding, annotation, training_export) — tự phân tích trên output |
| Cách báo độ bất định khi N nhỏ (Wilson CI / bootstrap) | Wilson 95% CI cho Deletion Completeness và Exception handling accuracy (số ngoại lệ chỉ vài chục cặp); báo kèm numerator/denominator, không chỉ phần trăm |
| Ngày giờ chốt + người chốt | 2026-11-14 · Phạm Tiến Phát |

Metric phụ đi kèm (evaluator in sẵn): `False-delete count`, `Exception handling accuracy`, `Audit trace completeness` (#hành động có dòng trong `audit_log.jsonl` với `item_id = artefact_id` / #hành động).
Kiểm tra thêm của PoC (không phải metric chấm): idempotency — chạy 2 lần liên tiếp phải ra `actions.csv` giống hệt.

Nếu baseline = 0 thì ghi rõ "baseline = 0" thay vì phần trăm cải thiện.

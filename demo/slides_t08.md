# T08 · Retention & Delete Request Simulator
Phạm Tiến Phát · IND-03063 (cá nhân) · số liệu: `reports/self_check_t08.md`

---

## 1 · Vấn đề: "Xóa dữ liệu của tôi" không phải xóa 1 file

```
raw_video ─► backup
    └─► frame ─► annotation
          └─► face_crop ─► embedding ─► training_export (gộp 9–10 người)
```

- 10 yêu cầu xóa · 212 artefact mock · 158 cặp (yêu cầu, artefact) trong phạm vi
- Bẫy: 2 video đang **legal hold**, backup trên hệ thống **vendor-x**, 3 embedding **không rõ nguồn gốc**,
  ảnh **chung 2 người**, export gộp nhiều người
- Xóa thiếu → còn dữ liệu định danh · Xóa thừa → mất bằng chứng / xóa dữ liệu người khác. **Cả hai không đảo ngược được.**

---

## 2 · Baseline vs PoC (không cần GT)

| | Baseline (xóa raw theo filename) | PoC |
|---|---|---|
| Cặp trong phạm vi được xử lý | 10/158 = 0.0633 | 158/158 = 1.0 |
| Xóa dữ liệu đang legal hold | **2** (`a0043`, `a0099`) | **0** |
| Xóa dữ liệu người khác | 0 | 0 |
| Audit coverage | 0.0 | 1.0 |
| Runtime toàn bộ | 0.036 s | 0.544 s |

Metric chính thức (Deletion Completeness, False-delete) cần `gt/` của giảng viên.

---

## 3 · Cách làm: lineage + luật có thứ tự ưu tiên + người duyệt

1. **Phạm vi** = artefact có subject của người yêu cầu (không xóa mù theo lineage: `a0012` là mặt của *người khác* cắt từ frame chung)
2. **Luật:** legal hold (lan xuống bản dẫn xuất) > danh tính > quyền ACL / nguồn gốc > nhiều người > xóa
3. **Mọi ngoại lệ (56) → người duyệt**; reviewer **không thể** ép xóa dữ liệu đang hold (bị chặn + ghi audit)
4. **HOLD** khi còn chờ duyệt → **GO** → dry-run → xóa mock, chạy lại = `already_deleted` (idempotent)

---

## 4 · Human-in-the-loop & 2 failure case

- Quyết định reviewer: hold → giữ · vendor-x → gửi vendor xóa · unknown origin → **xóa** · ảnh 2 người → **che mặt**, không xóa cả ảnh
- Kết quả: 105 xóa · 40 giữ (legal hold) · 13 chuyển tiếp → 10/10 yêu cầu `partially_fulfilled`
- **Failure 1:** baseline xóa video đang bị điều tra (khớp filename, không đọc `legal_hold`)
- **Failure 2:** baseline xóa raw nhưng để lại backup + 3 embedding mặt của subj_01 (không truy `parent_ids`)

---

## 5 · Giới hạn & câu hỏi cho debate

- Hold lan xuống export ⇒ an toàn nhưng **giữ thừa**: nếu GT chỉ giữ đúng artefact có cờ hold → mất completeness (không phát sinh xóa nhầm)
- "Che mặt" mới là ghi chú — chưa có redaction thật (T07)
- Xóa mock ≠ xóa thật: backup bất biến, cache, model đã train
- Cần DPO/legal xác nhận; không tuyên bố đạt GDPR / Luật 91/2025
- Tái lập: 1 lệnh, offline, seed 42 · `pytest`: 18 test T08 pass · fix lỗi CRLF làm lệch sha256 bằng `.gitattributes`

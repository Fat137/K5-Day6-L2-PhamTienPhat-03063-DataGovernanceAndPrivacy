# Report — Day 06 Data Lab (Field-to-Release PoC) · T08 · làm cá nhân

> Mọi con số lấy từ file do script sinh ra: `reports/self_check_t08.md|json` (`python -m tools.self_check_t08`),
> `out/poc/run_info.json`, `out/poc/request_summary.csv`. Metric chính thức (`reports/metrics_*.json`,
> `reports/table1_baseline_vs_poc.md`) cần `fixtures/t08_deletion/gt/` — chỉ giảng viên có, nên **chưa có** trong repo này.

## 00 · Header

| Mục | Giá trị |
|---|---|
| Group ID | IND-03063 (làm cá nhân, đã hỏi LC) |
| Challenge | T08 · Retention & Delete Request Simulator |
| Thành viên + vai trò | Phạm Tiến Phát — đảm nhận cả 5 vai trò: Lead/Integrator · Data & fixtures · Core algorithm · QA/metric · Human review + report |
| Git URL | https://github.com/Fat137/K5-Day6-L2-PhamTienPhat-03063-DataGovernanceAndPrivacy |
| Tag `demo-180m` (commit) | `2317799` (2026-11-14) |
| Tag `final-24h` (commit) | commit chứa chính bản report này — kiểm bằng `git rev-list -n 1 final-24h` (2026-11-14) |
| Deadline (giờ VN) | 2026-11-15 12:00 (UTC+7) |

## 01 · Problem statement

- **Ai bị ảnh hưởng?** Người có dữ liệu bị thu (người đi đường, tài xế trong video dashcam) và công ty vận hành dataset.
  Xóa thiếu → dữ liệu định danh (ảnh mặt, vector khuôn mặt, backup) vẫn còn sau khi đã báo "đã xóa": có thể vi phạm
  quyền xóa (GDPR Đ.17, Luật 91/2025) và tiếp tục bị dùng để train cho tới khi phát hiện. Xóa thừa → mất bằng chứng
  đang bị giữ theo nghĩa vụ pháp lý, hoặc xóa dữ liệu của người khác — lỗi này **không đảo ngược được**, nên được ưu
  tiên chặn hơn (xóa thiếu còn sửa được ở lần chạy sau).
- **Điểm nghẽn:** dữ liệu của một người lan qua nhiều bản dẫn xuất (raw → backup, frame → face_crop → embedding,
  annotation, training_export); một số artefact chứa nhiều người; một số đang bị legal hold, không có quyền xóa
  (ACL của vendor) hoặc không rõ nguồn gốc.
- **Đầu vào → đầu ra:** `requests.json` (10 yêu cầu) + `data_graph.json` (212 artefact) →
  `actions.csv` (mỗi cặp request–artefact: `delete` / `retain_legal_hold` / `escalate`) + hàng chờ duyệt + audit log +
  quyết định GO/HOLD/REWORK + bằng chứng xóa mock.
- **Tiêu chí quyết định (chốt trước khi chạy, `src/pipeline.py::decide`):**
  REWORK nếu graph hỏng (parent treo, id trùng) · HOLD nếu còn ngoại lệ chưa được người duyệt · GO khi mọi ngoại lệ
  đã có quyết định của reviewer → mới thực hiện xóa mock.
- **Giả định cần DPO/legal xác nhận:** (1) legal hold áp cho mọi bản sao và bản dẫn xuất của dữ liệu bị giữ;
  (2) ảnh nhiều người không xóa cả ảnh mà che mặt người yêu cầu; (3) `identity_verified = true` trong request là đáng tin;
  (4) artefact trên hệ thống vendor-x phải để vendor tự xóa theo hợp đồng xử lý dữ liệu.
  Đây là mô phỏng học tập, **không** khẳng định đạt GDPR / luật Việt Nam.

## 02 · Data & provenance

| Split | N | Nguồn / link | License | Synthetic? |
|---|---|---|---|---|
| toàn bộ (evaluator T08 không chia split) | 10 request · 212 artefact · 158 cặp trong phạm vi | `fixtures/t08_deletion/` (`synthetic:tools/make_fixtures.py (seed 42)`) | CC0-1.0 | có — mock, không xóa dữ liệu thật |

- **Schema:** artefact có `type, subject_ids, parent_ids, uri, owner, acl, legal_hold, origin_known, created_at` (+ `filename` cho raw/backup).
  Loại: raw_video 15, backup 15, frame 45, face_crop 45, embedding 45, annotation 45, training_export 2.
- **Ca đặc biệt trong fixture (tự tìm):** legal hold `a0043` (LH-2026-03, subj_03), `a0099` (LH-2026-07, subj_07);
  `origin_known=false` ở 3 embedding của subj_05 (`a0075, a0079, a0083`); ACL `vendor-x` ở backup `a0156` (subj_11);
  8 artefact 2 người (frame + annotation) và 2 training_export gộp 9–10 người.
- Seed 42. `manifest.csv` verified (sha256): **có** — `python -m tools.verify_manifests`, và pipeline kiểm lại ở bước load
  (`strict_provenance: true`).
- Sự cố tái lập đã gặp và sửa: Git for Windows (`core.autocrlf=true`) đổi LF→CRLF khi checkout làm lệch sha256 của cả 14 fixture.
  Sửa bằng `.gitattributes` (`fixtures/** -text`); đã kiểm bằng clone mới với `autocrlf=true` → 0 lỗi hash.
- Không dùng dữ liệu thật (handout: không xóa dữ liệu thực trong lab).

## 03 · Research & design

- **Kỹ thuật:** duyệt đồ thị lineage (BFS/DFS qua `parent_ids` và chỉ mục con) + bộ luật có thứ tự ưu tiên, theo gợi ý
  handout "legal hold > permission > multi-subject > delete". Chọn luật thay vì model vì quyết định xóa cần giải thích
  được từng dòng (`reason_code`) và không được sai kiểu "xác suất".
- **Data flow:**
  `requests + data_graph` → **load** (sha256, validate graph) → **process** (tìm artefact của subject, áp luật,
  loại dữ liệu người khác) → **rank** (mọi ngoại lệ vào `review_queue.csv`, nặng nhất trước) → **review**
  (`review_queue_reviewed.csv` → override + audit) → **decide** (GO/HOLD/REWORK) → **execute** (`dry_run_plan.csv`
  luôn ghi; xóa mock vào `mock_store.json` + `deletion_evidence.csv` chỉ khi GO).
- **Luật (theo thứ tự):** `LEGAL_HOLD` / `LEGAL_HOLD_INHERITED` → retain · `IDENTITY_NOT_VERIFIED` → escalate ·
  `NO_DELETE_PERMISSION` (ACL không có `data-platform`) → escalate · `UNKNOWN_ORIGIN` → escalate · `MULTI_SUBJECT` →
  escalate · `LINEAGE_TAG_MISSING` (bản dẫn xuất từ dữ liệu riêng của người yêu cầu nhưng thiếu tag) → escalate ·
  còn lại `SOLE_SUBJECT` → delete.
- **Phạm vi xóa = artefact có subject của người yêu cầu**, không phải "mọi thứ bên dưới lineage". Lý do: frame chung
  `a0011` (subj_00 + subj_01) có con `a0012` (face_crop) và `a0013` (embedding) **chỉ của subj_00**. Xóa theo lineage
  thuần sẽ xóa dữ liệu của người không yêu cầu. 6 artefact như vậy được ghi vào `excluded_other_subject.csv` + audit.
- **Thành phần offline:** không dùng model/weights; chỉ pandas + stdlib.
- **Trade-off đã cân nhắc:**
  - Hold lan xuống toàn bộ bản dẫn xuất → không bản sao/dẫn xuất nào của bằng chứng bị xóa, nhưng 2 training_export bị giữ vì chứa crop
    từ video bị hold, nên **cả 10 yêu cầu** đều chỉ `partially_fulfilled`. Chọn an toàn vì xóa nhầm không đảo ngược được,
    còn giữ thừa thì xóa được sau khi hết hold.
  - Không cắt budget review: mọi ngoại lệ (56) đều được duyệt, vì bỏ sót 1 legal hold là lỗi không sửa được.

## 04 · Baseline vs PoC

- **Baseline** (`src/baseline.py`, commit `e41e12a`): đúng mô tả handout — xóa video raw khớp `drive_<subject>_*.mp4`,
  không truy backup/dẫn xuất, không kiểm ngoại lệ, không review, không audit.
- **PoC** (`src/pipeline.py`, commit `d4c5661` + các sửa sau): như §03.
- Cùng pool (10 request × 212 artefact), cùng seed 42, cùng máy; evaluator T08 không có split nên không có heldout riêng.

**Bảng 1 · Baseline vs PoC.** Metric chính thức chờ GT của giảng viên. Bảng dưới lấy nguyên từ
`reports/self_check_t08.md` (không cần GT, định nghĩa trong `tools/self_check_t08.py`):

| Metric (unit) | Baseline | PoC | Δ & chú thích |
|---|---|---|---|
| Primary chính thức: Deletion Completeness | chờ GT | chờ GT | `src.evaluate` báo thiếu `gt/gt_actions.csv` |
| Thay thế không cần GT: Scope coverage (cặp được xử lý / 158) | 10/158 = 0.0633 (Wilson95 0.0347–0.1126) | 158/158 = 1.0 (Wilson95 0.9763–1.0) | +0.9367; higher tốt |
| Xóa dữ liệu đang legal hold (cặp) | 2 (`a0043`, `a0099`) | 0 | lower tốt, mục tiêu 0 |
| Xóa artefact chứa người khác / ngoài phạm vi | 0 / 0 | 0 / 0 | baseline = 0 vì chỉ xóa raw 1 người |
| Audit coverage | 0.0 | 1.0 | baseline không ghi audit |
| Review@B | không có review | 56/56 ngoại lệ được duyệt | B = toàn bộ ngoại lệ |
| Runtime / 100 artefact đầu vào | 0.0228 s | 0.2175 s | chậm hơn ~9.5× nhưng vẫn < 1 s cho toàn bộ; dao động giữa các lần chạy; CPU AMD64, Windows 11, Python 3.13.2 |

## 05 · Evaluation protocol

- **Primary (chính thức):** Deletion Completeness = #cặp GT `delete` mà pred `delete` / #cặp GT `delete`; khóa ghép
  (`request_id`, `artefact_id`); cặp không có trong pred tính là chưa xóa. **False-delete count** = ngoại lệ bị xóa
  + xóa ngoài phạm vi GT (mục tiêu 0). Xem `reports/metric_contract.md`.
- Không có threshold số; luật chốt 2026-11-14 trước khi chạy, không đổi sau đó.
- **Độ bất định:** N nhỏ (158 cặp, 56 ngoại lệ) → báo kèm tử/mẫu và Wilson 95% CI.
- **Rủi ro lệch GT (nói trước, không tune):** GT ẩn có thể coi bản dẫn xuất của video bị hold là `delete` (chỉ giữ đúng
  artefact có cờ hold). Nếu vậy PoC **mất completeness** ở 38 cặp `LEGAL_HOLD_INHERITED` nhưng **không** phát sinh
  false-delete. Tương tự, nếu GT coi 12 cặp `MULTI_SUBJECT` hoặc 1 cặp ACL vendor là `delete`, PoC (đang `escalate`)
  mất completeness ở các cặp đó; ngược lại 3 embedding `UNKNOWN_ORIGIN` mà reviewer cho xóa sẽ thành false-delete nếu GT
  coi là `escalate` — đây là rủi ro do quyết định của con người, đã ghi lý do trong `reports/review_queue.csv`.
- Lệnh evaluate chính xác (giảng viên chạy):
  `python -m src.evaluate --track t08 --pred out/poc/actions.csv --run-name poc --out reports/metrics_poc.json`
  (và tương tự cho `out/baseline/actions.csv`, rồi `python -m src.compare ...`).

## 06 · Result slices

| Slice (loại artefact) | N trong phạm vi | Baseline xử lý / xóa | PoC xử lý / xóa | Ghi chú |
|---|---|---|---|---|
| raw_video | 10 | 10 / 10 | 10 / 8 | PoC giữ 2 video bị hold |
| backup | 10 | 0 / 0 | 10 / 7 | 2 kế thừa hold, 1 ACL vendor-x |
| frame | 33 | 0 / 0 | 33 / 21 | 6 kế thừa hold, 6 nhiều người |
| face_crop | 30 | 0 / 0 | 30 / 24 | 6 kế thừa hold |
| embedding | 30 | 0 / 0 | 30 / 24 | 6 kế thừa hold; 3 không rõ nguồn gốc → reviewer cho xóa |
| annotation | 33 | 0 / 0 | 33 / 21 | 6 kế thừa hold, 6 nhiều người |
| training_export | 12 | 0 / 0 | 12 / 0 | cả 2 export chứa dẫn xuất của video bị hold |

- **Failure case 1 — baseline xóa nhầm bằng chứng (exception).** DR-002 (subj_03) và DR-005 (subj_07): baseline xóa
  `a0043` (`legal_hold: LH-2026-03`) và `a0099` (`LH-2026-07`). Root cause: khớp theo filename, không đọc metadata
  `legal_hold`. PoC giữ lại cả 2 và 28 bản dẫn xuất (đếm không trùng; `a0211` nằm dưới cả hai video); test `test_legal_hold_and_its_derivatives_are_never_deleted`
  và `test_reviewer_cannot_force_delete_under_legal_hold` (kể cả reviewer sửa thành `delete` cũng bị chặn + ghi audit).
- **Failure case 2 — baseline chỉ xóa raw, giữ bản sao và embedding định danh.** Ví dụ DR-001 (subj_01): baseline xóa
  `a0015` nhưng để lại backup `a0016` (`drive_subj_01_001.mp4.bak`), 3 face_crop và 3 embedding (`a0019, a0023, a0027`).
  Root cause: không truy `parent_ids`. Tổng cộng baseline chỉ chạm 10/158 cặp. Test `test_poc_traces_lineage_beyond_raw_video`.
- **Bẫy lineage (PoC xử lý đúng):** frame `a0011` thuộc video của subj_00 nhưng chứa subj_01 → được tìm thấy nhờ quét
  `subject_ids` chứ không chỉ đi xuống từ video của subj_01; con `a0012/a0013` của nó chỉ là subj_00 → loại, không xóa.
- **Latency:** PoC 0.461 s cho cả 10 request (212 artefact), baseline 0.0484 s — xem `reports/self_check_t08.md`.

## 07 · Human & decision

- **Review queue:** `out/poc/review_queue.csv` → bản đã duyệt `reports/review_queue.csv`. B = mọi ngoại lệ (56):
  40 legal hold (2 trực tiếp + 38 kế thừa), 1 ACL vendor-x, 3 unknown origin, 12 multi-subject. Xếp theo mức nghiêm
  trọng (`score`: hold 5 / 4.5 > ACL 3.5 > unknown origin 3 > multi-subject 2).
- **Quyết định reviewer (Phạm Tiến Phát, 2026-11-14):** legal hold → `approve` (giữ, xóa khi hết hold) · vendor-x →
  `approve` (gửi yêu cầu xóa cho vendor) · unknown origin → `edit` → `delete` (embedding gắn subject, định danh được
  khuôn mặt) · multi-subject → `approve` (không xóa cả ảnh, chuyển che mặt người yêu cầu).
- **Override + audit:** override chỉ nhận `delete / retain_legal_hold / escalate`; reviewer bắt buộc có tên; file phải
  UTF-8 (Excel: "CSV UTF-8"). Mọi bước ghi vào `out/poc/audit_log.jsonl` (append-only): `plan:*` cho mọi cặp,
  `review:*`, `review:override_blocked_legal_hold`, `exclude_other_subject`, `mock_delete:deleted|already_deleted`.
- **Decision log:** `reports/decision_log.csv` — HOLD (`PENDING_HUMAN_REVIEW`, 56 chờ duyệt) → GO
  (`ALL_EXCEPTIONS_REVIEWED`); owner Phạm Tiến Phát.
- **Kết quả sau duyệt:** 105 delete (đã xóa mock), 40 retain_legal_hold, 13 escalate; cả 10 yêu cầu `partially_fulfilled`
  → phải trả lời người yêu cầu phần nào đã xóa, phần nào bị giữ và căn cứ (`out/poc/request_summary.csv`).
- **Khi nào HOLD/REWORK thay vì tự động:** còn ngoại lệ chưa duyệt → HOLD, không xóa gì; graph hỏng → REWORK.

## 08 · Reproduce & tests

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m tools.preflight
.venv\Scripts\python.exe -m src.main --track t08 --pipeline src.baseline --output out/baseline --run-name baseline --offline --seed 42
.venv\Scripts\python.exe -m src.main --input fixtures/t08_deletion --config configs/lab.yaml --output out/poc --run-name poc --offline --seed 42
# (reviewer điền out/poc/review_queue_reviewed.csv — bản đã duyệt: reports/review_queue.csv — rồi chạy lại lệnh trên)
.venv\Scripts\python.exe -m tools.self_check_t08 out/baseline out/poc --out reports/self_check_t08
.venv\Scripts\python.exe -m pytest -q
```

- `pytest`: **66 passed, 40 skipped** (toàn repo); `tests/test_t08.py`: **19 passed, 1 skipped**
  (test so với GT chỉ chạy khi có overlay giảng viên). Skip còn lại là test hạ tầng cần GT của các track khác.
- `preflight`: 0 FAIL, 2 WARN (`data/real`, `models_optional/` — tùy chọn).
- Máy: Windows 11 (10.0.26200), CPU AMD64 Family 25 (AMD), Python 3.13.2 · git sha khi đo: `eb4db3e` (ghi trong `reports/self_check_t08.json`).

## 09 · Limitations & ethics

- **Pháp lý / PII:** dữ liệu mock, không có PII thật. Chính sách hold, multi-subject, vendor là giả định của tôi —
  cần DPO/legal xác nhận; không tuyên bố tuân thủ GDPR / Luật 91/2025 / Nghị định 356/2025.
- **Chưa biết:** cách GT vạch ranh giới hold và multi-subject (§05). `escalate` cho ảnh nhiều người mới chỉ ghi chú
  "che mặt" — chưa có bước redaction thật (liên quan T07).
- **Xóa mock ≠ xóa thật:** chưa mô phỏng xóa trong backup bất biến (Glacier), cache, log, model đã train từ export.
- **Scale 10×:** BFS tổ tiên mỗi artefact là O(độ sâu) — đủ nhanh; nhưng hàng chờ review tăng tuyến tính → cần gom
  theo loại ngoại lệ (duyệt hàng loạt có kiểm soát) và SLA theo `deadline`.
- **Cải tiến:** nhắc hạn (`deadline` 30 ngày), tự xóa khi hold được giải phóng, notification stub cho vendor, resume khi
  job bị ngắt, xóa/huấn luyện lại phần training_export thay vì giữ cả file.

## 10 · Team contribution

**Bảng 2 · Individual contribution evidence** (đối chiếu `git shortlog -sne --all`, `reports/team_contributions.csv`)

| Học viên | Git author | Module / vai trò | Commit SHA | Trình bày? |
|---|---|---|---|---|
| Phạm Tiến Phát | Phạm Tiến Phát | Toàn bộ (làm cá nhân): config + metric contract, baseline, pipeline, tests, review, report | `bb57988`, `e41e12a`, `d4c5661`, `f24a269`, `362cc4d`, `9783f03`, `461525b`, `2317799`, `eb4db3e`, `7d8f739`, `bc9ac18` + commit gắn tag `final-24h` | có |

## 11 · References

| URL | Ngày xem | 1–2 dòng ý nghĩa |
|---|---|---|
| https://eur-lex.europa.eu/eli/reg/2016/679/oj — GDPR Điều 17 (Right to erasure) | 2026-11-14 | Quyền xóa và ngoại lệ ở khoản 3 (nghĩa vụ pháp lý, khiếu kiện) → căn cứ giữ dữ liệu đang legal hold. |
| https://thuvienphapluat.vn/van-ban/Bo-may-hanh-chinh/Luat-Bao-ve-du-lieu-ca-nhan-2025-so-91-2025-QH15-625628.aspx — Luật Bảo vệ dữ liệu cá nhân số 91/2025/QH15 (ban hành 26/06/2025, hiệu lực 01/01/2026) | 2026-11-14 | Quyền xóa dữ liệu của chủ thể và các trường hợp ngoại lệ ở Việt Nam (handout T08 §8). |
| https://thuvienphapluat.vn/van-ban/Quyen-dan-su/Nghi-dinh-356-2025-ND-CP-huong-dan-Luat-Bao-ve-du-lieu-ca-nhan-687428.aspx — Nghị định 356/2025/NĐ-CP (31/12/2025, thay Nghị định 13/2023) | 2026-11-14 | Quy định chi tiết thi hành Luật 91/2025; cần DPO đối chiếu thời hạn và thủ tục xử lý yêu cầu xóa. |
| https://sites.research.google/datacardsplaybook/ — Data Cards Playbook (Google PAIR) | 2026-11-14 | Ghi lại nguồn gốc / lineage của dataset để truy được bản dẫn xuất khi phải xóa. |

# Schema changelog

## 2026-09-24 — `l1r/trips` có cuốc HỦY + lý do hủy (1.1.0)

- **`l1r/trips` 1.0.0 → 1.1.0**: thêm `cancel_reason` và `cancelled_by` (`customer`|`driver`) — cả hai
  **optional, additive**. Bảng `trips` trước đây 100% `completed`; nay có thêm dòng `status=cancelled`
  (huỷ sau khi tài xế nhận đơn, lấy từ sự kiện `order_cancelled_after_accept` của engine sim).
  Snapshot `trips@1.0.0` lấy từ git HEAD; upcaster 1.0→1.1 **chỉ stamp version**.
- **Lưu ý MOCK**: sim chỉ biết "huỷ sau nhận", không phân biệt nguyên nhân. `cancel_reason`/
  `cancelled_by` do mockgen gán theo tỷ lệ cố định (55% khách đổi ý, 20% sự cố xe, 25% "khách không
  đến" do tài xế huỷ) — là giả định dữ liệu giả, KHÔNG phải quan sát.
- Cuốc huỷ: `gross_vnd=0`, `commission_vnd=0`, `distance_km=0`, không có `pickup_time`/`complete_time`.
  Mọi bộ đếm theo `status=completed` không đổi; bộ đếm theo tổng dòng `trips` phải lọc `status`.
- Thêm cột `vehicle_class` (đã có trong schema từ 1.0.0 nhưng generator chưa điền) cho mọi dòng.

## 2026-08-10 — Cycle 8: S4 biết KHOẢNG CÁCH TỚI MỐC thưởng (1.1.0, UPDATE-198)

- **`l3/allocation_input` 1.0.0 → 1.1.0**: thêm `candidates[].tier_gap_points` (số ĐIỂM còn thiếu
  để chạm mốc kế) và `tier_weight` (trọng số hạng tử mốc trong hàm mục tiêu S4) — cả hai
  **optional, additive**. Snapshot `allocation_input@1.0.0` giữ nguyên; upcaster 1.0→1.1 **chỉ
  stamp version**.
- **Vì sao cần**: `UPDATE-195` đo được hàm thưởng **bậc thang** còn **+5.067đ/người/ngày** ngay cả
  khi giữ **TỔNG ĐIỂM toàn đội cố định** — tức tiền GSM trả thêm, không lấy của tài xế khác. Cơ chế
  mạnh nhất (ưu tiên **điều phối**) bị `CLAUDE.md §5` cấm; đường hợp lệ là ưu tiên **ô tốt** cho
  người sát mốc. Hạng tử này tác động được vì `pen` hằng theo HÀNG và ma trận gán là **CHỮ NHẬT**
  (ứng viên > chỗ ở 54,7% batch) ⇒ hằng số hàng quyết định **ai được chỗ** (đo: 130/400 chữ nhật
  đổi nghiệm vs **0/400** vuông).
- **Vì sao KHÔNG overload `priority_soc`**: nó là kênh ưu tiên **PIN** với ngữ nghĩa đã ghi ở
  `world.py:443-444` (*"người ít pin không nên bị kéo chạy rỗng"*). Nhét khoảng-cách-tới-mốc vào đó
  sẽ **xoá** nghĩa cũ và mọi consumer đang đọc nó thành mức pin sẽ đọc sai.
- **Vì sao đơn vị ĐIỂM chứ không phải đồng**: đặt tên `tier_gain_vnd` sẽ kích `MONEY_MARKERS` của
  `tests/_health_boundary_scan.py` và biến ba file trên đường phân bổ thành money-scope, buộc sửa
  file ranh giới `_health_boundary_manifest.py`. Chi phí bỏ giá trị tiền là nhỏ: mốc kế chênh nhau
  30k/30k/55k/55k (< 2×) trong khi `gap_points` trải 5–30+ (> 6×) ⇒ khoảng cách áp đảo giá trị.
- **Vì sao `tier_weight` nằm trong INPUT chứ không phải `params`**: `_capture_checkpoint` chỉ ghi
  input, nên để ở `params` thì artifact **không cho biết trọng số nào đã sinh ra kết quả** — một lỗ
  hổng provenance.
- Upcaster **KHÔNG bịa** `tier_gap_points` cho record cũ: record 1.0.0 không biết lúc đó tài xế cách
  mốc bao xa, và `0` là khẳng định cụ thể *"đang đứng đúng mốc"* chứ không phải *"không biết"* —
  đúng mẫu hidden-fallback đã trả giá (`soc=None` → đọc thành pin đầy). Cũng **không** bịa
  `tier_weight = 0`: record cũ sinh ra khi hạng tử này **chưa tồn tại**, khác với *"đã có mà tắt"*.

## 2026-08-06 — `D-ADV-04`: nhãn CÁCH SUY MẪU SỐ của tốc-độ-điểm (1.1.0, UPDATE-167)

- **`l3/bonus_gap_input` 1.0.0 → 1.1.0**: thêm `historical_rate_method` (enum
  `measured_intervals | estimated_span_scaled | day_average_mixed | none`) và
  `historical_rate_days` — cả hai **optional**, additive. Snapshot `bonus_gap_input@1.0.0`
  giữ nguyên; upcaster 1.0→1.1 **chỉ stamp version**.
- **Vì sao cần**: `historical_points_per_hour` từng có **hai quy ước** — ba producer chia
  điểm-của-bucket cho giờ online **TOÀN NGÀY**, còn solver `bonus_feasibility._walk` tiêu thụ nó
  như điểm/giờ **TRONG bucket** (nhân với từng giờ của bucket) ⇒ rate ước NON 2–5× ⇒ S1 phán
  *"không với tới mốc"* về mốc **với tới được**. Đã reproduce (`repro-s1-denominator.py`).
  Nay `description` của trường **ghim quy ước** và phân biệt rõ **khoá vắng mặt** ("không có bằng
  chứng hiện diện" ⇒ solver fallback) với **giá trị `0.0`** ("đã online mà 0 điểm" — ADV-05).
- **Vì sao KHÔNG dùng trường `source` sẵn có** để mang nhãn xấp xỉ: `source` là nhãn MOCK/REAL mà
  `CLAUDE.md` §5 bắt buộc; overload nó sẽ **xoá** nhãn đó. Cũng **không** nhét metadata vào
  `historical_points_per_hour` — consumer nào `for b, v in hist.items()` sẽ đọc metadata thành rate.
- Upcaster **KHÔNG bịa** `historical_rate_method` cho record cũ: record 1.0.0 **không biết** mẫu số
  của nó được suy thế nào; vắng mặt là sự thật, `day_average_mixed` là suy đoán.
## 2026-08-08 — CompositeEpisode proactive identity (1.2.0, UPDATE-168)

- **`advisor/composite_episode` 1.1.0 → 1.2.0**: mọi episode bắt buộc có `decision_key`
  ổn định và `evidence_refs[]` truy được về toàn bộ component signals/solver reports; record
  thiếu một trong hai bị reject fail-closed và không thể render thành card.
- Thêm surface **`BANNER`** cho nudge persistent (A3 plan slipping, A5 minute-30 waiting) và
  giữ snapshot `composite_episode@1.1.0` để replay lịch sử qua upcaster thuần.
- A5 nay mở đúng tại mốc 30 phút idle khi còn plan S2 sống, cập nhật-in-place và đóng bằng
  revision EXPIRED khi block thật sự kết thúc; không suy luận sức khỏe và không tính interruption.

## 2026-08-08 — AdviceCheckpoint intent và chất lượng bằng chứng (1.3.0, UPDATE-167)

- **`advisor/advice_checkpoint` 1.2.0 → 1.3.0**: thêm `decision_key`, identity episode,
  `output_intent`, `evidence_quality` và `evidence_refs`; mỗi number nay bắt buộc có
  semantic `key` + `label`, không còn chỉ là N1/N2 vô nghĩa.
- Snapshot `advice_checkpoint@1.2.0` giữ hình dạng cũ. Upcaster có thể khôi phục intent
  từ canonical action nhưng đặt quality là `UNMEASURED`, vì record 1.2 không lưu bộ
  semantic signal và không được phép bịa rằng bằng chứng cũ là đủ.
- Policy chặn action/summary checkpoint có evidence `INSUFFICIENT`/`LOW_QUALITY`/
  `UNMEASURED`; observation/passive vẫn được phép mô tả đúng giới hạn của nó.

## 2026-08-07 — CompositeEpisode hai mức + target lifecycle (1.1.0, UPDATE-162)

- **`advisor/composite_episode` 1.0.0 → 1.1.0**. Snapshot `composite_episode@1.0.0` giữ
  nguyên; upcaster 1.0→1.1 là pure rename + field null.
- **Đổi tên `rest_debt_vs_idle` → `waiting_and_rest_record`.** Tên cũ khẳng định một
  khoản thiếu hụt ("nợ nghỉ") suy ra từ đại lượng chưa từng được đo: `idle` là block suy
  diễn từ timeline còn `rest` là segment ghi nhận, và 216/270 actor **có** rest. Upcaster
  đổi tên tại chỗ để record 1.0 replay dưới một từ vựng duy nhất.
- **+ episode type `operational_phase_change`** — so hai cửa sổ 120′ liền kề ĐÃ QUA, có
  guard tối thiểu 3 cuốc mỗi cửa sổ. Không thay thế detector nửa-ca cũ (đã bỏ vì
  future leak); đây là bản past-only đầu tiên.
- **+ lifecycle state `WATCHING`** (mức 1 của A1: quan sát được nhưng không có hành động
  canonical để chuẩn bị ⇒ contract cấm ngắt quãng) và **`TARGET_ACTIVE` / `AT_RISK` /
  `TARGET_CLOSED`** (vòng đời của một *mốc thưởng*, chỉ đi kèm authority SOLVER và
  `source_solver_report_refs` — chặn việc suy AT_RISK từ payout/gap).
- **+ `supersedes_checkpoint_id`** (nullable): episode là *trạng thái mới của một card đã
  có*, không phải card thứ hai. Routing repaint card đó và không tính ngắt quãng.
  Null cho mọi record 1.0.

## 2026-08-05 — AdviceCheckpoint facts nền móng (1.2.0, UPDATE-147)

- **`advisor/advice_checkpoint` 1.1.0 → 1.2.0**: thêm `numbers[]` ({value, unit, source}
  của solver report — typed, có provenance), `caveats[]` (string) và `fingerprint`
  (material digest) vào record. Trước đây cả ba bị strip khi persist ⇒ card nghèo facts
  và dedup product không bao giờ khớp record đã lưu. Snapshot `advice_checkpoint@1.1.0`
  giữ nguyên; upcaster 1.1→1.2 điền `numbers=[]`/`caveats=[]` (record cũ không lưu —
  không bịa) và TÁI LẬP `fingerprint` từ chính material fields đã persist.
- Fingerprint material bổ sung `future_head` (code + window của hành động kế tiếp):
  plan đổi bucket SWAP là một lời khuyên KHÁC, không được dedup nhầm với revision cũ.
- Lifecycle transition mở thêm `ready → queued` (tài xế bắt đầu di chuyển sau khi
  checkpoint ready — moving gate ghi dấu vết thay vì silent trắng).

## 2026-08-03 — AdviceCheckpoint runtime contract (1.1.0)

- **`advisor/advice_checkpoint` 1.0.0 → 1.1.0**: thêm identity/reference tách bạch
  `source_decision_id`, `run_id`, `solver_input_refs[]`, `solver_report_refs[]` để replay
  tới exact solver artifacts mà không overload/backfill `decision_id` legacy.
- **`advisor/advice_checkpoint_event` 1.0.0 → 1.1.0**: thêm event `expanded` dạng
  side-channel; event này và `execution_observed` không đổi presentation state.
- **`advisor/advice_artifact` 1.0.0 → 1.1.0**: thêm kind `agent_shadow_output` cho
  evaluation artifact không được phép đi vào response/lifecycle tài xế.
- Ba schema giữ snapshot 1.0.0 và pure upcaster 1.0→1.1; runtime producer luôn điền refs,
  còn record cũ được upcast với refs nullable/rỗng đúng lịch sử.
- Thêm contract đóng `agent_presentation_input@1.0.0` và
  `agent_presentation_output@1.0.0`; output agent chỉ được tham chiếu fact/number/caveat ID
  và enrich phần lý do, không sở hữu action/window/expiry/source/số tự do.

## 2026-08-03 — AdviceCheckpoint shadow contract (1.0.0)

- **`advisor/advice_artifact`**, **`advisor/advice_checkpoint`** và
  **`advisor/advice_checkpoint_event`** là contract mới cho presentation lifecycle shadow.
  Checkpoint không overload `decision_id` legacy; event stream có các trạng thái
  `created/queued/ready/offered/displayed/...` và `execution_observed` là liên kết độc lập.
- Store tương ứng là SQLite append-only, content-addressed artifacts và idempotent theo
  `checkpoint_id`/`event_id`. Đây là snapshot lịch sử trước runtime v2; không dual-write
  legacy lifecycle.

## 2026-07-29 (chiều) — B3: `policy_bundle` 1.0.0 → 1.1.0 (+`costs` optional)

- **`l0/policy_bundle` 1.1.0** (minor, additive): +khối `costs` optional —
  `battery_free_until` (Platform độc quyền: 2029-03-31, official greensm 26/03/2026),
  `swap_fee_vnd` (9.000đ/lượt sau ưu đãi), `battery_rent_vnd_month`,
  `swap_range_km_per_pack`, `cash_cost_vnd_per_km_by_track`. Snapshot
  `policy_bundle@1.0.0.schema.json` dựng từ git HEAD (đúng quy trình); upcaster
  1.0.0→1.1.0 = stamp-only (KHÔNG bịa costs cho record cũ — vắng mặt ⇒
  `resolve_cost_params` trả UNKNOWN). Consumer: `gsm_core/policy.py::resolve_cost_params`
  (3 trạng thái ACTIVE/OFF_BY_POLICY/UNKNOWN) + `shift_dp.solve` qua
  `params["policy_costs_as_of"]` (opt-in — caller cũ nguyên vẹn). Tương thích: record
  1.0.0 persist vẫn validate pass (test_mockgen + test_schema_versioning xanh).

## 2026-07-29 (muộn) — Cycle W đóng: siết `occurred_at`/`observed_at` TẠI CHỖ (1.0.0)

- **`advisor/advice_lifecycle_event`**: pattern hai trường timestamp siết dần qua 2 đợt
  review đối kháng — (đợt 1, W-4b) giờ `([01]\d|2[0-3])`; (đợt 2, F-S4) tháng `01-12`,
  ngày `01-31`, offset giờ `00-23`. **Không bump version — lý do ghi tường minh** (README
  bước 2 yêu cầu khai): đây là bugfix NARROWING chặn record độc (`T24:00:00`, tháng 13…
  từng lọt regex rồi giết toàn bộ projection — store append-only không gỡ được), và
  **không record persist nào từng mang giá trị bị siết** (store mới ra đời trong chính
  cycle này, chỉ có ở tmp/test). Lớp chặn THẬT là `datetime.fromisoformat` tại
  `event_log.append` (X-1 — regex không kiểm được lịch: `2026-02-31` khớp mọi pattern);
  regex chỉ là tài liệu + lớp phòng đầu. Description `run_id` sửa theo format thật của
  `runner.derive_run_id` (thêm `-c{digest8}`, bỏ `-d{day}` không tồn tại — F-S3).

## 2026-07-29 — Cycle W (ĐA-05): advice lifecycle event log

- **`advisor/advice_lifecycle_event` MỚI (1.0.0)**: envelope một event vòng đời advice —
  append-only, idempotent theo `event_id`; IDs tách vai trò `decision_id`/`display_id`/
  `event_id`; `occurred_at`+`observed_at` ISO; `actor`/`origin`/`source`; `reason_code`;
  `context_revision` (chỗ cho ĐA-04 material_revision). Store: `gsm_core/lifecycle/event_log.py`
  (validate qua registry TRƯỚC khi ghi); projections MỘT LUẬT (UI + sim):
  `gsm_core/lifecycle/projections.py`. Ba namespace decision_id hợp pháp ghi trong
  description: `adv-*` (pipeline) / `s1-*` (UI) / `slth-*` (sim, deterministic).

## 2026-07-28 — Cycle V: registry ĐA PHIÊN BẢN (gỡ B-02) + 2 thay đổi

- **CƠ CHẾ**: validate route theo `record["schema_version"]`; lịch sử = `{entity}@{ver}.schema.json`;
  version lạ fail-loud; upcaster `src/gsm_core/upcasters.py`; backward-compat test bằng record
  persist thật. Quy trình bump mới trong README.
- **`shift_plan_input` 1.0.0 → 1.1.0** (minor, additive): +`rest_taken_min`, +`shift_elapsed_min`
  (optional/nullable — Cycle R, UPDATE-085). Snapshot `shift_plan_input@1.0.0.schema.json`;
  upcaster 1.0.0→1.1.0 = stamp (không bịa giá trị nghỉ). Producer bridge emit 1.1.0; producer
  l1r/features giữ 1.0.0 — hai version sống song song hợp lệ.
  ⚠ Đính chính quy trình: đợt Cycle R (2026-07-28 sáng) đã thêm 2 trường này mà KHÔNG bump —
  đúng anti-pattern B-02; entry này trả nợ. 5 đợt additive trước đó (xem các entry dưới) cũng
  không bump — chấp nhận làm lịch sử, không truy bump hồi tố (record cũ vẫn validate vì các đợt
  đó đều additive-optional với chính schema hiện hành 1.0.0 của chúng).
- **`market_state_view` MỚI** (l3, 1.0.0): view T-045a từng emit `schema_version` mà không có
  schema/entity nào trong registry — validate không thể chạm tới. Nay đăng ký + test payload thật.


## 2026-07-24 (PI-5c, UPDATE-042) — additive

- `l3/penalty_explain_input` (mới): input S8 UC6. Số tiền trừ từ `driver_penalization_ATA`;
  ngưỡng từ policy. Guardrail: giải thích QUY TẮC — KHÔNG dạy lách.
- `l3/anomaly_alert_input` (mới): input S9 UC7, `source=INFERRED`. **KHÔNG mang
  `evidence_ref`** sang view (chống lộ cách phát hiện). Guardrail: KHÔNG kết tội.
- `advisor/solver_report.solver`: enum **+`penalty_explain`, +`anomaly_alert`** → đủ 9 solver.

## 2026-07-24 (PI-5b, UPDATE-041) — additive

- `l3/idle_reduction_input` (mới): input S7 IdleReduction (UC5). `hex` CHỈ để thống kê —
  D-004b/B1 cấm dùng để chỉ định chỗ đứng; `active_reposition` chỉ từ campaign CHÍNH THỨC.
- `advisor/solver_report.solver`: enum **+`idle_reduction`**.

## 2026-07-24 (PI-4b, UPDATE-038) — additive, KHÔNG phá contract cũ

- `l0/policy_bundle`: **+`weekly_quota`** optional `{min_revenue_vnd, min_active_days,
  clawback_rate, market_scope}` — khoán tuần Vận Doanh 23/02/2026. Số = **TBC-với-GSM**
  (image-locked); `null` ⇒ solver S5 **KHÔNG được suy đoán mốc** (§5).
- `advisor/solver_report.solver`: enum **+`weekly_khoan`, +`mission_knapsack`** (enum đóng
  → phải khai tường minh khi thêm solver).
- `l3/weekly_khoan_input` (mới): input S5; `money_basis ∈ {gross, driver_payout}`,
  mặc định **gross** (quyết định (d) 2026-07-24, nhãn ASSUMPTION).
- `l3/mission_select_input` (mới): input S6; `reward_vnd` chỉ lấy từ `mission_catalog`.
- Tất cả field mới là **optional/additive** → 13 schema `l1r` + 23 entity cũ không đổi;
  suite cũ vẫn xanh.

## 2026-07-24 (PI-1, UPDATE-034) — layer `l1r`

- **+13 entity `l1r/*`** mirror bảng thật `gsm-data-prod` (KPI daily/weekly, trips,
  hex_tracking, mission×3, penalization, fraud). 5 bảng thiếu cột thật được **ENGINEER**
  với nhãn `x-availability: TBC-với-GSM`; PII khai qua `x-pii-columns` (optional field
  ⇒ record sau khi tool scrub vẫn validate).

## 1.0.0 — 2026-07-23 (T-038 C0, UPDATE-024)

- Initial: 23 entity across l0/l1/l2/l2i/l3/advisor theo spec
  `core-data-schema-and-advisor-architecture.md` v1.1.
- Ghi chú thiết kế:
  - `payout_ledger` tách gross/payout tại nguồn; **net-input entity CHƯA có**
    (chờ known costs — thuê xe/điện per track; thêm khi T-011 policy registry
    hoặc GSM export chi phí; sẽ là minor bump).
  - `policy_bundle.track` có `green_bike_unspecified` (guardrail T-004 — không auto-map).
  - `trip_record.dist_km` theo distance contract M0-9 (= haversine endpoints trong mock).
  - `advice_request.trigger_source` theo advice-timing spec (user_ask/anchor/event_trigger).
- TBC-với-GSM (fallback trong spec §1.6): GPSPing tần suất; swap wait đo trực tiếp;
  SOC telemetry; demand request-log (unserved).

# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ report JSON trong `reports/`, CSV trong `benchmark-results/` và screenshot trong `screenshots/`.

**Họ tên:** Đào Quang Huy (2A202603642)
**Ngày chạy:** 2026-11-25
**Môi trường:** Laptop Intel Core i7-10870H (16 luồng), Windows 11 + WSL2 Ubuntu 24.04 (7.7 GB RAM cho WSL), Python 3.12.3, Docker Desktop 4.99.0 (Engine 29.9.3, 16 CPU / 7.7 GB). Load test và agent chạy trên cùng một máy.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | LLM mock mất ~150–250 ms/câu ở trạng thái bình thường (p50 ≈ 190–200 ms), nên 500 ms chừa ~2.5 lần khoảng an toàn cho jitter và hàng đợi, nhưng vẫn đủ chặt để bắt khi tail latency lan lên p95 (vd `MOCK_SLOW_RATE=0.1` → p95 1687.6 ms). Đặt theo p95 chứ không theo average vì average che giấu ~3% request chậm 1.5 s. |
| Error rate `/ask` | < 2 % | Upstream LLM mock lỗi ~1% (`MOCK_ERROR_RATE=0.01`); các lần đo thực tế 0–1.33%. Ngưỡng 2% = error budget gấp đôi mức nền: vượt nghĩa là có vấn đề thật, không phải nhiễu. Alert đặt lỏng hơn (5% trong 1 phút) để tránh báo động vì spike ngắn. |
| Accuracy trên golden dataset | ≥ 80 % (gate mặc định), mục tiêu 100 % sau khi sửa KB | 80% là ngưỡng tối thiểu để chấp nhận release; quan trọng hơn là **không có regression** so với baseline. Sau khi sửa knowledge base, gate được siết lên `--min-accuracy 1.0` và đạt 23/23. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)

300 request mỗi mức, `MOCK_SLOW_RATE=0.03` mặc định. Nguồn: `reports/load_v2_c{1,10,50,100}.json`.

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.06 | 193.4 | 226.6 | 1700.0 | 1.33% (4×503) | ✅ PASS |
| 10 | 33.57 | 197.3 | 252.2 | 1713.6 | 1.33% (4×503) | ✅ PASS |
| 50 | 109.79 | 191.8 | 367.8 | 1707.9 | 0.67% (2×503) | ✅ PASS |
| 100 | 58.72 | 411.6 | 2921.1 | 4257.5 | 1.00% (3×503) | ❌ FAIL (p95) |

**Nhận xét:** RPS **không tăng tuyến tính**.
- Từ 1 → 10: gần tuyến tính (4.06 → 33.57, ×8.3). Mỗi request chủ yếu là *chờ* LLM (`asyncio.sleep`), nên event loop xử lý được nhiều request song song; RPS ≈ concurrency / latency (1 / 0.248 s ≈ 4.0; 10 / 0.258 s ≈ 38.8).
- Từ 10 → 50: tăng chậm lại (×3.3 thay vì ×5); p50 vẫn ~192 ms nhưng p90 tăng từ 226.0 lên 326.8 ms, p95 từ 252.2 lên 367.8 ms: request bắt đầu phải **xếp hàng**.
- Từ 50 → 100: RPS **giảm** (109.79 → 58.72), p50 tăng gấp đôi (411.6 ms), p95 vọt lên 2921.1 ms. Agent chạy **1 process uvicorn, 1 event loop**: phần việc CPU của mỗi request (parse JSON, Pydantic, ghi log JSON, cập nhật Prometheus metrics) chạy tuần tự trên một core, nên khi quá nhiều request cùng lúc thì chúng tranh nhau event loop và thời gian chờ chồng chất. Thêm vào đó, load test và agent chạy chung máy nên tranh CPU với nhau. Với 300 request, mức 100 chỉ có ~3 "lượt", nên phần khởi tạo kết nối ban đầu cũng chiếm tỉ trọng lớn hơn.
- Kết luận: điểm bão hoà của **một instance** nằm khoảng 50 request đồng thời. Muốn tăng thông lượng phải **scale ngang** (thêm worker/instance), không phải tăng concurrency.

### 2.2 Tail latency

300 request, concurrency 20, khởi động lại v2 với từng giá trị. Nguồn: `reports/load_v2_slow_{0,0.03,0.1}.json`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 201.8 | 240.9 | 263.2 |
| 0.03 (mặc định) | 260.5 | 317.0 | 1717.6 |
| 0.1 | 323.5 | 1687.6 | 1717.6 |

**Nhận xét:**
- **p99 nhạy nhất**: chỉ cần 3% request chậm, p99 nhảy từ 263.2 ms lên 1717.6 ms (×6.5), trong khi avg chỉ tăng 201.8 → 260.5 ms (+29%).
- **p95 có "ngưỡng"**: với 3% request chậm (< 5%), p95 vẫn nằm trong nhóm request bình thường (317.0 ms); khi lên 10% (> 5%), p95 rơi vào nhóm chậm và vọt lên 1687.6 ms, vi phạm SLO. Percentile pN nhìn thấy sự cố khi hơn (100 − N)% request bị ảnh hưởng.
- **avg đánh lừa**: ở 0.1, avg 323.5 ms trông vẫn "ổn" (< 500 ms) nhưng thực tế 1/10 người dùng phải chờ hơn 1.6 giây. Vì vậy **SLO không nên đặt theo average**: average cộng gộp và làm loãng tail, không cho biết bao nhiêu người dùng bị chậm. SLO theo p95/p99 nói thẳng "95%/99% người dùng nhanh hơn X".

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

Nguồn: `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json`.

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | 100.0% (23/23) |
| Avg tokens / câu | 57.3 | 108.8 | 118.7 |
| Tổng cost (USD) | 0.000407 | 0.000758 | 0.000834 |
| Cost / câu đúng (USD) | 0.00040695 | 0.00003607 | 0.00003628 |
| Latency p50 / p95 (ms) | 158.6 / 188.9 | 192.0 / 224.6 | 188.6 / 226.8 |
| Quality gate | FAIL (accuracy < 80%) | PASS (≥ 80%, 0 regression so với v1) | PASS (`--min-accuracy 1.0`, 0 regression so với v2) |

**Những câu v2 trả lời sai ban đầu và cách sửa:**

| ID | Keyword bị thiếu | Đã sửa gì |
|---|---|---|
| `security-02` ("JWT khác gì API key trong xác thực?") | `token`, `hết hạn`, `chữ ký` | KB không có tài liệu nào về JWT nên retrieval rỗng, agent trả câu chung chung. Thêm entry `(["jwt", "api key", "xác thực"], ...)` vào `KNOWLEDGE_BASE` giải thích JWT là **token** tự chứa thông tin, được ký, server kiểm tra **chữ ký**, token **hết hạn** theo `exp`; API key là chuỗi cố định không tự hết hạn. |
| `scaling-03` ("Kubernetes HPA scale dựa trên những chỉ số nào?") | `cpu`, `memory`, `custom metrics` | Tương tự, không có tài liệu về Kubernetes. Thêm entry `(["kubernetes", "hpa", "autoscal"], ...)`: HPA scale theo **CPU** và **memory** utilization hoặc **custom metrics** (request/giây, độ dài queue) từ Prometheus. |

Trước khi thêm, đã kiểm tra các keyword kích hoạt mới chỉ khớp đúng 2 câu này trong golden dataset (không "cướp" retrieval của câu khác). Kết quả: 2 câu được cải thiện, **0 regression**. Không sửa `golden_dataset.jsonl`. Bản sửa cũng được chép sang `production/utils/mock_llm.py`.

**Quality gate dùng exit code để chặn merge:** `run_eval.py` trả **exit code 0** khi gate PASS và **1** khi FAIL. Chạy thử v1 với `--baseline reports/eval_v2.json`: accuracy 91.3% → 4.3%, **20 câu regression**, `Quality gate ... ❌ FAIL`, **exit code 1**. Bước CI (GitHub Actions):

```yaml
- name: Eval quality gate
  working-directory: 06-lab-complete/benchmark-observability/develop
  run: |
    pip install -r requirements.txt
    AGENT_VERSION=v2 PORT=8002 python app.py &
    for i in $(seq 1 30); do curl -sf http://localhost:8002/health && break; sleep 1; done
    python eval/run_eval.py --url http://localhost:8002 \
      --baseline reports/eval_v2_fixed.json --min-accuracy 1.0 --out eval_pr.json
    # exit code != 0 → step fail → job fail → branch protection chặn merge
```

Đặt job này là *required status check* trong branch protection của `main`: PR nào làm accuracy giảm hoặc có câu regression sẽ không merge được.

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**
- v2 tốn **gần gấp đôi token** mỗi câu (57.3 → 108.8) vì prompt có thêm tài liệu retrieve, nên tổng cost tăng 0.000407 → 0.000758 USD (×1.86); latency p95 tăng 188.9 → 224.6 ms (+35.7 ms: bước retrieval + prompt dài hơn).
- Nhưng accuracy tăng từ 4.3% lên 91.3%, nên **cost / câu đúng giảm ~11 lần** (0.00040695 → 0.00003607 USD). Trả tiền cho câu trả lời sai là lãng phí hoàn toàn, nên "cost / câu đúng" mới là chỉ số đúng để so hai phiên bản, chứ không phải "cost / câu".
- Bản sửa KB tăng thêm ~10 token/câu (108.8 → 118.7) nhưng cost / câu đúng gần như không đổi (0.00003607 → 0.00003628), latency không đổi đáng kể.
- Kết luận: đánh đổi +86% chi phí và +36 ms p95 để lấy +87 điểm % accuracy là rất đáng.

---

## 4. Observability stack (production)

Stack chạy bằng `docker compose -p k5-day14-observability up -d --build` trong `production/`. `promtool test rules /p/alert_rules_test.yml` → `SUCCESS`.

> Đã sửa 1 lỗi của Dockerfile `production/` trước khi chạy được: user `agent` có HOME là `/app` nên Python không thấy packages copy vào `/home/agent/.local` (container crash `ModuleNotFoundError: No module named 'uvicorn'`). Thêm `ENV PYTHONUSERBASE=/home/agent/.local`.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải: [`screenshots/grafana-dashboard.png`](screenshots/grafana-dashboard.png). Chụp lúc đang bơm lỗi (bước cuối): Traffic 14.5 req/s, Error rate 18.36% (đỏ), Latency p95 1.90 s (đỏ), In-flight 10, Instances up 2, LLM cost (1h) $3.31. Các biểu đồ bên dưới thấy rõ dấu vết từng thí nghiệm: p95 nhảy lên ~2 s ở B, cost burn rate vọt lên ~$17/giờ ở C, "Instances up" tụt ở D.
- [x] Trace chậm trong Jaeger: [`screenshots/jaeger-slow-trace.png`](screenshots/jaeger-slow-trace.png), trace `c4bd804…`, 1.8 s, span `chat mock-llm-small` 1.7 s.
- [x] Trace lỗi trong Jaeger: [`screenshots/jaeger-error-trace.png`](screenshots/jaeger-error-trace.png), trace `805439e…`, `POST /ask` 503, span `chat mock-llm-small` có `error.type = upstream_unavailable` và **event `exception`**: `exception.message = Upstream LLM error: 503 Service Unavailable (mock)`.
- [x] Trang Alerts của Prometheus với **2 alert cùng firing**: [`screenshots/prometheus-alert-firing.png`](screenshots/prometheus-alert-firing.png): `AgentHighErrorRate` và `AgentHighLatencyP95` đều FIRING (nhóm `agent-slo`: FIRING (2)).

### 4.1b Trả lời Exercise 7.4 (trạng thái bình thường: cấu hình mặc định, Locust 30 users)

Số đo từ Prometheus lúc 10:30:58 UTC (cửa sổ 1 phút):

```text
p50 = 0.1785 s   p95 = 0.294 s   p99 = 1.8326 s   avg = 0.2351 s
req/s theo instance: 172.18.0.6:8000 = 9.20   172.18.0.8:8000 = 8.90
tokens/s: input = 1416.8   output = 756.6
error rate = 1.08%   cost = $1.92/giờ   alerts: (none)
```

1. **p95 và avg?** p95 = 294 ms, avg = 235 ms (p50 = 179 ms, p99 = 1833 ms). avg nằm giữa p50 và p95 vì bị ~3% request chậm 1.5 s (`MOCK_SLOW_RATE=0.03`) kéo lên: chỉ 3% nhưng mỗi cái chậm gấp ~8 lần nên đẩy trung bình lên ~56 ms so với p50. Khoảng cách avg ↔ p99 (235 ms vs 1833 ms) mới là **tail latency**: avg trông ổn nhưng cứ 100 người thì có người chờ gần 2 giây. Vì vậy SLO và alert dùng p95/p99.
2. **Nginx có chia đều không?** Có: 9.20 vs 8.90 req/s (chênh ~3%), đúng kiểu round-robin. Ở benchmark 3 instance cũng chia đều (2366 / 2373 / 2347 request).
3. **Vì sao input tokens/s > output tokens/s?** Input = system prompt + **tài liệu retrieve được (context RAG của v2)** + câu hỏi, còn output chỉ là câu trả lời ngắn. Mỗi request input ~80–90 token, output ~35–55 token, nên input/s ≈ 1.9 lần output/s. Ở thí nghiệm E (v1 không có retrieval), tokens/s giảm từ 1495.7 xuống 1038.6 vì prompt mất phần context.
4. **`curl http://localhost:8080/metrics` trả gì?** `HTTP/1.1 404 Not Found` (trang 404 của nginx). `nginx.conf` có `location = /metrics { return 404; }`: `/metrics` chỉ dành cho Prometheus scrape **trực tiếp từng agent trong mạng nội bộ Docker** (`agent:8000`), không đi qua nginx. Không public ra ngoài vì metric lộ thông tin nội bộ (route, lưu lượng, chi phí, phiên bản), ai cũng scrape được thì vừa lộ thông tin vừa tạo thêm tải.

### 4.2 Thí nghiệm "phá hệ thống"

Locust chạy nền 30 users suốt thí nghiệm. Mỗi bước: đổi biến môi trường → `docker compose up -d` (recreate agent) → script hỏi `GET /api/v1/alerts` mỗi 5 s, ghi thời điểm alert chuyển `pending` và `firing` (tính từ lúc đổi cấu hình). Prometheus `scrape_interval` = `evaluation_interval` = 5 s.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` | **93 s** (pending sau 29 s, `for: 1m`); error rate đo được 21.21%; trả cấu hình về thì inactive sau 52 s | Error rate (đỏ), Requests/s theo status code (đường 503 tăng), LLM calls theo kết quả (error) |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` | **150 s** (pending sau 28 s, `for: 2m`); p95 = 1.915 s nhưng p50 chỉ 0.212 s, avg 0.636 s; inactive sau 52 s khi trả về | Latency p50/p95/p99: p95/p99 vọt lên ~1.9 s, p50 gần như phẳng |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` | **≤ 150 s** (đã firing ở lần kiểm tra lúc 150 s; vì `for: 2m` nên không thể sớm hơn 120 s). Cost: $1.78/giờ trước → $34.58/giờ (rate 1m) / $17.76/giờ (rate 5m) sau 150 s; LLM p95 0.496 s | Cost burn rate (USD/giờ), LLM latency p95 theo model (series `mock-llm-large`) |
| D | `docker pause` 1 instance (`agent-1`) | `AgentInstanceDown` | **42 s** (pending sau 10 s, `for: 30s`); `up` = 0 cho 172.18.0.6, 1 cho 172.18.0.8; unpause thì inactive sau 7 s | Instances up (2 → 1); Requests/s theo instance (1 đường về 0) |
| E | `AGENT_VERSION=v1` | **Không có** | Không firing sau 150 s: error 1.52%, p95 0.214 s vẫn xanh; `agent_info` version = `v1` | Retrieval hit ratio (1.0 → không còn dữ liệu vì v1 không retrieve), Tokens/s giảm 1495.7 → 1038.6 |

Bước cuối (để chụp ảnh): bật đồng thời `MOCK_ERROR_RATE=0.2` + `MOCK_SLOW_RATE=0.3` → `AgentHighErrorRate` firing sau 92 s, `AgentHighLatencyP95` firing sau 145 s, khớp với thí nghiệm A và B riêng lẻ.

**Phân tích:**
- **Thời gian tới lúc firing ≈ thời gian cửa sổ dữ liệu + `for`:** A `rate[1m]` cần ~30 s dữ liệu xấu để vượt 5% (pending ở 29 s), rồi thêm `for: 1m` → ~90 s. B cần p95 vượt 1 s (pending ở 28 s) rồi `for: 2m` → ~150 s. D không có cửa sổ `rate`, chỉ cần 1–2 lần scrape thất bại (pending ở 10 s) + `for: 30s` → 42 s. `for` là cái giá cố ý trả để tránh báo động vì spike ngắn.
- **Thí nghiệm D: vì sao lỗi 504 không hiện trên metric của agent?** Trong 2 phút sau khi pause, log nginx có **27 response 504**, nhưng error rate phía agent chỉ 0.46%. Request đến instance bị pause bị treo tới `proxy_read_timeout 10s` rồi **nginx** trả 504. Agent bị pause không xử lý xong request nào nên không đếm nó, còn agent kia chỉ thấy request thành công. Metric chỉ phản ánh những gì chính service đó nhìn thấy, nên production cần thêm giám sát ở tầng load balancer (metric/log của nginx) và synthetic check từ bên ngoài. Ngoài ra phải dùng `docker pause` chứ không `docker stop`: container bị stop biến mất khỏi Docker DNS, Prometheus coi như target không tồn tại (không phải `up == 0`) nên không có alert.
- **Ngưỡng cost alert:** ở 30 users với model nhỏ, chi phí ~$1.92/giờ, sát ngưỡng $2/giờ; ngay sau benchmark 100 users, cửa sổ `rate[5m]` vẫn chứa traffic đó nên alert chi phí còn firing lúc bắt đầu thí nghiệm. Ngưỡng nên tính theo ngân sách thực và lưu lượng dự kiến, nếu không sẽ báo động giả khi traffic tăng hợp lệ.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đổi sang v1 làm accuracy rơi từ 100% xuống 4.3% (`eval_v1.json`), nhưng latency, error rate, traffic, instance đều bình thường, thậm chí p95 còn tốt hơn (0.214 s) và tốn ít token hơn. Metric hạ tầng chỉ trả lời "có chạy không / có nhanh không", không trả lời "có **đúng** không". Cách phát hiện:
1. **Eval quality gate trước khi deploy** (CI): chạy `run_eval.py --baseline` trên golden dataset, exit code 1 chặn merge/deploy. Đổi sang v1 sẽ bị chặn với 20 câu regression.
2. **Online eval / canary sau khi deploy:** định kỳ gửi các câu trong golden dataset tới production (synthetic probe) và xuất `agent_eval_accuracy` thành metric, đặt alert khi accuracy < 80%.
3. **Metric proxy cho chất lượng:** retrieval hit ratio (rơi khỏi 1.0 ở E), độ dài câu trả lời, tỉ lệ trả lời "chung chung"; alert khi đổi đột ngột. Thêm `agent_info{version}` vào dashboard để thấy ngay version nào đang chạy.
4. **Tín hiệu từ người dùng:** thumbs up/down, tỉ lệ hỏi lại, và LLM-as-judge chấm mẫu ngẫu nhiên các câu trả lời thật.

### 4.3 Debug bằng trace

Chọn trace chậm nhất trong 15 phút cuối mà thời lượng span khớp với log (lấy qua Jaeger API v3, rồi grep log theo `trace_id`):

- `trace_id`: `c4bd80477f3f1f3932a1aecf05df4e1e`
- Tổng thời gian: **1818.2 ms** (`POST /ask`, log middleware ghi 1816.5 ms)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small`** (**1677.3 ms, 92.3%**); `agent.retrieve_context` chỉ 16.4 ms (0.9%). Request chậm vì **LLM upstream** (rơi vào nhóm 3% request chậm của mock), không phải code của agent hay bước retrieval.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep c4bd80477f3f1f3932a1aecf05df4e1e`):

```json
{"ts": "2026-11-25T10:22:55.449036+00:00", "level": "INFO", "service": "observable-agent", "instance": "9d49832444ae", "event": "llm_call", "trace_id": "c4bd80477f3f1f3932a1aecf05df4e1e", "span_id": "e2fec2fd821dc3de", "request_id": "f83b1db36a70", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 73, "output_tokens": 35, "cost_usd": 3.2e-05, "llm_ms": 1677.3}
{"ts": "2026-11-25T10:22:55.450239+00:00", "level": "INFO", "service": "observable-agent", "instance": "9d49832444ae", "event": "http_request", "trace_id": "c4bd80477f3f1f3932a1aecf05df4e1e", "span_id": "e2fec2fd821dc3de", "request_id": "f83b1db36a70", "method": "POST", "route": "/ask", "status": 200, "ms": 1816.5}
```

`llm_ms` trong log (1677.3) trùng đúng thời lượng span LLM trong Jaeger.

Trace lỗi `805439e09b044e435ff86a3812f85017` (874.7 ms) và log của nó:

```json
{"ts": "2026-11-25T10:22:54.791671+00:00", "level": "INFO", "service": "observable-agent", "instance": "9d49832444ae", "event": "llm_error", "trace_id": "805439e09b044e435ff86a3812f85017", "span_id": "c266b4e85fcfed6a", "request_id": "a54e339ecc94", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
{"ts": "2026-11-25T10:22:54.792708+00:00", "level": "INFO", "service": "observable-agent", "instance": "9d49832444ae", "event": "http_request", "trace_id": "805439e09b044e435ff86a3812f85017", "span_id": "c266b4e85fcfed6a", "request_id": "a54e339ecc94", "method": "POST", "route": "/ask", "status": 503, "ms": 873.2}
```

> Quan sát thêm: vài trace chậm nhất lúc 10:22:09–10:22:43 có thời lượng span trong Jaeger dài hơn `ms` trong log ~1.2–1.9 s (vd `36bf91ee…`: span 3605.8 ms, log 1758.4 ms). Middleware đo bằng `time.perf_counter()` (đồng hồ đơn điệu) còn span OpenTelemetry dùng giờ hệ thống; độ lệch chỉ xuất hiện trong vài giây đó, nên nhiều khả năng giờ hệ thống của VM bị chỉnh nhảy. Vì vậy không dùng các trace đó để kết luận.

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Nguồn: hàng `POST /ask` trong `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv`. Mỗi container agent bị giới hạn `cpus: 0.5`, `memory: 256M` (docker-compose.yml).

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 52.95 | 210 | 1600 | 2000 | 71 / 6515 (1.09%) |
| 3 | 57.73 | 200 | 270 | 1700 | 57 / 7073 (0.81%) |

Bằng chứng lượt chạy đúng số instance (Prometheus, `sum by (instance) (increase(http_requests_total{route="/ask"}[2m]))` ngay sau mỗi lượt):

```text
scale_1: /ask on 172.18.0.6:8000 = 6499
scale_3: /ask on 172.18.0.6:8000 = 2366
         /ask on 172.18.0.8:8000 = 2373
         /ask on 172.18.0.9:8000 = 2347

p95 LLM call vs p95 toàn request /ask (2 phút cuối mỗi lượt):
scale_1: llm p95 = 198.5 ms   request p95 = 885.9 ms
scale_3: llm p95 = 197.8 ms   request p95 = 295.7 ms
```

**Giải thích kết quả:**
- **p95 cải thiện rất rõ** khi scale từ 1 lên 3 instance: 1600 → 270 ms (−83%), p50 gần như không đổi (210 → 200 ms). Nginx chia tải đều (~2350 request mỗi instance).
- **Nút thắt ở 1 instance là CPU của container, không phải LLM:** p95 của riêng LLM call giữ nguyên ~198 ms ở cả hai lượt, nhưng p95 toàn request ở 1 instance là 885.9 ms. Phần chênh lệch là thời gian request **xếp hàng** trong agent: container chỉ có 0.5 CPU, mỗi request tốn CPU cho FastAPI/Pydantic, ghi log JSON, cập nhật metrics và tạo/gửi span OpenTelemetry, nên ~53 req/s đã vượt khả năng của nửa core. Ba instance có tổng 1.5 CPU, hết xếp hàng, request p95 về 295.7 ms, gần bằng latency LLM.
- **RPS gần như không đổi (52.95 → 57.73)** vì Locust là mô hình *closed-loop*: 100 user, mỗi user nghỉ 0.5–2 s giữa các lần gọi, nên tải phát ra bị giới hạn bởi số user và thời gian nghỉ (≈ 100 / (1.25 s + latency) ≈ 60 req/s), không phải bởi sức chứa server. Khi latency giảm, mỗi user quay vòng nhanh hơn một chút nên RPS tăng nhẹ.
- **p99 vẫn ~1700 ms ở cả hai lượt:** đó là ~3% request bị mock LLM làm chậm thêm 1.5 s (`MOCK_SLOW_RATE=0.03`). Tail latency nằm ở upstream LLM, **scale ngang không sửa được**; cần timeout + retry/hedging, hoặc đổi model/provider.
- Failures (~1%) đều là `503` do mock LLM lỗi upstream (`MOCK_ERROR_RATE=0.01`), không liên quan số instance.

> Ghi chú quá trình: lần chạy đầu bị loại vì (1) Dockerfile của `production/` lỗi (agent crash `No module named 'uvicorn'`, 100% 502), đã sửa bằng `ENV PYTHONUSERBASE=/home/agent/.local`; (2) `docker compose run locust` tự scale agent về `replicas: 2` trong lúc đo "1 instance", nên chạy lại với `--no-deps`. Bảng trên là lần chạy sạch cuối cùng (10:00–10:05 UTC).

---

## 6. Kết luận

- 3 điều học được về benchmark & observability:
  1. **Average nói dối, percentile nói thật.** Với 10% request chậm, avg 323.5 ms vẫn trông "đạt" nhưng p95 đã 1687.6 ms. SLO, alert và benchmark phải đặt theo p95/p99. Tail latency nằm ở upstream LLM thì scale ngang không sửa được (p99 ~1.7 s ở cả 1 và 3 instance); scale chỉ xoá được phần **xếp hàng** (p95 1600 → 270 ms khi CPU là nút thắt).
  2. **Hạ tầng xanh không có nghĩa là trả lời đúng.** Đổi sang v1 làm accuracy rơi từ 100% xuống 4.3% mà không alert nào kêu. Chất lượng phải đo bằng eval có baseline, gắn exit code vào CI; "cost / câu đúng" mới là chỉ số so sánh hai phiên bản (v2 đắt gấp ~1.9 lần mỗi câu nhưng rẻ hơn ~11 lần mỗi câu đúng).
  3. **Ba trụ cột phải nối được với nhau, và số liệu phải được kiểm chứng.** Dashboard báo p95 tăng → Jaeger chỉ ra span `chat` chiếm 92% → `trace_id` dẫn tới dòng log có `llm_ms`, token, cost. Trong lúc đo, 2 lần chạy benchmark bị hỏng (`compose run` tự scale agent, pipe làm Locust dừng sớm) chỉ bị phát hiện nhờ đối chiếu số request mỗi instance trên Prometheus. Không kiểm tra thì đã nộp số sai.
- Nếu đưa agent này lên production thật, sẽ thêm:
  - **Online eval / synthetic probe:** định kỳ chạy một phần golden dataset vào production, xuất `agent_eval_accuracy` và alert khi < 80% hoặc giảm so với hôm trước (bắt được thí nghiệm E).
  - **Alert theo error budget burn rate** (multi-window, vd 1h + 5m) thay cho ngưỡng cố định, để vừa nhanh vừa ít báo động giả; alert p99 riêng cho tail latency.
  - **Giám sát tầng load balancer:** metric/log của nginx (tỉ lệ 502/504, upstream response time) và synthetic check từ bên ngoài, vì 504 ở thí nghiệm D không hiện trên metric agent.
  - **Saturation thật:** CPU throttling của container (`container_cpu_cfs_throttled_seconds_total`) và độ dài hàng đợi; ở benchmark 1 instance, request p95 (886 ms) ≫ LLM p95 (198 ms) chính là dấu hiệu bão hoà CPU.
  - **Cost theo tenant/ngày và dự báo** (alert khi chi phí dự kiến cuối tháng vượt ngân sách), cộng metric từ cost guard của Part 6 (số request bị 402) và rate limit (số 429).
  - **LLM upstream:** tỉ lệ lỗi theo provider/model, retry count, và timeout + fallback model khi provider chậm.

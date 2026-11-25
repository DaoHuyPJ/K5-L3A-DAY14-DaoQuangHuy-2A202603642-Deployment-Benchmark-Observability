# Day 14 Lab - Mission Answers

> Môi trường chạy: Ubuntu 24.04 trên WSL2 (`os.name = posix`), Python 3.12.3, Docker Desktop 4.99.0 (Engine 29.9.3).

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns found

Trong `01-localhost-vs-production/develop/app.py`:

| # | Anti-pattern | Dòng | Vì sao nguy hiểm |
|---|---|---|---|
| 1 | **Secret hardcode trong code**: `OPENAI_API_KEY = "sk-..."`, `DATABASE_URL = "postgresql://admin:password123@..."` | 17–18 | Push lên GitHub là lộ key và mật khẩu DB. Xoá file sau đó cũng không hết, vì secret vẫn nằm trong lịch sử git |
| 2 | **Log ra secret**: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | 34 | Key xuất hiện trong log, ai đọc được log (log aggregator, người vận hành) là lấy được key. Đã thấy thật trong log khi chạy (bên dưới) |
| 3 | **Config cố định trong code**: `DEBUG = True`, `MAX_TOKENS = 500` | 21–22 | Muốn đổi giữa dev/staging/prod phải sửa code và build lại; DEBUG bật sẵn khi lên production |
| 4 | **`print()` thay vì logging**: in cả câu hỏi và câu trả lời của người dùng | 33, 38 | Không có level, không có timestamp, không parse được bằng máy; còn ghi nội dung người dùng vào log |
| 5 | **Không có health check** (`/health`, `/ready`) | 42–43 | Platform không biết agent còn sống hay đã treo để restart, load balancer không biết khi nào nên ngừng gửi request. Gọi thử `/health` trả `404` |
| 6 | **Bind `host="localhost"`** | 51 | Chỉ nhận kết nối từ chính máy đó; chạy trong container thì bên ngoài không gọi vào được (cần `0.0.0.0`) |
| 7 | **Port cứng `port=8000`** | 52 | Railway/Render cấp port qua biến `PORT`; port cứng thì app không nhận traffic |
| 8 | **`reload=True`** | 53 | Chế độ dev: theo dõi file và tự khởi động lại, tốn tài nguyên và không ổn định trên production |
| 9 | **Không xử lý shutdown** (không lifespan, không xử lý SIGTERM) | — | Khi platform tắt/redeploy, request đang chạy bị cắt ngang, không có chỗ đóng kết nối DB |

### Exercise 1.2: Chạy bản develop

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found

{"detail":"Not Found"}
```

Log của server (cho thấy anti-pattern #2: secret bị in ra log):

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [3123] using WatchFiles
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Đây là câu trả lời từ AI agent (mock). ...
INFO:     127.0.0.1:32942 - "POST /ask?question=Hello HTTP/1.1" 200 OK
INFO:     127.0.0.1:32954 - "GET /health HTTP/1.1" 404 Not Found
```

**Quan sát:** app chạy và trả lời được, nhưng chưa production-ready: lộ secret trong log, không có health check, chỉ nghe trên `localhost:8000` và chạy chế độ reload.

### Exercise 1.3: So sánh với bản production

Chạy bản production (`cp .env.example .env`, nạp biến môi trường, `python app.py`):

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.2,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:43:48.343327+00:00"}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). ...","model":"gpt-4o-mini"}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{}'
HTTP/1.1 422 Unprocessable Entity
{"detail":"question field is required"}
```

Gửi `SIGTERM` (`kill -TERM <pid>`) khi server đang chạy, process tự thoát sạch:

```text
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
...
INFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
INFO:     Finished server process [3362]
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong `app.py` (key, DB URL, DEBUG, port) | Đọc từ biến môi trường qua `config.py` (`Settings`), có `.env.example` làm mẫu | Cùng một image chạy được ở dev/staging/prod chỉ bằng cách đổi env; secret không nằm trong code và git (12-Factor: Config) |
| Secret | Hardcode và còn `print` ra log | Lấy từ env; `validate()` bắt buộc `AGENT_API_KEY` khi `ENVIRONMENT=production` (fail fast); log chỉ ghi độ dài câu hỏi | Lộ key thì bị dùng trộm, tốn tiền, phải thu hồi key |
| Health check | Không có (`/health` → 404) | `/health` (liveness: còn sống không) và `/ready` (readiness: sẵn sàng nhận request chưa, trả 503 khi chưa) | Platform dùng `/health` để restart container treo; load balancer dùng `/ready` để không gửi traffic vào instance chưa sẵn sàng hoặc đang tắt |
| Logging | `print()` tự do | `logging` với format JSON và level | Log JSON parse được bằng máy (Loki, Datadog), lọc theo level/event; không ghi secret |
| Shutdown | Không xử lý, request đang chạy bị cắt | `lifespan`: tắt cờ `is_ready`, chờ request đang chạy xong, rồi dọn dẹp; có handler `SIGTERM` | Redeploy/scale down không làm lỗi request của người dùng |
| Host / Port | `localhost:8000` cứng | `HOST` mặc định `0.0.0.0`, `PORT` từ env | Container và cloud cần nghe trên `0.0.0.0` và port do platform cấp |
| Reload / Debug | `reload=True` luôn bật | `reload` chỉ bật khi `DEBUG=true` | Production không theo dõi file, chạy ổn định hơn |
| Validate input | `question` qua query, thiếu thì 422 mặc định của FastAPI | Body JSON, thiếu `question` thì 422 với thông báo rõ ràng | Client biết lỗi gì để sửa, không lộ chi tiết nội bộ |

**Phát hiện thêm khi chạy bản production:** các dòng log JSON (`"event": "startup"`, `"agent_request"`...) **không hề xuất hiện** trong output. Nguyên nhân: `config.py` gọi `logging.warning(...)` lúc import (dòng 45) **trước khi** `app.py` gọi `logging.basicConfig(...)` (dòng 28). `logging.warning` tự cấu hình root logger với level `WARNING`, nên `basicConfig` sau đó bị bỏ qua, và mọi log `INFO` bị nuốt. Đã kiểm chứng:

```text
after import config -> root handlers: [<StreamHandler <stderr> (NOTSET)>] level: WARNING
after basicConfig  -> level: WARNING (basicConfig ignored)
```

Cách sửa: gọi `logging.basicConfig(...)` trước khi import `config`, hoặc dùng `basicConfig(..., force=True)`. Bài học: log có cấu trúc chỉ có ích khi đã kiểm tra là nó thật sự được in ra.

## Part 2: Docker

### Exercise 2.1: Dockerfile questions

Đọc `02-docker/develop/Dockerfile`:

1. **Base image:** `python:3.11` (dòng 8), bản Python đầy đủ trên Debian, có sẵn gcc, header và nhiều công cụ build. Tiện nhưng rất nặng.
2. **Working directory:** `/app` (dòng 11). Mọi lệnh `COPY`, `RUN`, `CMD` sau đó chạy tương đối với `/app`.
3. **Vì sao `COPY requirements.txt` trước rồi mới `COPY app.py`:** Docker cache theo từng layer, và một layer bị build lại thì mọi layer sau nó cũng build lại. `requirements.txt` ít khi đổi, còn code đổi liên tục. Copy requirements và `pip install` trước thì khi chỉ sửa code, Docker dùng lại layer `pip install` đã cache (bước tốn thời gian nhất), chỉ build lại layer copy code. Nếu `COPY . .` trước thì mỗi lần sửa một dòng code là cài lại toàn bộ dependencies.
4. **`CMD` và `ENTRYPOINT`:**
   - `CMD ["python", "app.py"]` là lệnh **mặc định**, bị thay thế hoàn toàn nếu chạy `docker run my-agent:develop <lệnh khác>` (ví dụ `docker run -it my-agent:develop bash`).
   - `ENTRYPOINT` là chương trình **cố định** của container; tham số trong `docker run ...` (hoặc `CMD`) được **nối thêm** vào sau nó. Muốn đổi phải dùng `--entrypoint`.
   - Kết hợp thường gặp: `ENTRYPOINT ["uvicorn"]` + `CMD ["main:app", "--port", "8000"]`, khi đó `CMD` đóng vai trò tham số mặc định.

### Exercise 2.2: Build và chạy container

Build từ gốc repo (Dockerfile `COPY` theo đường dẫn tính từ gốc):

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -d --name cp2-dev -p 8000:8000 my-agent:develop

$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
HTTP/1.1 200 OK
server: uvicorn
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}

$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.8,"container":true}

$ docker exec cp2-dev sh -c 'whoami; python --version'
root
Python 3.11.18
```

Debug container: `docker logs cp2-dev` xem log (thấy `Uvicorn running on http://0.0.0.0:8000`), `docker exec` vào trong container chạy lệnh. Bản develop **chạy bằng user `root`**, là một điểm yếu về bảo mật.

### Exercise 2.3: Multi-stage build và so sánh kích thước

Đọc `02-docker/production/Dockerfile`:

- **Stage 1 (`builder`, dòng 16–29):** từ `python:3.11-slim`, cài `gcc`, `libpq-dev` để compile được các package cần build, rồi `pip install --user` vào `/root/.local`. Stage này **không được deploy**.
- **Stage 2 (`runtime`, dòng 36–72):** bắt đầu lại từ một `python:3.11-slim` sạch, tạo user không phải root `appuser`, chỉ copy `/root/.local` (các package đã cài) từ builder, copy `main.py` và `utils/`, chạy bằng `USER appuser`, có `HEALTHCHECK`, chạy `uvicorn` với 2 worker.
- **Vì sao nhỏ hơn:**
  1. Base `python:3.11-slim` thay cho `python:3.11` đầy đủ.
  2. `gcc`, `libpq-dev`, cache apt và cache pip nằm lại ở stage builder, không vào image cuối. `docker history my-agent:advanced` cho thấy layer packages chỉ **40.8 MB**, các layer code chỉ vài KB.

Số đo (`docker images my-agent`):

```text
IMAGE               ID             DISK USAGE   CONTENT SIZE
my-agent:advanced   bafb57b3328a        240MB         57.4MB
my-agent:develop    f25e2165183f       1.67GB          426MB
```

| | Develop (`python:3.11`, single-stage) | Production (`python:3.11-slim`, multi-stage) |
|---|---|---|
| Disk usage | **1.67 GB** (1,667,792,457 bytes) | **240 MB** (239,815,368 bytes) |
| Content size (nén, dung lượng phải tải về) | 426 MB | 57.4 MB |
| User chạy app | `root` | `appuser` |

- **Chênh lệch (disk usage):** (1667.8 − 239.8) / 1667.8 = **giảm 85.6%**, image production nhỏ khoảng **7 lần**.
- Theo content size: (426 − 57.4) / 426 = **giảm 86.5%**.
- Build lần đầu: develop mất 2m13s, production 34s (một phần nhờ base image slim nhỏ hơn).

### Exercise 2.4: Docker Compose stack

Chạy từ `02-docker/production/` (đã `touch .env.local`; dùng `-p` để không trùng tên project với Part 5 và Part 7):

```text
$ docker compose -p k5-day14-docker up -d --build
$ docker compose -p k5-day14-docker ps
SERVICE   IMAGE                   STATUS                            PORTS
agent     k5-day14-docker-agent   Up 3 seconds (health: starting)   8000/tcp
nginx     nginx:alpine            Up 3 seconds                      0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 15 seconds (healthy)           6333-6334/tcp
redis     redis:7-alpine          Up 15 seconds (healthy)           6379/tcp
```

**Sơ đồ kiến trúc:**

```text
                    Client (curl)
                        │  http://localhost:80
                        ▼
          ┌──────────── nginx (nginx:alpine) ─────────────┐
          │ cổng 80 publish ra host · rate limit 10 r/s   │
          │ security headers · proxy_pass agent_backend   │
          └───────────────────────┬───────────────────────┘
   network "internal" (bridge)    │ http://agent:8000
                                  ▼
          ┌──────────── agent (multi-stage image) ────────┐
          │ uvicorn main:app, 2 workers, user appuser     │
          │ không publish cổng, chỉ nginx gọi được        │
          └──────────┬──────────────────────┬─────────────┘
                     │ redis://redis:6379/0 │ http://qdrant:6333
                     ▼                      ▼
          ┌──── redis:7-alpine ───┐  ┌── qdrant/qdrant:v1.9.0 ──┐
          │ cache, rate limit     │  │ vector DB cho RAG        │
          │ volume redis_data     │  │ volume qdrant_data       │
          └───────────────────────┘  └──────────────────────────┘
```

**Các service giao tiếp thế nào:**
- Cả 4 service nằm chung network bridge `internal` (`k5-day14-docker_internal`). Docker có DNS nội bộ nên các service gọi nhau bằng **tên service**: `agent:8000`, `redis:6379`, `qdrant:6333`.
- **Chỉ nginx publish cổng ra host** (80). Agent, Redis, Qdrant không mở cổng ra ngoài, nên muốn vào agent phải đi qua nginx (rate limit, security headers).
- **Thứ tự khởi động:** `depends_on` với `condition: service_healthy`, nên agent chỉ start khi Redis và Qdrant đã healthy. Log `up` cho thấy đúng thứ tự: `redis Healthy`, `qdrant Healthy`, rồi mới tới `agent Starting`, `nginx Starting`.
- Dữ liệu Redis và Qdrant lưu trong named volume, không mất khi container bị tạo lại.

**Test qua Nginx:**

```text
$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block
{"status":"ok","uptime_seconds":1.9,"version":"2.0.0","timestamp":"2026-11-25T08:54:19.769442"}

$ curl -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question":"Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

$ docker compose -p k5-day14-docker exec agent whoami
appuser
```

Header `Server: nginx` và các security header chứng tỏ request đi qua nginx rồi mới tới agent. Log agent in `Agent ready` hai lần, ứng với 2 worker của uvicorn.

**Nhận xét thêm:**
- `main.py` hiện **chưa dùng** `REDIS_URL` và `QDRANT_URL`; chúng chỉ được truyền vào qua env. Stack này minh hoạ cách nối các service, còn agent thật cần đọc các biến đó để kết nối.
- Cổng 443 được publish nhưng `nginx.conf` không có `listen 443` và chưa có chứng chỉ, nên HTTPS chưa hoạt động.
- Compose báo `the attribute version is obsolete`: Compose bản mới bỏ qua dòng `version: "3.9"`.

## Part 3: Cloud Deployment

### Exercise 3.1: Railway deployment (agent Part 6)

- **URL:** https://agent-production-4d28a07b.up.railway.app
- **Nền tảng:** Railway, project `k5-day14-agent`, gồm service `agent` (build `06-lab-complete/Dockerfile` theo `railway.toml`) và service `Redis`.
- **Biến môi trường** đặt trên Railway, không có trong code: `AGENT_API_KEY` (secret ngẫu nhiên), `REDIS_URL=${{Redis.REDIS_URL}}`, `ENVIRONMENT=production`, `RATE_LIMIT_PER_MINUTE=10`, `DAILY_BUDGET_USD=1.0`, `MONTHLY_BUDGET_USD=10.0`; `PORT` do Railway cấp (`8080`).
- **Chi tiết và ảnh:** [DEPLOYMENT.md](DEPLOYMENT.md), ảnh trong `screenshots/` (`dashboard.png`, `running.png`, `test.png`).

4 lệnh tự kiểm của CP6:

```text
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"
200
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"
200
$ curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'
401
$ curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'
200
```

`/ready` trả `{"ready":true,"redis":"ok"}`, nghĩa là agent trên cloud đã kết nối được Redis của Railway. Rate limit 10/phút cũng hoạt động trên cloud (10 lần 200, sau đó 429 kèm `retry-after`).

### So sánh `railway.toml` và `render.yaml`

Cả hai file đều nằm trong `06-lab-complete/`, mô tả cùng một agent:

| Khía cạnh | `railway.toml` | `render.yaml` |
|---|---|---|
| Phạm vi | Chỉ cấu hình **một service** (cách build + deploy) | **Blueprint** mô tả cả hạ tầng: web service + Redis (Key Value) trong một file |
| Build | `builder = "DOCKERFILE"`, `dockerfilePath` | `runtime: docker`, `rootDir: 06-lab-complete`, `dockerfilePath` |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout = 30` | `healthCheckPath: /health` |
| Restart | `restartPolicyType = "ON_FAILURE"`, tối đa 3 lần | Render tự restart, không cấu hình trong file |
| Redis | Tạo riêng bằng `railway add --database redis`, nối bằng biến tham chiếu `${{Redis.REDIS_URL}}` | Khai báo luôn service `type: keyvalue`, nối bằng `fromService: ... property: connectionString` |
| Secret | Không nằm trong file; đặt bằng CLI/dashboard (`railway variables`) | Khai báo tên trong file nhưng giá trị dùng `generateValue: true` (Render tự sinh) hoặc `sync: false` (nhập tay trên dashboard) |
| Biến thường | Đặt trên dashboard/CLI | Ghi luôn trong `envVars` (`RATE_LIMIT_PER_MINUTE`, budget...) |
| Thư mục gốc | Chạy `railway up` ngay trong `06-lab-complete/` | Render đọc repo từ GitHub nên phải chỉ rõ `rootDir` |
| Format | TOML | YAML |

**Vì sao khác nhau:** Railway thiên về CLI và dashboard: file chỉ giữ phần build/deploy, còn biến và database quản lý bên ngoài, nên deploy nhanh từ máy local (`railway up`). Render theo hướng **Infrastructure as Code** qua Git: toàn bộ hạ tầng (service, database, biến, cách nối) nằm trong một file được commit, tạo lại môi trường chỉ cần trỏ Blueprint vào repo, nhưng phải push code lên GitHub trước. Điểm chung: PORT do platform cấp (Dockerfile dùng `${PORT:-8000}`), health check `/health`, và secret không bao giờ ghi thẳng giá trị.

### Exercise 3.3: Pipeline Cloud Run

Đọc `03-cloud-deployment/production-cloud-run/cloudbuild.yaml` và `service.yaml` (không deploy):

1. **Trigger:** push lên nhánh `main` kích hoạt Cloud Build chạy `cloudbuild.yaml` (hoặc chạy tay `gcloud builds submit`).
2. **Test:** bước `test` chạy trong `python:3.11-slim`: cài `requirements.txt` + `pytest`, chạy `pytest tests/`. Test fail thì pipeline dừng, không build.
3. **Build:** `docker build` image với 2 tag `ai-agent:$COMMIT_SHA` (truy vết đúng commit) và `ai-agent:latest`, dùng `--cache-from latest` để tận dụng layer cache.
4. **Push:** đẩy cả hai tag lên Container Registry `gcr.io/$PROJECT_ID`.
5. **Deploy:** `gcloud run deploy ai-agent --image ...:$COMMIT_SHA` tới `asia-southeast1`, min 1 / max 10 instance, 512Mi, 1 CPU, `ENVIRONMENT=production`, secret lấy từ **Secret Manager** (`--set-secrets`). `service.yaml` mô tả cùng service dưới dạng Knative (IaC): concurrency 80, `livenessProbe` gọi `/health`, `startupProbe` gọi `/ready`, `AGENT_API_KEY`/`OPENAI_API_KEY` qua `secretKeyRef`.

Mỗi bước có `waitFor` bước trước (test → build → push → deploy), nên chỉ code qua test mới lên production. Deploy theo tag `$COMMIT_SHA` thay vì `latest` thì rollback chỉ là deploy lại SHA cũ.

## Part 4: API Security

### Exercise 4.1: API Key authentication (`04-api-gateway/develop`)

Chạy: `AGENT_API_KEY=secret-key-123 python app.py`

- **Key được check ở đâu:** trong dependency `verify_api_key()` (`app.py` dòng 39–54), đọc header `X-API-Key` qua `APIKeyHeader`. Endpoint `/ask` gắn nó bằng `Depends(verify_api_key)` (dòng 70). Giá trị đúng lấy từ biến môi trường `AGENT_API_KEY` (dòng 35). `/` và `/health` không gắn dependency nên vẫn public (platform cần gọi `/health`).
- **Sai key thì sao:** thiếu header thì **401** `Missing API key`; có header nhưng sai thì **403** `Invalid API key.` Request bị chặn trước khi chạm vào LLM.
- **Rotate key thế nào:** key nằm trong env, không nằm trong code, nên chỉ cần đổi `AGENT_API_KEY` trên platform rồi restart/redeploy, không phải sửa code. Hạn chế: app chỉ nhận **một** key, nên lúc đổi thì mọi client đang dùng key cũ bị 403 ngay. Cách tốt hơn là cho phép nhận 2 key cùng lúc (ví dụ `AGENT_API_KEYS=key_cu,key_moi`), chuyển client sang key mới rồi mới bỏ key cũ.

```text
$ curl -i http://localhost:8000/ask -X POST -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

### Exercise 4.2: JWT authentication (`04-api-gateway/production`)

**JWT flow (`auth.py`):**
1. Client gửi `username/password` tới `POST /auth/token`. `authenticate_user()` kiểm tra, sai thì 401.
2. `create_token()` tạo JWT gồm `sub` (username), `role`, `iat`, `exp` (60 phút), ký bằng HS256 với `JWT_SECRET`.
3. Client gửi `Authorization: Bearer <token>` ở các request sau.
4. `verify_token()` kiểm tra chữ ký và hạn dùng. Không có token thì 401, hết hạn thì 401, chữ ký sai hoặc token giả thì 403. Server lấy `username`, `role` ngay trong token, **không cần tra DB mỗi request** (stateless).

Payload của token đọc được mà không cần secret, vì JWT chỉ được **ký** chứ không **mã hoá**: `{'sub': 'student', 'role': 'user', 'iat': 1795596972, 'exp': 1795600572}`. Vì vậy không được đặt dữ liệu nhạy cảm vào payload.

```text
$ curl -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
{"access_token":"eyJ***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"..."}

$ curl -i -X POST http://localhost:8000/auth/token ... -d '{"username":"student","password":"nope"}'
HTTP/1.1 401 Unauthorized
{"detail":"Invalid credentials"}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
x-content-type-options: nosniff
x-frame-options: DENY
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer abc.def.ghi" ...
HTTP/1.1 403 Forbidden
{"detail":"Invalid token."}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","usage":{"requests_remaining":9,"budget_remaining_usd":1.6e-05}}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" ... -d '{"question":""}'
HTTP/1.1 422 Unprocessable Entity
{"detail":[{"type":"string_too_short","loc":["body","question"],"msg":"String should have at least 1 character",...}]}
```

### Exercise 4.3: Rate limiting

- **Thuật toán:** sliding window. Mỗi user có một `deque` lưu timestamp các request (`rate_limiter.py` dòng 305). Mỗi lần check, bỏ các timestamp cũ hơn 60 giây (dòng 317–318), nếu còn ≥ `max_requests` thì trả 429. Docstring ghi "Sliding Window Counter", nhưng thực chất là **sliding window log** (lưu từng timestamp, không phải đếm theo ô thời gian), nên chính xác nhưng tốn bộ nhớ theo số request.
- **Limit:** user thường **10 request/phút** (`rate_limiter_user`), admin **100 request/phút** (`rate_limiter_admin`), dòng 364–365.
- **Bypass cho admin:** `app.py` dòng 141 chọn limiter theo `role` lấy từ JWT: `role == "admin"` thì dùng `rate_limiter_admin`. Admin không được miễn hẳn, chỉ có hạn mức cao hơn. Đăng nhập `teacher/teach456` (role admin) gọi liền 15 request đều 200.
- **Hạn chế:** state nằm trong RAM của process, nên chạy nhiều instance thì mỗi instance đếm riêng (user được gấp N lần limit), restart là mất. Production cần lưu trong Redis (đây là việc của Part 6).

Vòng lặp 20 request bằng token `student` (đã dùng 1 request ở 4.2, nên còn 9):

```text
req 1 -> 200  {"question":"Test 1",...,"usage":{"requests_remaining":8,...
req 2 -> 200
...
req 8 -> 200  {...,"usage":{"requests_remaining":1,...
req 9 -> 200  {...,"usage":{"requests_remaining":0,...
req 10 -> 429  {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
...
req 20 -> 429  {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" ... -d '{"question":"one more"}'
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795597034
retry-after: 59
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

Tổng cộng đúng 10 request được 200 trong 1 phút, từ request thứ 11 trở đi là 429. Response 429 có `Retry-After` để client biết bao lâu nữa thử lại.

### Exercise 4.4: Cost guard implementation

**Cách `cost_guard.py` đang làm:**
- **Trước khi gọi LLM**, `check_budget(user)` (dòng 47–78): nếu tổng chi của cả hệ thống ≥ $10/ngày thì **503**; nếu chi của user ≥ $1/ngày thì **402 Payment Required**; tới 80% thì log warning.
- **Sau khi gọi LLM**, `record_usage()` cộng số token vào và quy ra tiền theo giá GPT-4o-mini ($0.15/1M input, $0.60/1M output).
- Reset theo ngày: record mang theo `day`, sang ngày mới thì tạo record mới.

Kiểm tra bằng `/me/usage` sau 10 request: `{"requests":10,"input_tokens":40,"output_tokens":308,"cost_usd":0.000191,"budget_usd":1.0,...}`. Chi phí mỗi request rất nhỏ nên khó chạm $1 bằng tay. Để thấy 402, chạy chính class `CostGuard` với budget nhỏ (`daily_budget_usd=0.0001`, không sửa file):

```text
User student at 81% budget
request 1: ok, used=$0.000027
request 2: ok, used=$0.000054
request 3: ok, used=$0.000081
request 4: ok, used=$0.000108
request 5: HTTP 402 -> {'error': 'Daily budget exceeded', 'used_usd': 0.000108, 'budget_usd': 0.0001, 'resets_at': 'midnight UTC'}
```

**Implement theo yêu cầu đề ($10/tháng mỗi user, lưu trong Redis, reset đầu tháng):**

```python
import os
import redis
from datetime import datetime, timezone

r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
MONTHLY_BUDGET_USD = 10.0

def check_budget(user_id: str, estimated_cost: float) -> bool:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    key = f"budget:{user_id}:{month}"           # sang tháng mới là key mới -> tự reset
    new_total = r.incrbyfloat(key, estimated_cost)  # cộng nguyên tử
    r.expire(key, 32 * 24 * 3600)               # tự dọn sau ~1 tháng
    if new_total > MONTHLY_BUDGET_USD:
        r.incrbyfloat(key, -estimated_cost)     # vượt thì hoàn lại phần vừa cộng
        return False                            # caller trả 402
    return True
```

Lý do thiết kế:
- **Key theo tháng** `budget:<user>:<YYYY-MM>`: sang tháng mới tự dùng key mới, không cần cron reset. `EXPIRE` 32 ngày để Redis tự xoá key cũ.
- **Lưu trong Redis chứ không trong RAM:** nhiều instance dùng chung một con số, restart không mất.
- **Cộng trước rồi kiểm tra** bằng `INCRBYFLOAT` (nguyên tử) thay vì `GET` rồi `SET` như lời giải mẫu. Lời giải mẫu có race condition: hai request đến cùng lúc đều đọc thấy còn budget và cùng đi qua. Cộng nguyên tử rồi hoàn lại khi vượt thì không bao giờ cho qua quá budget.
- Dùng **UTC** để mọi instance (có thể khác múi giờ) reset cùng lúc.

Đã chạy thử với Redis thật (container `redis:7-alpine`):

```text
sequential: [True, True, True, False, False]          # 3 x $3 = $9 qua; lần 4 lên $12 thì bị chặn
concurrent 50 x $1: allowed = 10  stored total = 10.0  ttl_days = 32.0
```

50 request $1 gửi đồng thời, đúng 10 request được qua và tổng lưu trong Redis đúng $10.0, không bị vượt budget.

**Rate limit và cost guard bảo vệ hai thứ khác nhau, cần cả hai:** rate limit chặn **tốc độ** (spam, bot, một người làm nghẽn server), còn cost guard chặn **tổng tiền**. Một user gọi đều 10 request/phút không vi phạm rate limit, nhưng gọi suốt cả tháng với prompt dài vẫn có thể đốt hết ngân sách; ngược lại, cost guard không ngăn được một đợt spam làm nghẽn server trong vài giây.

**Lỗi phát hiện khi chạy:** `app.py` dòng 160 trả `"budget_remaining_usd": usage.total_cost_usd`, tức là trả về số tiền **đã dùng** (`1.6e-05`) chứ không phải số còn lại. `/me/usage` mới tính đúng (`budget_remaining_usd: 0.999809`).

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop`)

`develop/app.py` đã có sẵn hai endpoint:
- **`/health` (liveness, dòng 104–144):** trả 200 nếu process còn chạy, kèm uptime, version, kiểm tra bộ nhớ (`degraded` nếu RAM > 90%). Không kiểm tra dependency bên ngoài: nếu Redis/DB chết mà `/health` trả lỗi thì platform sẽ restart container liên tục, trong khi restart không sửa được Redis.
- **`/ready` (readiness, dòng 147–168):** trả **503** khi `_is_ready = False` (đang khởi động hoặc đang tắt), 200 khi sẵn sàng. Load balancer dựa vào nó để quyết định có gửi traffic vào instance này không. Trong bản production (`production/app.py` dòng 208–215), `/ready` còn `ping` Redis, Redis chết thì 503.

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":0.7,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T08:58:31.001996+00:00","checks":{"memory":{"status":"ok","used_percent":13.9}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}
```

(`in_flight_requests` = 1 vì middleware đếm luôn chính request `/ready` đang chạy.)

### Exercise 5.2: Graceful shutdown

Cách app xử lý (đối chiếu 4 bước của đề):
1. **Ngừng nhận request mới:** uvicorn bắt SIGTERM, đóng socket lắng nghe (`Shutting down`).
2. **Hoàn thành request đang chạy:** uvicorn chờ các request dở dang xong, tối đa `timeout_graceful_shutdown=30` giây (dòng 198); middleware `track_requests` đếm `_in_flight_requests`.
3. **Đóng kết nối:** phần sau `yield` trong `lifespan` (dòng 54–66) đặt `_is_ready = False` và là chỗ để đóng DB/Redis.
4. **Thoát:** process kết thúc với exit code 0.

Test: gửi một request `/ask` (mock LLM trễ 3 giây), sau 0.5 giây gửi `kill -TERM`:

```text
kill -TERM 5132 at +0.5s
new request during shutdown -> HTTP 000        # bị từ chối kết nối: server đã ngừng nhận request mới
long request -> HTTP 200 after 3.114933s       # request đang chạy vẫn hoàn thành
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
process exited after 3.586997778s
```

Log server theo đúng thứ tự:

```text
INFO:     Shutting down
INFO:     127.0.0.1:43686 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-25 08:58:34,190 INFO 🔄 Graceful shutdown initiated...
2026-11-25 08:58:34,190 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [5132]
2026-11-25 08:58:34,192 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

**Kết luận:** request đang chạy **có hoàn thành** (200 sau 3.1 giây), request mới bị từ chối, process tự thoát sau khoảng 3.6 giây. Nếu là SIGKILL (`kill -9`) thì process chết ngay và request bị cắt.

**Quan sát thêm:**
- Dòng `Received signal 15` của `handle_sigterm` chỉ in ra **sau cùng**: uvicorn tự cài handler SIGTERM khi khởi động, nên handler tự viết không chạy ngay lúc nhận tín hiệu; uvicorn chỉ gọi lại nó sau khi đã tắt xong. Logic dọn dẹp vì vậy nên đặt trong `lifespan` chứ không đặt trong signal handler.
- `/ask` khai báo `async def` nhưng gọi `ask()` dùng `time.sleep()` (blocking), nên trong 3 giây đó cả event loop bị chặn, các request khác (kể cả `/health`) phải chờ. Nên dùng `def` (FastAPI chạy trong threadpool) hoặc `await asyncio.sleep`.

### Exercise 5.3: Stateless design (`05-scaling-reliability/production/app.py`)

- **Anti-pattern:** lưu `conversation_history = {}` trong RAM. Mỗi instance có RAM riêng: request 1 của user vào instance A lưu history ở A, request 2 vào instance B thì B không thấy gì. Restart hoặc redeploy cũng mất hết.
- **Bản production:** mọi state nằm trong Redis. `save_session()` dùng `SETEX session:<id>` với TTL 3600 giây (dòng 59–65), `load_session()` đọc lại (dòng 68–73), `append_to_history()` giữ tối đa 20 message. Instance không giữ gì trong RAM nên **bất kỳ instance nào cũng phục vụ được bất kỳ request nào**, và có thể thêm/bớt/khởi động lại instance thoải mái.
- Nếu không kết nối được Redis, app rơi về `_memory_store` (dòng 42–45) và in cảnh báo "not scalable". Fallback này tiện khi dev, nhưng trên production nên fail (để `/ready` trả 503) thay vì âm thầm chạy với state cục bộ.

### Exercise 5.4: Load balancing (3 instance + Redis + Nginx)

```text
$ docker compose -p k5-day14-scaling up -d --build --scale agent=3
$ docker compose -p k5-day14-scaling ps
NAME                       IMAGE                    STATUS                    PORTS
k5-day14-scaling-agent-1   k5-day14-scaling-agent   Up 40 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   k5-day14-scaling-agent   Up 40 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   k5-day14-scaling-agent   Up 40 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   nginx:alpine             Up 40 seconds             0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   redis:7-alpine           Up 51 seconds (healthy)   6379/tcp
```

10 request `POST /chat` qua nginx (cổng 8080):

```text
HTTP 200 | q=Request 1    served_by=instance-4615e0 storage=redis
HTTP 200 | q=Request 2    served_by=instance-3502bd storage=redis
HTTP 200 | q=Request 3    served_by=instance-b612de storage=redis
HTTP 200 | q=Request 4    served_by=instance-4615e0 storage=redis
HTTP 200 | q=Request 5    served_by=instance-3502bd storage=redis
HTTP 200 | q=Request 6    served_by=instance-b612de storage=redis
HTTP 200 | q=Request 7    served_by=instance-4615e0 storage=redis
HTTP 200 | q=Request 8    served_by=instance-3502bd storage=redis
HTTP 200 | q=Request 9    served_by=instance-b612de storage=redis
HTTP 200 | q=Request 10   served_by=instance-4615e0 storage=redis
```

`docker compose logs agent` đếm số `POST /chat` mỗi container: `agent-1: 3`, `agent-2: 4`, `agent-3: 4` (gồm cả 1 request thử trước đó). Nginx phân phối **round-robin** đều giữa 3 instance: Docker DNS phân giải tên `agent` thành IP của cả 3 container, và `upstream agent_cluster` xoay vòng giữa chúng. Header `X-Served-By` (ví dụ `172.18.0.5:8000`) cho biết request đi tới container nào.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
Session ID: 4bbedaa7-9070-4155-b93a-76c259a8f0d8

Request 1: [instance-3502bd]   Q: What is Docker?
Request 2: [instance-b612de]   Q: Why do we need containers?
Request 3: [instance-4615e0]   Q: What is Kubernetes?
Request 4: [instance-3502bd]   Q: How does load balancing work?
Request 5: [instance-b612de]   Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-4615e0', 'instance-b612de', 'instance-3502bd'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  ...
  [user]: What is Redis used for?...
  [assistant]: Tôi là AI agent được deploy lên cloud. ...
✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu của **cùng một session** lần lượt rơi vào cả 3 instance khác nhau, nhưng history vẫn đủ **10 message** (5 câu hỏi + 5 trả lời) theo đúng thứ tự. Lý do: không instance nào giữ history trong RAM; mỗi request đọc history từ Redis, thêm message mới rồi ghi lại vào Redis. Nếu lưu trong RAM, mỗi instance chỉ thấy phần history của chính nó và history sẽ bị đứt đoạn.

(Script chỉ gửi 5 câu rồi đọc history, không tự tắt instance nào.)

**Thử thêm: tắt hẳn một instance giữa cuộc hội thoại.** Gửi lượt 1, `docker stop k5-day14-scaling-agent-1`, rồi gửi tiếp lượt 2–6 cùng `session_id`:

```text
docker stop k5-day14-scaling-agent-1
HTTP 200 | q=turn 2 after failure   served_by=instance-b612de
HTTP 200 | q=turn 3 after failure   served_by=instance-4615e0
HTTP 200 | q=turn 4 after failure   served_by=instance-b612de
HTTP 200 | q=turn 5 after failure   served_by=instance-4615e0
HTTP 200 | q=turn 6 after failure   served_by=instance-b612de
--- history
count: 12
```

Cả 5 request đều 200, chỉ còn 2 instance phục vụ, và history vẫn đủ 12 message (gồm cả lượt 1 do instance đã chết xử lý). Redis giữ session với TTL còn `3599` giây.

Log nginx ghi lại lúc chuyển hướng:

```text
[error] upstream timed out (110: Operation timed out) while connecting to upstream, ... upstream: "http://172.18.0.3:8000/chat"
```

Nginx vẫn gửi thử vào IP của container đã tắt, kết nối bị timeout, rồi nhờ `proxy_next_upstream error timeout http_503` mới chuyển sang instance khác, nên client vẫn nhận 200 nhưng **request đó bị chậm** vì phải chờ timeout. Cải thiện: đặt `proxy_connect_timeout` ngắn (ví dụ 2s) và `max_fails=1 fail_timeout=10s` cho upstream để nginx sớm bỏ instance chết ra.

**Lỗi nhỏ phát hiện được:** `turn` trả về **2** ngay ở câu đầu tiên của một session mới. `chat()` thêm câu hỏi vào history trước rồi mới đếm, sau đó lại `+ 1` (`app.py` dòng 140–154), nên bị đếm dư 1.

**Ghi chú:** lần chạy đầu ngay sau khi build, `test_stateless.py` gặp `HTTP Error 502: Bad Gateway`; chạy lại khi các container đã ổn định thì không gặp lại.

## Part 6: Final Project — output `check_production_ready.py`

Chạy bản **đầy đủ** (có Docker) trong `06-lab-complete/`, trên Ubuntu/WSL2 (`os.name = posix`), exit code `0`:

```text
================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Một số dòng chi tiết từ cùng lần chạy:

```text
✅ Docker: build image thành công — 22s
✅ Docker: image < 500 MB — 273 MB
✅ Khi chạy: vượt giới hạn → 429 — 429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)
✅ Khi chạy: state nằm trong Redis — restart agent vẫn bị 429
✅ Khi chạy: Redis mất kết nối → /ready 503 (còn /health vẫn 200)
✅ Khi chạy: hết budget → 402 — 402 ở request thứ 1
✅ Khi chạy: mọi dòng log của app là JSON hợp lệ — 15 dòng JSON
✅ Khi chạy: SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0
✅ Docker: `docker stop` tắt êm (exit code 0, < 10s) — exit code 0, 0.7s
```

`check_production_ready.py` không bị sửa: `git diff -- 06-lab-complete/check_production_ready.py` trống.

### Thiết kế từng phần

| File | Cách làm |
|---|---|
| `app/config.py` | Dataclass `Settings` đọc mọi giá trị từ env (`PORT`, `ENVIRONMENT`, `AGENT_API_KEY`, `REDIS_URL`, `RATE_LIMIT_PER_MINUTE`, `DAILY_BUDGET_USD`, `MONTHLY_BUDGET_USD`, `LOG_LEVEL`). `AGENT_API_KEY` không có giá trị mặc định. |
| `app/auth.py` | `APIKeyHeader("X-API-Key")`, so sánh bằng `secrets.compare_digest` (chống đoán key qua thời gian phản hồi). Thiếu, sai, hoặc server chưa cấu hình key → **401** (mặc định là đóng, không bao giờ mở cửa khi thiếu cấu hình). |
| `app/redis_client.py` | `redis.from_url()` chỉ tạo pool, **không ping lúc import**, nên container khởi động được khi chưa có Redis. Timeout kết nối 1 giây để `/ready` không bị treo. |
| `app/rate_limiter.py` | Sliding window bằng **Redis sorted set** cho mỗi API key (lưu hash SHA-256 chứ không lưu key thô). Một transaction `ZREMRANGEBYSCORE → ZADD → ZCARD → EXPIRE` nên nhiều worker đếm chung không sai. Vượt → **429** + `Retry-After`; request bị chặn không bị tính vào cửa sổ. |
| `app/cost_guard.py` | Cộng chi phí bằng `INCRBYFLOAT` (nguyên tử) vào `cost:day:<YYYY-MM-DD>` và `cost:month:<YYYY-MM>` (UTC, TTL 2 ngày / 32 ngày). Đạt `DAILY_BUDGET_USD` hoặc `MONTHLY_BUDGET_USD` → **402**. Sang ngày/tháng mới tự dùng key mới nên tự reset. |
| `app/main.py` | `/health` luôn 200 (không phụ thuộc Redis); `/ready` 503 khi Redis chết hoặc đang tắt; `/ask` = auth → rate limit → cost guard → LLM → ghi chi phí. `JsonFormatter` cho mọi log của app. Handler lỗi: Redis lỗi → 503 JSON, lỗi khác → 500 JSON, **không lộ stack trace**. `lifespan` cho graceful shutdown, không tự đăng ký signal handler để khỏi đè handler của uvicorn. `/ask` dùng `def` để FastAPI chạy trong threadpool, LLM chậm không chặn event loop. |
| `Dockerfile` | 2 stage: builder cài deps vào `/opt/venv`; runtime `python:3.11-slim`, user `app` (non-root), `HEALTHCHECK` gọi `/health` theo `PORT`. `CMD ["sh","-c","exec uvicorn ... --port ${PORT:-8000}"]`: `exec` làm uvicorn thành PID 1 để nhận SIGTERM. |
| `docker-compose.yml` | `agent` (build) + `redis:7-alpine` có healthcheck và volume; agent `depends_on: condition: service_healthy`, `REDIS_URL=redis://redis:6379/0`, đọc thêm `.env` với `required: false`. |
| `.dockerignore` | Loại `.env*` (giữ `.env.example`), `__pycache__`, `venv`, `.git`, tài liệu, `benchmark-observability/`, script chấm. |
| `railway.toml` / `render.yaml` | Railway: `builder = "DOCKERFILE"`, `healthcheckPath = "/health"`. Render: web service Docker + Key Value (Redis), `AGENT_API_KEY` dùng `generateValue: true`, `REDIS_URL` lấy từ service Redis. |

### Kiểm tra thêm bằng `docker compose` (ngoài script chấm)

Chạy `docker compose -p k5-day14-final up -d --build` với `.env` tạm (key ngẫu nhiên, `RATE_LIMIT_PER_MINUTE=3`, đã xoá sau khi test):

```text
SERVICE   IMAGE                  STATUS                           PORTS
agent     k5-day14-final-agent   Up 1 second (health: starting)   0.0.0.0:8000->8000/tcp
redis     redis:7-alpine         Up 7 seconds (healthy)           6379/tcp
k5-day14-final-agent:latest  286MB

$ docker compose exec agent sh -c 'whoami; cat /proc/1/cmdline'
user=app
/opt/venv/bin/python /opt/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 8
```

Auth và xử lý lỗi input (mã lỗi rõ ràng, không có stack trace):

```text
no key          -> 401  {"detail":"Invalid or missing API key. Include header: X-API-Key: <key>"}
wrong key       -> 401  {"detail":"Invalid or missing API key. Include header: X-API-Key: <key>"}
correct key     -> 200  {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server ...","usage":{"input_tokens":6,"output_tokens":32,"cost_usd":2.01e-05,...}}
empty question  -> 422  {"detail":[{"type":"string_too_short","loc":["body","question"],"msg":"String should have at least 1 character",...}]}
missing field   -> 422  {"detail":[{"type":"missing","loc":["body","question"],"msg":"Field required",...}]}
invalid JSON    -> 422  {"detail":[{"type":"json_invalid","loc":["body",1],"msg":"JSON decode error",...}]}
2001 ký tự      -> 422  {"detail":[{"type":"string_too_long","loc":["body","question"],"msg":"String should have at most 2000 characters",...}]}
```

Rate limit, state trong Redis, restart agent:

```text
req 1 -> 200
req 2 -> 200
req 3 -> 429  {"detail":{"error":"Rate limit exceeded","limit":3,"window_seconds":60,"retry_after_seconds":60}}
HTTP/1.1 429 Too Many Requests
retry-after: 60
x-ratelimit-limit: 3
x-ratelimit-remaining: 0

$ redis-cli --scan
cost:day:2026-11-25
cost:month:2026-11
ratelimit:bc927e19a53552d4
cost:month:2026-11 = 0.0000615 (ttl 2764800s)
cost:day:2026-11-25 = 0.0000615 (ttl 172800s)

$ docker compose restart agent
after restart -> 429  {"detail":{"error":"Rate limit exceeded","limit":3,"window_seconds":60,"retry_after_seconds":58}}
```

Tắt Redis (`docker compose stop redis`):

```text
/health -> 200  {"status":"ok","uptime_seconds":0.5,"version":"1.0.0","environment":"development"}
/ready  -> 503  {"ready":false,"reason":"redis unavailable"}
/ask    -> 503  {"detail":"Service temporarily unavailable (storage)"}
/ready  -> 200  {"ready":true,"redis":"ok"}          # sau khi bật lại Redis
```

Log JSON của agent (21/21 dòng parse được bằng `json.loads`):

```text
{"ts": "2026-11-25T09:10:47.441+00:00", "lvl": "INFO", "logger": "agent", "event": "shutdown", "in_flight": 0}
{"ts": "2026-11-25T09:10:48.865+00:00", "lvl": "INFO", "logger": "agent", "event": "startup", "environment": "development", "port": 8000, "rate_limit_per_minute": 3, "daily_budget_usd": 1.0, "monthly_budget_usd": 10.0, "api_key_configured": true, "redis_ok": true}
{"ts": "2026-11-25T09:10:58.223+00:00", "lvl": "ERROR", "logger": "agent", "event": "redis_error", "path": "/ask", "error": "ConnectionError"}
{"ts": "2026-11-25T09:10:58.223+00:00", "lvl": "INFO", "logger": "agent", "event": "request", "method": "POST", "path": "/ask", "status": 503, "ms": 3711.8}
```

`docker compose stop agent`: **0.7 giây, exit code 0**, log có `shutdown_complete`.

**Còn có thể cải thiện:** khi Redis chết, request `/ask` mất khoảng 3.7 giây mới trả 503 (chờ phân giải DNS tên `redis` và timeout kết nối). Có thể thêm circuit breaker: sau vài lần lỗi liên tiếp thì trả 503 ngay trong một khoảng thời gian.

## AI log

| # | Hỏi / nhờ AI làm gì | AI trả về / đã làm gì | Mình tự kiểm tra thế nào |
|---|---|---|---|
| 1 | Claude Code (Claude Opus 5.5): đọc README, RULES, CHECKPOINTS, SUBMISSION, RUBRIC và hướng dẫn lộ trình làm bài | Tóm tắt lộ trình CP0–CP8 và các lưu ý K5 | Đối chiếu với README và CHECKPOINTS |
| 2 | Cài môi trường: WSL2, Ubuntu, Docker Desktop (bị lỗi 403 khi tải và thiếu Virtual Machine Platform) | Chỉ ra nguyên nhân (giới hạn API GitHub theo IP, Fast Startup làm lần restart không áp dụng tính năng), tải file cài chính thức và kiểm chữ ký/SHA256, cài Ubuntu và Docker vào ổ E, bật WSL Integration | `wsl --version`, `docker version`, `python -c "import os; print(os.name)"` in `posix` |
| 3 | Repo bị đuôi dòng CRLF làm `.env` lỗi khi chạy trên Linux | Đặt `core.autocrlf=input` cho repo và đổi file đang làm việc sang LF | `git ls-files --eol` toàn bộ `w/lf`; `git status` sạch (nội dung không đổi) |
| 4 | Part 1: chạy bản develop/production, ghi lại output, tìm anti-pattern và làm bảng so sánh | Chạy app trong WSL, chụp output curl và log server, viết nháp câu trả lời Part 1; phát hiện lỗi log JSON bị nuốt và kiểm chứng bằng script | Đọc lại `develop/app.py`, `production/app.py`, `config.py` đối chiếu từng dòng; tự chạy lại `curl` để xem kết quả |
| 5 | Part 2: build image bị Docker Hub trả `429 Too Many Requests` (giới hạn tải ẩn danh theo IP) | Cấu hình Docker dùng `registry-mirrors: ["https://mirror.gcr.io"]` (mirror Docker Hub của Google), không sửa Dockerfile | `docker info` thấy mirror; build lại thành công |
| 6 | Part 2: build 2 image, đo kích thước, chạy container và stack Compose, trả lời câu hỏi | Chạy các lệnh, ghi lại output và số đo, viết nháp câu trả lời, vẽ sơ đồ | Số MB lấy từ `docker images` và `docker image inspect`; tự tính lại % chênh lệch; `docker compose ps` và curl qua nginx |
| 7 | Part 4: chạy test API key, JWT, rate limit, cost guard; viết bản cost guard dùng Redis | Chạy các lệnh curl (che token), demo 402 bằng `CostGuard` với budget nhỏ, viết nháp câu trả lời và đoạn code Redis; chỉ ra race condition trong lời giải mẫu và lỗi `budget_remaining_usd` | Đếm lại 10 request 200 rồi 429 trong output; đọc lại `auth.py`, `rate_limiter.py`, `cost_guard.py` theo số dòng; chạy đoạn code Redis với 50 request đồng thời |
| 8 | Part 5: test health/ready, graceful shutdown, scale 3 instance, `test_stateless.py`, tắt 1 instance giữa session | Chạy các lệnh, ghi lại output, viết nháp câu trả lời; chỉ ra handler SIGTERM tự viết chạy sau cùng, `async def` gọi hàm blocking, nginx chờ timeout khi instance chết, lỗi `turn` đếm dư | Đọc lại `develop/app.py`, `production/app.py`, `nginx.conf` theo số dòng; đối chiếu `served_by` và số message trong history |
| 9 | Part 6: viết code cho các TODO trong `06-lab-complete/` (config, auth, redis_client, rate_limiter, cost_guard, main, Dockerfile, compose, .dockerignore, .env.example, railway.toml, render.yaml) | Đọc phần runtime của `check_production_ready.py` để biết quy ước, viết code, chạy script chấm (chế độ nhanh rồi đầy đủ) và test thêm bằng `docker compose` | `PASS 12/12 · 43/43`; tự test thêm 401/422/429/503, restart agent, tắt Redis, `docker stop`; `git diff` xác nhận không sửa script chấm. Đã đọc lại từng file và có thể giải thích từng phần (bảng thiết kế ở trên) |
| 10 | Part 3: deploy agent Part 6 lên Railway, so sánh `railway.toml`/`render.yaml`, đọc pipeline Cloud Run | Cài Railway CLI; sau khi mình tự `railway login`, AI tạo project, Redis, service `agent`, đặt biến (key sinh ngẫu nhiên, không ghi vào repo), `railway up`, tạo domain, chạy 4 lệnh tự kiểm; viết `DEPLOYMENT.md` và nháp Part 3 | 4 lệnh trả 200/200/401/200; `check_production_ready.py --public-url` báo ✅; mở URL trên trình duyệt; chụp ảnh dashboard |

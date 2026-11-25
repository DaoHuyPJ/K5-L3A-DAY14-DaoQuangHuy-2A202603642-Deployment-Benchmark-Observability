# Deployment Information

## Public URL

https://agent-production-4d28a07b.up.railway.app

## Platform

**Railway**: project `k5-day14-agent`, 2 service:

| Service | Nguồn | Vai trò |
|---|---|---|
| `agent` | `06-lab-complete/` (build bằng `Dockerfile` theo `railway.toml`) | FastAPI agent Part 6 |
| `Redis` | Railway Redis database | Rate limit + cost guard |

Deploy bằng Railway CLI, chạy trong `06-lab-complete/`:

```bash
railway init --name k5-day14-agent
railway add --database redis
railway add --service agent --variables "AGENT_API_KEY=<secret>" --variables 'REDIS_URL=${{Redis.REDIS_URL}}' ...
railway up --service agent --ci
railway domain --service agent
```

## Test Commands

Đặt `BASE` là URL public và `AGENT_API_KEY` là key đã đặt trên Railway. Mỗi lệnh chỉ in mã HTTP, không lộ key.

```bash
BASE=https://agent-production-4d28a07b.up.railway.app

curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả khi chạy (2026-11-25):

```text
$ curl -sS -o /dev/null -w "%{http_code}\n" "$BASE/health"
200
$ curl -sS -o /dev/null -w "%{http_code}\n" "$BASE/ready"
200
$ curl ... -X POST "$BASE/ask"                      (không có key)
401
$ curl ... -X POST "$BASE/ask" -H "X-API-Key: ..."  (key đúng)
200
```

### Health Check

```bash
curl https://agent-production-4d28a07b.up.railway.app/health
# {"status":"ok","uptime_seconds":36.1,"version":"1.0.0","environment":"production"}

curl https://agent-production-4d28a07b.up.railway.app/ready
# {"ready":true,"redis":"ok"}
```

### API Test (with authentication)

```bash
curl -X POST https://agent-production-4d28a07b.up.railway.app/ask \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":6,"output_tokens":32,"cost_usd":2.01e-05,"requests_remaining":8}}
```

### Rate limit trên cloud (`RATE_LIMIT_PER_MINUTE=10`)

```text
200 200 200 200 200 200 200 200 429 429      # 2 request đã dùng ở trên + 8 = 10, rồi 429
HTTP/1.1 429 Too Many Requests
retry-after: 53
```

### Script chấm với URL public

```text
$ python check_production_ready.py --static-only --public-url https://agent-production-4d28a07b.up.railway.app
✅ 12. Public URL ready (Railway / Render config)
     ✅ railway.toml: builder + healthcheckPath = "/health"
     ✅ App lắng nghe theo biến PORT do platform cấp
     ✅ Public URL: GET https://agent-production-4d28a07b.up.railway.app/health → 200
```

## Environment Variables Set

Đặt trên service `agent` của Railway (bằng `railway add --variables`), **không ghi trong code hay trong repo**:

| Biến | Giá trị |
|---|---|
| `AGENT_API_KEY` | secret ngẫu nhiên 43 ký tự (sinh bằng `RandomNumberGenerator`, không commit) |
| `REDIS_URL` | `${{Redis.REDIS_URL}}`: tham chiếu tới service Redis, Railway tự điền |
| `ENVIRONMENT` | `production` (tắt `/docs`) |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | `10.0` |
| `PORT` | Railway tự cấp (log khởi động: `port=8080`); `CMD` dùng `${PORT:-8000}` |

Log khởi động trên Railway:

```text
INFO:     Started server process [1]
[INFO] ts="2026-11-25T09:21:26.795+00:00" logger="agent" event="startup" environment="production" port=8080 rate_limit_per_minute=10 daily_budget_usd=1 monthly_budget_usd=10 api_key_configured=true redis_ok=true
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
```

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)

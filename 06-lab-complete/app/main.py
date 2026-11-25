"""
Production AI Agent — Part 6 Final Project

  GET  /health  liveness  — luôn 200 khi process còn chạy (không phụ thuộc Redis)
  GET  /ready   readiness — 200 khi Redis kết nối được, 503 khi không (hoặc đang tắt)
  POST /ask     X-API-Key → rate limit (429) → cost guard (402) → LLM → ghi chi phí

Chạy: uvicorn app.main:app --workers 2
Chấm: python check_production_ready.py
"""
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import redis_client
from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging (tiêu chí 10): mỗi dòng log của app = 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("agent")
    log.handlers[:] = [handler]
    log.setLevel(settings.log_level)
    log.propagate = False  # không để root/uvicorn in lại dòng này ở format khác
    return log


logger = _setup_logging()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})


# ─────────────────────────────────────────────────────────
# Lifecycle + graceful shutdown (tiêu chí 11)
#   uvicorn tự bắt SIGTERM: ngừng nhận kết nối mới, chờ request đang chạy xong,
#   rồi mới chạy phần sau `yield`. Không tự đăng ký signal handler để khỏi
#   đè mất handler của uvicorn.
# ─────────────────────────────────────────────────────────
_state = {"ready": False, "in_flight": 0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_event("startup", environment=settings.environment, port=settings.port,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              daily_budget_usd=settings.daily_budget_usd,
              monthly_budget_usd=settings.monthly_budget_usd,
              api_key_configured=bool(settings.agent_api_key),
              redis_ok=redis_client.redis_healthy())
    if not settings.agent_api_key:
        log_event("config_warning", logging.WARNING,
                  detail="AGENT_API_KEY is empty — every /ask request will be rejected with 401")
    _state["ready"] = True
    yield
    _state["ready"] = False
    log_event("shutdown", in_flight=_state["in_flight"])
    redis_client.close()
    log_event("shutdown_complete")


app = FastAPI(
    title="Production AI Agent",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None,
)


@app.middleware("http")
async def access_log(request: Request, call_next):
    """Log mỗi request thành 1 dòng JSON + gắn security headers."""
    start = time.perf_counter()
    _state["in_flight"] += 1
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response
    finally:
        _state["in_flight"] -= 1
        if request.url.path not in ("/health", "/ready"):
            log_event("request", method=request.method, path=request.url.path, status=status,
                      ms=round((time.perf_counter() - start) * 1000, 1))


# ─────────────────────────────────────────────────────────
# Error handling: mã lỗi rõ ràng, không lộ stack trace ra client
# ─────────────────────────────────────────────────────────
@app.exception_handler(redis.RedisError)
async def redis_error_handler(request: Request, exc: redis.RedisError):
    log_event("redis_error", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable (storage)"},
                        headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.error("unhandled_error", exc_info=exc, extra={"fields": {"path": request.url.path}})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


START_TIME = time.time()


@app.get("/")
def root():
    return {"app": "Production AI Agent", "version": app.version, "environment": settings.environment}


@app.get("/health")
def health():
    """Liveness (tiêu chí 4): process còn chạy là 200 — không kiểm tra Redis,
    để platform không restart container chỉ vì Redis tạm thời chết."""
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "version": app.version,
        "environment": settings.environment,
    }


@app.get("/ready")
def ready():
    """Readiness (tiêu chí 5): 503 khi đang tắt hoặc Redis không kết nối được,
    để load balancer ngừng gửi traffic vào instance này."""
    if not _state["ready"]:
        return JSONResponse(status_code=503, content={"ready": False, "reason": "shutting down"})
    if not redis_client.redis_healthy():
        return JSONResponse(status_code=503, content={"ready": False, "reason": "redis unavailable"})
    return {"ready": True, "redis": "ok"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    # `def` (không async): FastAPI chạy trong threadpool → LLM chậm không chặn event loop
    remaining = check_rate_limit(api_key)          # 429 khi vượt RATE_LIMIT_PER_MINUTE
    check_budget()                                 # 402 khi hết ngân sách

    started = time.perf_counter()
    answer = llm_ask(body.question)
    input_tokens, output_tokens = estimate_tokens(body.question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)

    log_event("llm_call", user_id=body.user_id, input_tokens=input_tokens,
              output_tokens=output_tokens, cost_usd=round(cost, 8),
              llm_ms=round((time.perf_counter() - started) * 1000, 1))
    return {
        "question": body.question,
        "answer": answer,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 8),
            "requests_remaining": remaining,
        },
    }

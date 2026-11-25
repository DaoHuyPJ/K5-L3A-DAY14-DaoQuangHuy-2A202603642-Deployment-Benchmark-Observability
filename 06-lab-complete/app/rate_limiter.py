"""
Rate limiting (tiêu chí 7).

  • Mỗi API key tối đa RATE_LIMIT_PER_MINUTE request trong 60 giây (sliding window)
  • Vượt giới hạn → HTTP 429 kèm header Retry-After
  • State nằm trong Redis (sorted set) → mọi worker / instance dùng chung,
    restart agent vẫn còn
"""
import hashlib
import math
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def _redis_key(api_key: str) -> str:
    # Không lưu API key thô trong Redis — chỉ lưu hash
    digest = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    return f"ratelimit:{digest}"


def check_rate_limit(key: str) -> int:
    """Ghi nhận 1 request của `key`. Vượt giới hạn → 429. Trả về số request còn lại."""
    limit = settings.rate_limit_per_minute
    now = time.time()
    rkey = _redis_key(key)
    member = f"{now:.6f}-{uuid.uuid4().hex[:8]}"

    # Một transaction (MULTI/EXEC): bỏ request cũ ngoài cửa sổ, thêm request này,
    # đếm, gia hạn TTL — các worker chạy song song không đếm sai.
    pipe = r.pipeline(transaction=True)
    pipe.zremrangebyscore(rkey, 0, now - WINDOW_SECONDS)
    pipe.zadd(rkey, {member: now})
    pipe.zcard(rkey)
    pipe.expire(rkey, WINDOW_SECONDS + 1)
    _, _, count, _ = pipe.execute()

    if count > limit:
        # Request bị chặn không được tính vào cửa sổ
        r.zrem(rkey, member)
        oldest = r.zrange(rkey, 0, 0, withscores=True)
        oldest_ts = oldest[0][1] if oldest else now
        retry_after = max(1, math.ceil(oldest_ts + WINDOW_SECONDS - now))
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit": limit,
                "window_seconds": WINDOW_SECONDS,
                "retry_after_seconds": retry_after,
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )
    return limit - count

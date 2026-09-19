import time
from collections import defaultdict
from fastapi import HTTPException, Request

class SimpleRateLimiter:
    def __init__(self):
        self.requests = defaultdict(list)
        
    def check_rate_limit(self, request: Request, max_requests: int = 10, window_seconds: int = 60, route_name: str = "default"):
        client_ip = request.client.host if request.client else "127.0.0.1"
        key = f"{route_name}:{client_ip}"
        now = time.time()
        
        self.requests[key] = [ts for ts in self.requests[key] if now - ts < window_seconds]
        
        if len(self.requests[key]) >= max_requests:
            raise HTTPException(
                status_code=429,
                detail=f"Too Many Requests: Rate limit of {max_requests} per {window_seconds}s exceeded for route '{route_name}'. Please wait before retrying."
            )
            
        self.requests[key].append(now)

rate_limiter = SimpleRateLimiter()

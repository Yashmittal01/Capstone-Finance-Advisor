import redis
import json
import os
from typing import Optional, Dict, Any

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

try:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    # Test connection
    redis_client.ping()
    print("✓ Redis connected successfully")
except Exception as e:
    print(f"⚠ Redis connection failed: {e}")
    redis_client = None


def save_session_memory(session_id: str, key: str, value: Dict | str):
    """Save session memory to Redis"""
    if redis_client is None:
        raise RuntimeError("Redis client not initialized")
    redis_client.hset(session_id, key, json.dumps(value))


def get_session_memory(session_id: str, key: str) -> Optional[Dict | str]:
    """Retrieve session memory from Redis"""
    if redis_client is None:
        raise RuntimeError("Redis client not initialized")
    data = redis_client.hget(session_id, key)
    return json.loads(data) if data else None


def delete_session(session_id: str):
    """Delete entire session from Redis"""
    if redis_client is None:
        raise RuntimeError("Redis client not initialized")
    redis_client.delete(session_id)


def delete_key(session_id: str, key: str):
    """Delete specific key from session"""
    if redis_client is None:
        raise RuntimeError("Redis client not initialized")
    redis_client.hdel(session_id, key)
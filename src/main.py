"""
Twillow SMS Service - Main FastAPI application.

This service provides high-throughput SMS sending capabilities by:
- Queueing requests using Redis and Taskiq
- Rate limiting SMS providers to 50 RPS each
- Supporting 200 RPS total throughput across 3 providers
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from taskiq_redis import redis_broker

from .config import settings
from .providers import router as providers_router
from .queue import router as queue_router
from .middleware import create_rate_limiting_middleware
from .tasks import broker
from .database import initialize_database, async_initialize_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager."""
    # Startup
    app.state.redis = Redis.from_url(settings.redis_url)
    await broker.startup()

    # Initialize database
    await async_initialize_database()

    yield
    # Shutdown
    await broker.shutdown()
    await app.state.redis.close()


app = FastAPI(
    title="Twillow SMS Service",
    description="High-throughput SMS service with Redis queueing and rate limiting",
    version="0.1.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Add rate limiting middleware
app.add_middleware(
    create_rate_limiting_middleware(
        exclude_paths=["/health", "/docs", "/openapi.json"],
        include_provider_check=True
    )
)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "Twillow SMS Service",
        "version": "0.1.0",
        "docs_url": "/docs"
    }


# Include routers
app.include_router(queue_router, prefix="/api/sms", tags=["SMS Queue"])
app.include_router(providers_router, prefix="/api/sms", tags=["SMS Providers"])


@app.get("/taskiq-status")
async def taskiq_status():
    """Get TaskIQ broker status."""
    return {
        "broker_type": "RedisBroker",
        "queue_name": "sms_queue",
        "redis_url": settings.taskiq_broker_url,
        "max_connection_pool_size": 20,
        "status": "active"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
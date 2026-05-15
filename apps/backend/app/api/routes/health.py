"""Health-check route. No auth required."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["health"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}

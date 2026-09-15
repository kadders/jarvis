from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from api.routers import effects, recognize
from models.handler import WandHandler
from utils.config import get_settings

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.wand = WandHandler(settings)
    logger.info("Wand service started on port {}", settings.api_port)
    yield


app = FastAPI(
    title="Jarvis Wand Service",
    description="Interactive wand gesture + incantation fusion for the Jarvis stack",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(recognize.router, prefix="/api/v1")
app.include_router(effects.router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"status": "ok", "message": "Jarvis Wand Service is running"}


@app.get("/health")
async def health():
    settings = get_settings()
    wand = getattr(app.state, "wand", None)
    return {
        "status": "ok",
        "service": "wand",
        "version": "0.1.0",
        "environment": settings.environment,
        "gesture_backend": wand.gesture.backend if wand else None,
    }


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_debug,
    )

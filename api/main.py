"""FastAPI app for hable-ya.

Loads the shared Pipecat services (STT / LLM / TTS) once during lifespan and
pings the llama.cpp backend until it is ready before flipping
`app.state.ready = True`. The `cuda_bootstrap` call runs before any pipecat
imports so CUDA-linked libs resolve correctly (see hable_ya/cuda_bootstrap.py).
"""
from __future__ import annotations

# Must run before any pipecat/torch import — see hable_ya/cuda_bootstrap.py.
from hable_ya.cuda_bootstrap import bootstrap_cuda

bootstrap_cuda()

import logging  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI  # noqa: E402

from api.routes.health import router as health_router  # noqa: E402
from api.routes.session import router as session_router  # noqa: E402
from hable_ya.config import settings  # noqa: E402
from hable_ya.pipeline.services import load_services, warmup_llm  # noqa: E402

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger("hable_ya.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.ready = False
    app.state.settings = settings
    app.state.services = load_services(settings)
    await warmup_llm(settings)
    app.state.ready = True
    logger.info("hable-ya ready on %s:%d", settings.host, settings.port)
    yield


app = FastAPI(title="hable-ya", lifespan=lifespan)
app.include_router(health_router)
app.include_router(session_router)

from fastapi import FastAPI

from api.routes.health import router as health_router
from api.routes.session import router as session_router

app = FastAPI(title="hable-ya")

app.include_router(health_router)
app.include_router(session_router)

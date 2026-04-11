from fastapi import APIRouter, WebSocket

router = APIRouter()


@router.websocket("/ws/session")
async def session_ws(websocket: WebSocket):
    """WebSocket endpoint for a voice session."""
    raise NotImplementedError

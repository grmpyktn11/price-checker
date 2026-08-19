from fastapi import APIRouter, HTTPException

from backend.services import trace

router = APIRouter(prefix="/api", tags=["debug"])


# the same object the chat results response carries under "debug", so a panel can be
# refreshed without re-running a 60 second search. the last few runs are held in memory only
# (trace.MAX_TRACES) and are gone on restart
@router.get("/debug/last")
def get_last_trace() -> dict:
    last = trace.last()
    if last is None:
        raise HTTPException(404, "no search has run since the backend started")
    return last


# what this conversation's in-flight search is doing, for the waiting screen. read off the
# same trace the run is already filling in, so it cannot disagree with the debug panel.
# {"running": false} once the run ends, which is how the client knows to stop polling
@router.get("/chat/progress/{conversation_id}")
def get_progress(conversation_id: str) -> dict:
    live = trace.live(conversation_id)
    return {"running": True, **live} if live else {"running": False}

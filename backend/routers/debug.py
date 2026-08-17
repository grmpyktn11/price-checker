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

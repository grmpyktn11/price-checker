import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend import scheduler
from backend.db import get_db
from backend.models import Alert
from backend.services import email, trace

router = APIRouter(prefix="/api", tags=["debug"])

logger = logging.getLogger(__name__)


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

# the three background jobs, by the name the scheduler registers them under
JOBS = {
    "scrape": scheduler.run_scrape_job,
    "review_check": scheduler.run_review_check_job,
    "digest": scheduler.run_digest_job,
}
# these re-run the whole pipeline for every watched item, so they take minutes. awaiting one
# inside the request would hang the browser with no way to tell whether it was working
SLOW_JOBS = ("scrape", "review_check")


class JobOut(BaseModel):
    job: str
    ran: bool
    detail: str


# what the scheduler would do on its own, on demand. the jobs are hours or a day apart, so
# without this the only way to test an email is to wait until 08:00
@router.post("/debug/jobs/{job}", response_model=JobOut)
async def run_job(job: str) -> JobOut:
    runner = JOBS.get(job)
    if runner is None:
        raise HTTPException(404, f"unknown job: pick one of {', '.join(JOBS)}")
    if job in SLOW_JOBS:
        asyncio.create_task(run_in_background(job, runner))
        return JobOut(job=job, ran=True,
                      detail="started in the background - it re-searches every watched item, "
                             "so give it a few minutes, then check Alerts")
    try:
        count = await runner() or 0
    # the job's own error handling already tolerates a bad item; anything reaching here is a
    # bug worth seeing in the panel rather than only in the log
    except Exception as error:
        logger.exception("manual %s job failed", job)
        return JobOut(job=job, ran=False, detail=f"{type(error).__name__}: {error}")
    return JobOut(job=job, ran=True,
                  detail=f"{count} alert{'' if count == 1 else 's'} emailed" if count
                         else "nothing was pending, so no email was sent")


# the task outlives the response, so its failure has nowhere to be returned to. log it
async def run_in_background(job: str, runner) -> None:
    try:
        await runner()
        logger.info("manual %s job finished", job)
    except Exception:
        logger.exception("manual %s job failed", job)


class MailOut(BaseModel):
    sent: bool
    detail: str


# proves the whole mail path end to end without needing an alert to exist first
@router.post("/debug/test-email", response_model=MailOut)
async def send_test_email() -> MailOut:
    if not email.RESEND_API_KEY:
        return MailOut(sent=False, detail="RESEND_API_KEY is not set")
    if not email.USER_EMAIL:
        return MailOut(sent=False, detail="USER_EMAIL is not set")
    sent = await email.send_email(
        "Shopper: test email",
        "<h2>Shopper</h2><p>If you are reading this, alert delivery works.</p>",
    )
    return MailOut(sent=sent,
                   detail=f"sent to {email.USER_EMAIL}" if sent
                          else "Resend rejected it - check the server log")


class StatusOut(BaseModel):
    jobs: list[dict]              # id, next run time
    pending_alerts: int           # queued for the next digest
    email_configured: bool
    user_email: str | None
    watched_items: int


@router.get("/debug/status", response_model=StatusOut)
def debug_status(db: Session = Depends(get_db)) -> StatusOut:
    jobs = [{"id": job.id, "next_run": str(job.next_run_time) if job.next_run_time else None}
            for job in scheduler.scheduler.get_jobs()]
    return StatusOut(
        jobs=jobs,
        pending_alerts=db.query(Alert).filter(Alert.sent_at.is_(None)).count(),
        email_configured=bool(email.RESEND_API_KEY and email.USER_EMAIL),
        # shown so a test email that "sent" but never arrived has an obvious first suspect
        user_email=email.USER_EMAIL or None,
        watched_items=len(scheduler.watched_items(db)),
    )

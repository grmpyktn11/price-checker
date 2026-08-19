from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Profile
from backend.services import email as email_service

router = APIRouter(prefix="/api", tags=["profile"])

PROFILE_ID = 1


class LocationUpdate(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    display_address: str


class EmailUpdate(BaseModel):
    # "" clears it, falling back to the USER_EMAIL env var
    email: str = Field(max_length=254)


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    lat: float | None
    lon: float | None
    display_address: str | None
    email: str | None


# single-user app: one profile row, id 1, created blank on first access
def get_or_create_profile(db: Session) -> Profile:
    profile = db.get(Profile, PROFILE_ID)
    if profile is None:
        profile = Profile(id=PROFILE_ID)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


@router.get("/profile", response_model=ProfileOut)
def read_profile(db: Session = Depends(get_db)):
    return get_or_create_profile(db)


# where alerts go. the profile wins over the USER_EMAIL env var, which is only a fallback
@router.patch("/profile/email", response_model=ProfileOut)
def update_email(update: EmailUpdate, db: Session = Depends(get_db)):
    address = update.email.strip()
    if address and "@" not in address:
        raise HTTPException(422, "that does not look like an email address")
    profile = get_or_create_profile(db)
    profile.email = address or None
    db.commit()
    db.refresh(profile)
    return profile


# the address alerts actually go to, profile first
def alert_recipient(db: Session) -> str:
    return get_or_create_profile(db).email or email_service.USER_EMAIL


@router.patch("/profile/location", response_model=ProfileOut)
def update_location(update: LocationUpdate, db: Session = Depends(get_db)):
    profile = get_or_create_profile(db)
    profile.lat = update.lat
    profile.lon = update.lon
    profile.display_address = update.display_address
    db.commit()
    db.refresh(profile)
    return profile

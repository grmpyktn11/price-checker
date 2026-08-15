from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Profile

router = APIRouter(prefix="/api", tags=["profile"])

PROFILE_ID = 1


class LocationUpdate(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    display_address: str


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    lat: float | None
    lon: float | None
    display_address: str | None


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


@router.patch("/profile/location", response_model=ProfileOut)
def update_location(update: LocationUpdate, db: Session = Depends(get_db)):
    profile = get_or_create_profile(db)
    profile.lat = update.lat
    profile.lon = update.lon
    profile.display_address = update.display_address
    db.commit()
    db.refresh(profile)
    return profile

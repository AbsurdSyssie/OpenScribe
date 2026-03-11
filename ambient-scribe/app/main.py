import os
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import Base, engine, get_db
from .models import Team, Transcript, TranscriptStatus, TranscriptVersion, User, transcript_expiry
from .schemas import (
    TeamCreate,
    TeamOut,
    TranscriptCommit,
    TranscriptCreate,
    TranscriptOut,
    UserCreate,
    UserOut,
)

@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Ambient Scribe MVP", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/teams", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
def create_team(payload: TeamCreate, db: Session = Depends(get_db)):
    team = Team(name=payload.name, default_retention_days=payload.default_retention_days)
    db.add(team)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Team already exists") from exc
    db.refresh(team)
    return team


@app.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    team = db.get(Team, payload.team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    user = User(
        email=payload.email,
        password_hash=payload.password_hash,
        team_id=payload.team_id,
        team_role=payload.team_role,
        is_system_admin=False,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="User already exists") from exc
    db.refresh(user)
    return user


@app.post("/transcripts", response_model=TranscriptOut, status_code=status.HTTP_201_CREATED)
def create_transcript(payload: TranscriptCreate, db: Session = Depends(get_db)):
    owner = db.get(User, payload.owner_user_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Owner user not found")
    if owner.team_id != payload.team_id:
        raise HTTPException(status_code=400, detail="Owner user does not belong to the provided team")

    retention_days = payload.retention_days_applied or owner.team.default_retention_days
    transcript = Transcript(
        owner_user_id=payload.owner_user_id,
        team_id=payload.team_id,
        title=payload.title,
        current_draft_text_encrypted=payload.current_draft_text_encrypted,
        status=TranscriptStatus.recording,
        retention_days_applied=retention_days,
        retention_expires_at=transcript_expiry(retention_days),
    )
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    return transcript


@app.post("/transcripts/{transcript_id}/commit", response_model=TranscriptOut)
def commit_transcript(transcript_id: UUID, payload: TranscriptCommit, db: Session = Depends(get_db)):
    transcript = db.get(Transcript, transcript_id)
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    current_max = db.scalar(
        select(func.max(TranscriptVersion.version_no)).where(TranscriptVersion.transcript_id == transcript.id)
    )
    next_version = (current_max or 0) + 1

    version = TranscriptVersion(
        transcript_id=transcript.id,
        version_no=next_version,
        text_encrypted=payload.text_encrypted,
    )
    transcript.current_draft_text_encrypted = payload.text_encrypted
    transcript.status = TranscriptStatus.ready

    db.add(version)
    db.add(transcript)
    db.commit()
    db.refresh(transcript)
    return transcript


@app.get("/users/{user_id}/transcripts", response_model=list[TranscriptOut])
def list_user_transcripts(user_id: UUID, db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Transcript).where(Transcript.owner_user_id == user_id).order_by(Transcript.created_at.desc())
    )
    return list(rows)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8000")),
        reload=False,
    )

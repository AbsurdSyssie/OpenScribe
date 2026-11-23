"""Best-effort, content-free Redis revisions for owner workspace SSE.

The revisions are advisory. The workspace reader remains the authority: each
full payload is built from a fresh database session with its own authorization.
A periodic full refresh is mandatory because bulk SQL and other writers may
bypass ORM events, and Redis updates can be lost after a successful DB commit.
"""

from __future__ import annotations

import os
from functools import lru_cache

from redis import Redis, RedisError
from sqlalchemy import event
from sqlalchemy.orm import Session


# Avoid updating owner revisions for high-volume audio-delivery and quota
# bookkeeping, which are not themselves visible workspace changes.
_OWNER_MODELS = frozenset({
    "Transcript", "TranscriptVersion", "TranscriptIngestionJob",
    "TranscriptStreamSession", "TranscriptStreamWindow", "TranscriptCaptureSettings",
    "PostConsultationDictation", "PostConsultationDictationSegment",
    "GeneratedDocument", "RedactionRun", "ClinicalEntityRun",
    "TranscriptManualPiiEntity", "DetectedPiiDismissal",
    "ConsultationSplitAnalysis", "ConsultationSplitIntent", "ConsultationSplitDraft",
    "ConsultationSplitDraftTopic", "ConsultationSplitBatch", "ConsultationSplitBatchTopic",
    "ConsultationSplitTopicOutcome", "ConsultationSplitExecution",
    "TemplateSuggestionJob", "SmartPhrase", "PromptTemplate", "QuickAction",
})
_USER_MODELS = frozenset({"User", "UserSession", "UserAppPreference", "UserLlmPreference"})
_TEAM_MODELS = frozenset({
    "Team", "TeamSttConfig", "TeamSttSelection", "TeamLlmConfig",
    "TeamLlmSelection", "TeamLlmBackupSelection",
    "TeamHallucinationCheckSelection", "TeamClinicalNlpSelection",
    "TeamDeidentificationProviderAssignment", "TeamDeidentificationSelection",
    "PromptTemplate", "QuickAction",
})
_GLOBAL_MODELS = frozenset({
    "DefaultPromptTemplate", "DefaultPromptTemplateVersion",
    "DefaultQuickAction", "DefaultQuickActionVersion",
})
_PENDING_KEY = "_workspace_revision_pending"
_REDIS_KEY_PREFIX = "openscribe:workspace-revision:v1"


def revisions_enabled() -> bool:
    explicit = os.getenv("WORKSPACE_REVISION_ENABLED")
    if explicit is not None:
        return explicit.lower() in {"1", "true", "yes"}
    return os.getenv("APP_ENV", "development").lower() not in {"test", "testing"}


@lru_cache(maxsize=4)
def _client(url: str) -> Redis:
    return Redis.from_url(url, socket_timeout=0.2, socket_connect_timeout=0.2)


def _redis_client() -> Redis:
    url = os.getenv("WORKSPACE_REVISION_REDIS_URL") or os.getenv(
        "RATE_LIMIT_STORAGE_URL", "redis://localhost:6379/0"
    )
    return _client(url)


def _key(scope: str, scope_id: str = "") -> str:
    return f"{_REDIS_KEY_PREFIX}:{scope}:{scope_id}"


def read_workspace_revisions(owner_user_id: str, team_id: str | None) -> tuple[int, ...] | None:
    """Return an advisory revision; None means fall back to regular polling."""
    if not revisions_enabled():
        return None
    keys = [_key("owner", owner_user_id), _key("global")]
    if team_id:
        keys.append(_key("team", team_id))
    try:
        values = _redis_client().mget(keys)
        return tuple(int(value or 0) for value in values)
    except (RedisError, OSError, ValueError):
        return None


def _capture_workspace_changes(session: Session, *_args) -> None:
    if not revisions_enabled():
        return
    owners: set[str] = set()
    teams: set[str] = set()
    global_change = False
    for obj in session.new | session.dirty | session.deleted:
        model = type(obj).__name__
        if model in _OWNER_MODELS:
            owner = getattr(obj, "owner_user_id", None)
            if owner is not None:
                owners.add(str(owner))
        if model in _USER_MODELS:
            user_id = getattr(obj, "id" if model == "User" else "user_id", None)
            if user_id is not None:
                owners.add(str(user_id))
        if model in _TEAM_MODELS:
            team_id = getattr(obj, "id" if model == "Team" else "team_id", None)
            if team_id is not None:
                teams.add(str(team_id))
        if model in _GLOBAL_MODELS:
            global_change = True
    if not owners and not teams and not global_change:
        return
    pending = session.info.setdefault(_PENDING_KEY, (set(), set(), False))
    session.info[_PENDING_KEY] = (
        pending[0] | owners, pending[1] | teams, pending[2] or global_change,
    )


def _publish_workspace_changes(session: Session) -> None:
    owners, teams, global_change = session.info.pop(_PENDING_KEY, (set(), set(), False))
    if not (owners or teams or global_change) or not revisions_enabled():
        return
    # Commit has already succeeded. Redis failure cannot roll back a clinical
    # write; the SSE full-refresh fallback still ensures eventual convergence.
    try:
        pipe = _redis_client().pipeline(transaction=False)
        for owner in owners:
            key = _key("owner", owner)
            pipe.incr(key)
            pipe.expire(key, 86400)
        for team in teams:
            key = _key("team", team)
            pipe.incr(key)
            pipe.expire(key, 86400)
        if global_change:
            key = _key("global")
            pipe.incr(key)
            pipe.expire(key, 86400)
        pipe.execute()
    except (RedisError, OSError, ValueError):
        return


def _discard_workspace_changes(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


def install_revision_events() -> None:
    """Install once per process; bound to SQLAlchemy sessions, not HTTP routes."""
    if event.contains(Session, "before_flush", _capture_workspace_changes):
        return
    event.listen(Session, "before_flush", _capture_workspace_changes)
    event.listen(Session, "after_commit", _publish_workspace_changes)
    event.listen(Session, "after_rollback", _discard_workspace_changes)

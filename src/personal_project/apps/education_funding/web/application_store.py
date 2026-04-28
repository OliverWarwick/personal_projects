"""Persistent storage for client applications.

Backs the application portal with a per-user JSON file under the
configurable data directory. Each draft and its uploaded artefacts (CV,
video) survive process restarts so the candidate can leave and return to
the same application.

Storage layout::

    $EDUCATION_FINANCING_DATA_DIR/
        applications/{username}__{program_slug}.json
        uploads/{username}/{program_slug}/cv__<file>
                                          video__<file>

Files are written atomically (temp file + rename) so a partial write
can't corrupt an existing draft.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

from personal_project.apps.education_funding.web.applications import (
    Application,
    ApplicationStatus,
)

if TYPE_CHECKING:
    from fastapi import UploadFile

logger = logging.getLogger(__name__)

_DEFAULT_HOME_SUBDIR = ".education_financing"

# Allowed extensions and per-file size caps. Keep these conservative for
# the MVP — large videos are still painful to handle on a Python web
# server, and the candidate has a URL fallback for anything bigger.
_ALLOWED_CV_SUFFIXES = frozenset({".pdf", ".doc", ".docx"})
_ALLOWED_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".webm", ".m4v"})
_ALLOWED_PROOF_SUFFIXES = frozenset({".pdf", ".png", ".jpg", ".jpeg"})
_CV_MAX_BYTES = 10 * 1024 * 1024
_VIDEO_MAX_BYTES = 50 * 1024 * 1024
_PROOF_MAX_BYTES = 10 * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 64 * 1024

# Sanitisation: collapse anything that isn't alphanumeric / dot / dash /
# underscore so a user can't escape the upload directory via a crafted
# filename. The 64-character cap keeps paths readable.
_FILENAME_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")
_MAX_FILENAME_LEN = 64


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _data_dir() -> Path:
    """Return the configured data directory for application persistence.

    Reads ``EDUCATION_FINANCING_DATA_DIR`` if set (used by tests), else
    falls back to ``~/.education_financing``.
    """
    env = os.environ.get("EDUCATION_FINANCING_DATA_DIR")
    if env:
        return Path(env)
    return Path.home() / _DEFAULT_HOME_SUBDIR


def _draft_path(username: str, program_slug: str) -> Path:
    """Return the JSON path for a single (user, programme) draft."""
    return _data_dir() / "applications" / f"{username}__{program_slug}.json"


def _upload_dir(username: str, program_slug: str) -> Path:
    """Return the directory for a user's uploaded files."""
    return _data_dir() / "uploads" / username / program_slug


# ---------------------------------------------------------------------------
# Application I/O
# ---------------------------------------------------------------------------


def get_application(username: str, program_slug: str) -> Application | None:
    """Return the application for ``(username, program_slug)``, or ``None``.

    Args:
        username: Signed-in user's username.
        program_slug: Slug of the financing programme.

    Returns:
        The persisted ``Application`` instance, or ``None`` if no draft
        exists yet (or the file on disk is corrupt).

    """
    path = _draft_path(username, program_slug)
    if not path.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to load application draft from %s", path)
        return None
    return _application_from_dict(data)


def list_applications_for(username: str) -> list[Application]:
    """Return every persisted application for a user, across programmes.

    Used by the dashboard to surface the most recent in-flight
    application without needing to know the programme slug.

    Args:
        username: Signed-in user's username.

    Returns:
        A list of ``Application`` instances, ordered most-recently-
        updated first.

    """
    apps_dir = _data_dir() / "applications"
    if not apps_dir.exists():
        return []
    out: list[Application] = []
    prefix = f"{username}__"
    for path in apps_dir.iterdir():
        if not path.is_file() or not path.name.startswith(prefix):
            continue
        if path.suffix != ".json":
            continue
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Failed to load application draft from %s", path)
            continue
        out.append(_application_from_dict(data))
    return sorted(out, key=lambda a: a.last_updated or "", reverse=True)


def get_application_for(username: str) -> Application | None:
    """Return the most recently updated application for the user.

    Convenience wrapper used by the dashboard banner where the caller
    doesn't know which programme the user might have applied to.

    Args:
        username: Signed-in user's username.

    Returns:
        The most recently updated ``Application``, or ``None`` if the
        user has no drafts.

    """
    apps = list_applications_for(username)
    return apps[0] if apps else None


def save_application(username: str, application: Application) -> None:
    """Persist ``application`` to disk atomically.

    Writes to a sibling temp file and renames into place so a process
    crash mid-write can't leave a partial JSON document.

    Args:
        username: Signed-in user's username.
        application: The application to persist. ``last_updated`` is
            assumed to have been refreshed by the caller.

    """
    path = _draft_path(username, application.program_slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    payload = json.dumps(asdict(application), indent=2, ensure_ascii=False)
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def get_or_create_draft(username: str, program_slug: str) -> Application:
    """Return the existing draft for the user, or create a fresh one.

    Args:
        username: Signed-in user's username.
        program_slug: Slug of the financing programme to draft against.

    Returns:
        The loaded or newly-created ``Application``. New drafts start
        with status ``"saved"`` and a deterministic reference derived
        from ``(username, program_slug)``.

    """
    existing = get_application(username, program_slug)
    if existing is not None:
        return existing
    now = _now_iso()
    draft = Application(
        program_slug=program_slug,
        reference=_generate_reference(username, program_slug),
        status="saved",
        created_at=now,
        last_updated=now,
    )
    save_application(username, draft)
    return draft


def update_draft(
    username: str,
    program_slug: str,
    *,
    section: str | None = None,
    **field_updates: Any,
) -> Application | None:
    """Apply field updates to a draft and refresh ``last_updated``.

    Args:
        username: Signed-in user's username.
        program_slug: Programme slug.
        section: When supplied, the section name to mark as completed
            (e.g. ``"about"`` / ``"motivation"`` / ``"experience"``).
        **field_updates: Field name / value pairs to set on the
            ``Application``. Unknown fields are silently ignored to keep
            the form-handler resilient against template drift.

    Returns:
        The updated ``Application``, or ``None`` if no draft exists.
        Submitted/locked drafts are returned unchanged.

    """
    app = get_application(username, program_slug)
    if app is None:
        return None
    if not app.is_editable:
        return app

    valid_fields = {f.name for f in fields(Application)}
    for key, value in field_updates.items():
        if key in valid_fields:
            setattr(app, key, value)

    if section:
        app.sections_completed[section] = True

    app.last_updated = _now_iso()
    save_application(username, app)
    return app


def submit_application(username: str, program_slug: str) -> Application | None:
    """Move the draft from ``"saved"`` to ``"submitted"`` and persist.

    No-op if the application is already past the saved state (i.e.
    submitted/pending_review/live).

    Returns:
        The updated ``Application``, or ``None`` if no draft exists.

    """
    app = get_application(username, program_slug)
    if app is None:
        return None
    if app.status != "saved":
        return app
    now = _now_iso()
    app.status = "submitted"
    app.submitted_at = now
    app.last_updated = now
    save_application(username, app)
    return app


# ---------------------------------------------------------------------------
# File uploads
# ---------------------------------------------------------------------------


async def store_cv_upload(
    username: str,
    program_slug: str,
    upload: UploadFile,
) -> str | None:
    """Persist an uploaded CV file. Return the stored filename, or ``None``.

    Returns ``None`` for invalid extensions or oversize files.
    """
    return await _store_upload(
        username=username,
        program_slug=program_slug,
        upload=upload,
        kind="cv",
        allowed_suffixes=_ALLOWED_CV_SUFFIXES,
        max_bytes=_CV_MAX_BYTES,
    )


async def store_video_upload(
    username: str,
    program_slug: str,
    upload: UploadFile,
) -> str | None:
    """Persist an uploaded intro video. Return the stored filename, or ``None``.

    Returns ``None`` for invalid extensions or oversize files.
    """
    return await _store_upload(
        username=username,
        program_slug=program_slug,
        upload=upload,
        kind="video",
        allowed_suffixes=_ALLOWED_VIDEO_SUFFIXES,
        max_bytes=_VIDEO_MAX_BYTES,
    )


async def store_proof_of_funds_upload(
    username: str,
    program_slug: str,
    upload: UploadFile,
) -> str | None:
    """Persist an uploaded proof-of-funds document.

    Accepts PDFs and image scans (PNG/JPG) up to 10 MB, covering the
    common "bank statement screenshot" or "loan letter PDF" cases.

    Returns ``None`` for invalid extensions or oversize files.
    """
    return await _store_upload(
        username=username,
        program_slug=program_slug,
        upload=upload,
        kind="proof_of_funds",
        allowed_suffixes=_ALLOWED_PROOF_SUFFIXES,
        max_bytes=_PROOF_MAX_BYTES,
    )


def get_upload_path(
    username: str,
    program_slug: str,
    kind: str,
    filename: str,
) -> Path | None:
    """Resolve a previously uploaded file to an absolute on-disk path.

    Returns ``None`` if the file has been removed, or if the resolved
    path escapes the user's upload directory (defence against path
    traversal in the URL).
    """
    upload_root = _upload_dir(username, program_slug).resolve()
    target = (upload_root / f"{kind}__{filename}").resolve()
    try:
        target.relative_to(upload_root)
    except ValueError:
        return None
    if not target.exists() or not target.is_file():
        return None
    return target


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _store_upload(
    *,
    username: str,
    program_slug: str,
    upload: UploadFile,
    kind: str,
    allowed_suffixes: frozenset[str],
    max_bytes: int,
) -> str | None:
    """Stream an uploaded file to disk with a per-chunk size guard."""
    raw_name = upload.filename or ""
    suffix = Path(raw_name).suffix.lower()
    if suffix not in allowed_suffixes:
        return None

    upload_dir = _upload_dir(username, program_slug)
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitise_filename(raw_name) or f"{kind}{suffix}"
    target = upload_dir / f"{kind}__{safe_name}"

    bytes_written = 0
    with target.open("wb") as fh:
        while True:
            chunk = await upload.read(_UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            bytes_written += len(chunk)
            if bytes_written > max_bytes:
                fh.close()
                target.unlink(missing_ok=True)
                return None
            fh.write(chunk)

    return safe_name


def _application_from_dict(data: dict[str, Any]) -> Application:
    """Build an ``Application`` from a (possibly partial) dict.

    Tolerates extra keys (e.g. fields removed from the schema since the
    file was last written) and missing keys (newly added fields fall
    back to their dataclass defaults).
    """
    valid = {f.name for f in fields(Application)}
    filtered: dict[str, Any] = {k: v for k, v in data.items() if k in valid}
    return Application(**filtered)


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _generate_reference(username: str, program_slug: str) -> str:
    """Return a stable application reference for a user/programme pair.

    Uses SHA-256 (deterministic) so the same user keeps the same
    reference across process restarts.
    """
    digest = hashlib.sha256(f"{username}::{program_slug}".encode()).hexdigest()
    return f"APP-{digest[:4].upper()}"


def _sanitise_filename(name: str) -> str:
    """Reduce a user-supplied filename to a safe, capped ASCII string."""
    cleaned = _FILENAME_SAFE.sub("_", name).strip("_")
    return cleaned[:_MAX_FILENAME_LEN]


# Public alias kept for tests / callers that want the status type imported
# alongside the storage helpers.
__all__ = [
    "ApplicationStatus",
    "get_application",
    "get_application_for",
    "get_or_create_draft",
    "get_upload_path",
    "list_applications_for",
    "save_application",
    "store_cv_upload",
    "store_proof_of_funds_upload",
    "store_video_upload",
    "submit_application",
    "update_draft",
]

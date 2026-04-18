"""Shared input validators for Flarecrawl boundaries.

These helpers enforce narrow, defensive invariants on values that flow
from user-supplied input into filesystem paths, SQL identifiers, or
network identifiers. Each validator is side-effect free and raises
``ValueError`` on rejection so callers can surface a clean error.

Example
-------
>>> validate_job_id("my-job_42")
'my-job_42'
>>> validate_job_id("../evil")
Traceback (most recent call last):
    ...
ValueError: invalid job_id: '../evil'
"""

from __future__ import annotations

import re

__all__ = ["JOB_ID_RE", "validate_job_id"]

#: Accepted job-id character class. Matches 1-128 chars of
#: [A-Za-z0-9._-]. Deliberately excludes path separators, whitespace,
#: null bytes, and shell metacharacters so that a job-id can safely
#: participate in filesystem paths and log lines.
JOB_ID_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._-]{1,128}\Z")


def validate_job_id(job_id: str) -> str:
    """Return ``job_id`` unchanged if valid; raise ``ValueError`` otherwise.

    A job id is valid when it is a non-empty string of at most 128
    characters drawn from ``[A-Za-z0-9._-]``.
    """
    if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
        raise ValueError(f"invalid job_id: {job_id!r}")
    return job_id

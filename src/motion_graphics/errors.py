"""Structured error model. Every failure that crosses the Skill boundary is a MotionGraphicsError with a code
from ERROR_TABLE; the CLI turns it into {"ok": false, "error": {"code", "message", "retryable", "details"}}."""
from __future__ import annotations

from typing import Any, Dict, Optional

# code -> (exit code, retryable)
ERROR_TABLE: Dict[str, Any] = {
    "INVALID_REQUEST": (2, False),        # document shape / unknown fields / bad types / forbidden field
    "INVALID_INPUT": (3, False),          # an input file is missing, unreadable, not media, or not a regular file
    "PATH_NOT_ALLOWED": (4, False),       # input outside allowed roots, output outside workspace, traversal, symlink escape
    "UNSUPPORTED_OPERATION": (5, False),  # element / animation / operation type not implemented by this skill
    "UNSUPPORTED_FORMAT": (6, False),     # output/image/font format not in the contract, or codec missing
    "INVALID_TIME_RANGE": (8, False),     # start/end inconsistent, non-finite, or outside the media duration
    "DEPENDENCY_ERROR": (11, False),      # duplicate element id, missing asset/font reference
    "MISSING_INPUT": (7, False),          # a referenced font_id / asset does not exist in its registry
    "TOOL_ERROR": (12, True),             # ffmpeg-skill / ffmpeg failed, timed out, or is unavailable
    "OUTPUT_ERROR": (13, False),          # output could not be written, is empty, collides with an input, or exists
    "VALIDATION_ERROR": (14, False),      # output written but failed post-render validation
    "CANCELLED": (15, True),              # interrupted by signal
    "INTERNAL_ERROR": (16, False),        # a bug in this skill
}
ERROR_CODES = tuple(ERROR_TABLE)
EXIT_CODES = {code: ERROR_TABLE[code][0] for code in ERROR_CODES}


class MotionGraphicsError(Exception):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None, retryable: Optional[bool] = None):
        if code not in ERROR_TABLE:
            raise ValueError(f"unknown error code {code!r}")
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.retryable = ERROR_TABLE[code][1] if retryable is None else bool(retryable)

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable, "details": self.details}

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.code]

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

# Plan §10.3 — never subclass HTTPException: Starlette would drop error_class.
ERROR_CLASSES = (
    "llm",
    "docker",
    "gpu",
    "parse",
    "permission",
    "quota",
    "need_data",
    "awaiting_gpu",
    "stub",
    "no_docker",
    "no_gpu",
)


class ClassifiedHTTPException(Exception):
    """User-visible failure: short detail + error_class. No traceback in the body."""

    def __init__(self, status_code: int, detail: str, error_class: str, next_step: str | None = None) -> None:
        self.status_code = status_code
        self.detail = detail
        self.error_class = error_class if error_class in ERROR_CLASSES else "llm"
        self.next_step = (next_step or "").strip()
        super().__init__(detail)


async def classified_exception_handler(request: Request, exc: ClassifiedHTTPException) -> JSONResponse:
    content = {"detail": exc.detail, "error_class": exc.error_class}
    if exc.next_step:
        content["next_step"] = exc.next_step
    return JSONResponse(status_code=exc.status_code, content=content)


def public_exc_text(exc: BaseException, fallback: str = "operation failed") -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str) and detail.strip():
        return detail.strip()[:400]
    text = str(exc).split("Traceback")[0].strip()
    if not text or "Traceback" in str(exc):
        return fallback
    return text[:400]

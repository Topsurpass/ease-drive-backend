"""Turn unhandled exceptions into a normal response, inside the CORS layer.

Starlette's `ServerErrorMiddleware` sits *outside* every middleware the app
adds, `CORSMiddleware` included. So an unhandled exception bypasses CORS and
the browser receives a bare `500 text/plain` with no `Access-Control-Allow-
Origin` header. Chrome then reports the only thing it can see:

    Access to XMLHttpRequest ... has been blocked by CORS policy:
    No 'Access-Control-Allow-Origin' header is present

which names the wrong culprit and hides the real error. That is not
hypothetical: a missing `ease_bookings` table produced exactly that on
2026-08-14, and the CORS message sent the search in the wrong direction.

Catching here, *inside* CORSMiddleware, means the error leaves as an ordinary
JSON response, picks up its CORS headers on the way out, and the frontend can
read and report it. The exception is logged with its traceback first, so the
detail lands in the platform log rather than in the response.
"""

import logging
from collections.abc import Awaitable, Callable

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.schemas.booking import BookingError

logger = logging.getLogger("app.errors")

CallNext = Callable[[Request], Awaitable[Response]]


class ErrorEnvelopeMiddleware(BaseHTTPMiddleware):
    """Convert an unhandled exception into a JSON 500 the frontend can parse.

    Must be added *before* CORSMiddleware, because the last middleware added is
    the outermost, and CORS has to wrap this one to header its responses.

    `HTTPException` never reaches here: Starlette's `ExceptionMiddleware` sits
    further in and handles it, so a deliberate 503 keeps its own body.
    """

    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        try:
            return await call_next(request)
        except Exception:
            # exc_info so the traceback reaches the platform log. The response
            # deliberately carries no detail: it is read by a browser.
            logger.exception(
                "unhandled error on %s %s", request.method, request.url.path
            )
            body = BookingError(
                code="transport_error",
                message="The server hit an unexpected error. Please try again.",
            ).model_dump(by_alias=True)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=body
            )

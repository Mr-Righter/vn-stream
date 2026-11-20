"""Safe, versioned errors without echoed inputs or exception details."""

import logging
from uuid import uuid4

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from vn_stream.i18n import descriptor, text

LOG = logging.getLogger(__name__)


def failure(request, status, message, *, headers=None):
    return JSONResponse(
        {
            "error": descriptor(message),
            "request_id": request.scope.get("request_id") or uuid4().hex,
        },
        status,
        headers=headers,
    )


def install_error_handlers(app):
    @app.exception_handler(HTTPException)
    async def user_error(request, error):
        return failure(request, error.status_code, error.detail, headers=error.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_parameters(request, error):
        return failure(request, 422, text("errors.invalidRequestParameters"))

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        request_id = request.scope.get("request_id")
        LOG.error(
            "Unhandled request error: request_id=%s type=%s", request_id, type(error).__name__
        )
        return failure(
            request, 500, text("common.requestFailed"), headers={"Cache-Control": "no-store"}
        )

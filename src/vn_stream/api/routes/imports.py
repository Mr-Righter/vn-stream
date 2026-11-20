"""Small HTTP adapter for the durable import worker."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from vn_stream.api.schemas.envelopes import Success
from vn_stream.api.schemas.serialization import success, task_progress
from vn_stream.api.schemas.transfers import AbandonedImport, ImportTask
from vn_stream.engines.contracts import EngineId
from vn_stream.i18n import text


class CreateImportJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=200)
    upload_id: str | None = Field(default=None, min_length=32, max_length=32)
    directory_upload_id: str | None = Field(default=None, min_length=32, max_length=32)
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    password: str | None = Field(default=None, max_length=1024, repr=False)
    delete_source: bool = False
    engine_id: EngineId | None = None

    @model_validator(mode="after")
    def source(self):
        if sum(s is not None for s in (self.path, self.upload_id, self.directory_upload_id)) != 1:
            raise ValueError(text("errors.chooseExactlyOneSource"))
        return self


class RetryImportJob(BaseModel):
    engine_id: EngineId | None = None
    model_config = ConfigDict(extra="forbid")
    password: str | None = Field(default=None, max_length=1024, repr=False)


def import_job_routes(jobs):
    router = APIRouter(prefix="/api/v2")

    def call(action, *args, **kwargs):
        try:
            return action(*args, **kwargs)
        except KeyError:
            raise HTTPException(404, text("errors.importTaskNotFound")) from None
        except ValueError as error:
            raise HTTPException(
                409, error.args[0] if error.args else text("common.requestFailed")
            ) from None
        except OSError:
            raise HTTPException(400, text("errors.unableToReadTheSourceOrSaveTheImport")) from None

    def public_job(job):
        return success({**job, "progress_percent": task_progress(job)} if job else None)

    @router.post(
        "/imports",
        status_code=202,
        response_model=Success[ImportTask],
        response_model_exclude_unset=True,
    )
    def create(body: CreateImportJob):
        return public_job(call(jobs.create, **body.model_dump()))

    @router.get(
        "/imports/active",
        response_model=Success[ImportTask | None],
        response_model_exclude_unset=True,
    )
    def active():
        return public_job(jobs.active())

    @router.get(
        "/tasks/imports/{uid}",
        response_model=Success[ImportTask],
        response_model_exclude_unset=True,
    )
    def inspect(uid: str):
        return public_job(call(jobs.get, uid))

    @router.post(
        "/tasks/imports/{uid}/retry",
        status_code=202,
        response_model=Success[ImportTask],
        response_model_exclude_unset=True,
    )
    def retry(uid: str, body: RetryImportJob):
        return public_job(call(jobs.retry, uid, **body.model_dump(exclude_unset=True)))

    @router.delete(
        "/tasks/imports/{uid}",
        response_model=Success[AbandonedImport],
        response_model_exclude_unset=True,
    )
    def abandon(uid: str):
        return success(call(jobs.abandon, uid))

    return router

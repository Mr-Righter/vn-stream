"""Version-two JSON envelope contracts shared by business routes."""

from typing import Annotated, Generic, TypeVar

from pydantic import AliasChoices, BaseModel, BeforeValidator, Field

from vn_stream.api.schemas.serialization import timestamp

T = TypeVar("T")
Timestamp = Annotated[str, BeforeValidator(timestamp)]


def from_record(name: str, *aliases: str):
    return Field(validation_alias=AliasChoices(name, *aliases))


class Success(BaseModel, Generic[T]):
    data: T


class Page(BaseModel):
    page: int
    page_size: int
    total: int


class Paginated(Success[T]):
    pagination: Page


class ErrorMessage(BaseModel):
    code: str
    params: dict[str, str | int | float] = Field(default_factory=dict)


class Failure(BaseModel):
    error: ErrorMessage
    request_id: str


class Cancellation(BaseModel):
    cancelled: bool

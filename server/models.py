from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Message(Strict):
    role: Literal['system', 'user', 'assistant']
    content: str = Field(max_length=24000)


class StreamOptions(Strict):
    include_usage: bool = False


class ResponseFormat(Strict):
    type: Literal['json_object', 'text']


class Chat(Strict):
    model: str = Field(min_length=1, max_length=200)
    messages: list[Message] = Field(min_length=1, max_length=64)
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=256, ge=1, le=2048)
    stream: bool = False
    stream_options: StreamOptions | None = None
    response_format: ResponseFormat | None = None


class TenantCreate(Strict):
    name: str = Field(min_length=1, max_length=120)
    balance_micro_usd: int = Field(ge=0, le=9_000_000_000_000_000)
    token_limit: int = Field(ge=0, le=10**12)
    rpm_limit: int = Field(ge=1, le=100000)
    concurrency_limit: int = Field(ge=1, le=64)


class TenantPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal['active', 'disabled'] | None = None
    token_limit: int | None = Field(default=None, ge=0, le=10**12)
    rpm_limit: int | None = Field(default=None, ge=1, le=100000)
    concurrency_limit: int | None = Field(default=None, ge=1, le=64)


class KeyCreate(Strict):
    name: str = Field(default='API key', min_length=1, max_length=120)
    expires_at: str | None = None

    @field_validator('expires_at')
    @classmethod
    def expires(cls, value):
        if value is None:
            return None
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Timezone required')
        return parsed.astimezone(timezone.utc).isoformat()


class Rotate(Strict):
    expires_at: str | None = None
    _expires = field_validator('expires_at')(KeyCreate.expires.__func__)


class Credit(Strict):
    delta_micro_usd: int = Field(ge=-9_000_000_000_000_000, le=9_000_000_000_000_000)
    reason: str = Field(min_length=1, max_length=200)


class Price(Strict):
    model: str = Field(min_length=1, max_length=200)
    input_micro_usd_per_token: int = Field(ge=0, le=1000000)
    output_micro_usd_per_token: int = Field(ge=0, le=1000000)


class AgentRequest(Strict):
    task: str = Field(min_length=1, max_length=8192)
    tool_data: dict

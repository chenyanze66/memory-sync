from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=1, max_length=80)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"


class DeviceRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    platform: Literal["windows", "macos", "linux", "cloud-agent"]
    public_key: str = Field(description="Base64 encoded raw 32-byte Ed25519 public key")


class PushRequest(BaseModel):
    operation_id: UUID
    space: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    path: str = Field(min_length=1, max_length=1024)
    base_version_id: UUID | None = None
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: str = ""
    deleted: bool = False
    client_modified_at: datetime | None = None


class ResolveRequest(BaseModel):
    operation_id: UUID
    document_id: UUID
    parent_version_ids: list[UUID] = Field(min_length=2, max_length=32)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: str = ""
    deleted: bool = False
    client_modified_at: datetime | None = None

    @field_validator("parent_version_ids")
    @classmethod
    def unique_parents(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("parent_version_ids must be unique")
        return value

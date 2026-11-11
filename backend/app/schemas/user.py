from datetime import datetime
import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.user import UserRole

EMAIL_REGEX = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")


class UserCreate(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=100)
    role: UserRole = UserRole.ANALYST

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.lower()
        if not EMAIL_REGEX.match(v):
            raise ValueError("Invalid email format")
        return v


class UserUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    limit: int
    offset: int

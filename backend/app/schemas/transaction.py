from datetime import datetime, timedelta, timezone
from decimal import Decimal
import ipaddress
import re
import uuid

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.transaction import (
    RiskLevel,
    TransactionStatus,
    TransactionType,
)

CURRENCY_REGEX = re.compile(r"^[A-Z]{3}$")
COUNTRY_CODE_REGEX = re.compile(r"^[A-Z]{2}$")
MAX_NUMERIC_18_2 = Decimal("9999999999999999.99")


class TransactionCreate(BaseModel):
    sender_account_id: str = Field(..., min_length=1, max_length=64)
    receiver_account_id: str = Field(..., min_length=1, max_length=64)
    amount: Decimal = Field(...)
    currency: str = Field(..., min_length=3, max_length=3)
    transaction_type: TransactionType
    occurred_at: datetime
    description: str | None = Field(default=None, max_length=1000)
    channel: str | None = Field(default=None, max_length=20)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    device_id: str | None = Field(default=None, max_length=100)
    ip_address: str | None = Field(default=None, max_length=45)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount(cls, v: object) -> Decimal:
        if isinstance(v, float):
            str_v = str(v)
            if float(str_v) != v:
                raise ValueError("Float loses precision; supply string or exact decimal")
            val = Decimal(str_v)
        elif isinstance(v, (str, int)):
            val = Decimal(str(v))
        elif isinstance(v, Decimal):
            val = v
        else:
            raise ValueError("Invalid amount type")

        if val <= 0:
            raise ValueError("Amount must be greater than zero")
        if val > MAX_NUMERIC_18_2:
            raise ValueError("Amount exceeds maximum allowed precision for NUMERIC(18,2)")

        exponent = val.as_tuple().exponent
        if isinstance(exponent, int) and exponent < -2:
            raise ValueError("Amount cannot have more than 2 decimal places")
        return val

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        if not CURRENCY_REGEX.match(v):
            raise ValueError("Currency must be exactly 3 uppercase letters (ISO 4217)")
        return v

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, v: str | None) -> str | None:
        if v is not None and not COUNTRY_CODE_REGEX.match(v):
            raise ValueError("Country code must be exactly 2 uppercase letters (ISO 3166-1 alpha-2)")
        return v

    @field_validator("ip_address")
    @classmethod
    def validate_ip(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                ipaddress.ip_address(v)
            except ValueError:
                raise ValueError("Invalid IP address format")
        return v

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("occurred_at must be timezone-aware")
        now = datetime.now(timezone.utc)
        if v > now + timedelta(minutes=5):
            raise ValueError("occurred_at cannot be in the future (beyond 5 minutes clock skew)")
        return v

    @model_validator(mode="after")
    def validate_accounts(self) -> "TransactionCreate":
        if self.sender_account_id == self.receiver_account_id:
            raise ValueError("sender_account_id and receiver_account_id must be different")
        return self


class TransactionUpdate(BaseModel):
    status: TransactionStatus | None = None
    description: str | None = Field(default=None, max_length=1000)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class TransactionResponse(BaseModel):
    id: uuid.UUID
    idempotency_key: str
    sender_account_id: str
    receiver_account_id: str
    amount: Decimal
    currency: str
    transaction_type: TransactionType
    status: TransactionStatus
    description: str | None = None
    channel: str | None = None
    country_code: str | None = None
    device_id: str | None = None
    ip_address: str | None = None
    risk_score: int | None = None
    risk_level: RiskLevel | None = None
    occurred_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TransactionListResponse(BaseModel):
    items: list[TransactionResponse]
    total: int
    limit: int
    offset: int

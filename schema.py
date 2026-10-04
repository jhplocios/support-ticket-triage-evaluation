from enum import StrEnum
import re
from pydantic import BaseModel, field_validator

class Category(StrEnum):
    BILLING = "billing"
    BUG = "bug"
    ACCOUNT_ACCESS = "account_access"
    FEATURE_REQUEST = "feature_request"
    OTHER = "other"

class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    URGENT = "urgent"

ORDER_ID_PATTERN = re.compile(r"^ORD-\d{5}$")

class TicketTriage(BaseModel):
    category: Category
    priority: Priority
    order_id: str | None
    needs_human: bool


    @field_validator("order_id", mode="before")
    @classmethod
    def normalize_order_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            cleaned = v.strip()
            if not ORDER_ID_PATTERN.match(cleaned):
                raise ValueError(f"Invalid order ID format: '{cleaned}'")
            return cleaned
        raise ValueError(f"Expected str or None, received {type(v).__name__}")
    
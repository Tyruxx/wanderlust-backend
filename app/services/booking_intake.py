from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class BookingIntakeField(str, Enum):
    VENUE_CONTACT = "venue_contact"
    REQUESTOR_NAME = "requestor_name"
    RESERVATION_DATETIME = "reservation_datetime"
    PARTY_SIZE = "party_size"
    REMARKS = "remarks"
    SUMMARY_CONFIRMATION = "summary_confirmation"


BOOKING_INTAKE_ORDER: tuple[BookingIntakeField, ...] = (
    BookingIntakeField.VENUE_CONTACT,
    BookingIntakeField.REQUESTOR_NAME,
    BookingIntakeField.RESERVATION_DATETIME,
    BookingIntakeField.PARTY_SIZE,
    BookingIntakeField.REMARKS,
    BookingIntakeField.SUMMARY_CONFIRMATION,
)


class BookingIntakeState(BaseModel):
    venue_name: str
    venue_contact: str | None = None
    requestor_name: str | None = None
    reservation_datetime: str | None = None
    party_size: int | None = Field(default=None, ge=1, le=30)
    remarks: str | None = Field(default=None, max_length=500)
    summary_confirmed: bool = False

    def next_field(self) -> BookingIntakeField:
        if not self.venue_contact:
            return BookingIntakeField.VENUE_CONTACT
        if not self.requestor_name:
            return BookingIntakeField.REQUESTOR_NAME
        if not self.reservation_datetime:
            return BookingIntakeField.RESERVATION_DATETIME
        if self.party_size is None:
            return BookingIntakeField.PARTY_SIZE
        if self.remarks is None:
            return BookingIntakeField.REMARKS
        return BookingIntakeField.SUMMARY_CONFIRMATION


class BookingDatetimeValidation(BaseModel):
    valid: bool
    reason: str | None = None


def validate_future_datetime(value: datetime | None) -> BookingDatetimeValidation:
    if value is None:
        return BookingDatetimeValidation(valid=False, reason="Date and time could not be parsed.")
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if normalized <= datetime.now(timezone.utc):
        return BookingDatetimeValidation(valid=False, reason="Date and time must be in the future.")
    return BookingDatetimeValidation(valid=True)

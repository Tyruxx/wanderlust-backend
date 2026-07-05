from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
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


def parse_natural_datetime(value: str, *, now: datetime | None = None) -> datetime | None:
    """Parse common traveler booking phrases without forcing ISO input."""
    text = value.strip().lower()
    if not text:
        return None
    parsed = _parse_iso_like_datetime(text)
    if parsed is not None:
        return parsed

    time_parts = _parse_time_of_day(text)
    if time_parts is None:
        return None

    base_now = now or datetime.now()
    base_date = datetime(base_now.year, base_now.month, base_now.day)
    if "tomorrow" in text:
        date = base_date + timedelta(days=1)
    elif "today" in text or "tonight" in text:
        date = base_date
    else:
        weekday = _weekday_from_text(text)
        if weekday is None:
            date = _parse_month_day(text, base_now)
        else:
            days_until = (weekday - base_date.weekday()) % 7
            if days_until == 0 or "next " in text:
                days_until = days_until or 7
            date = base_date + timedelta(days=days_until)
        if date is None:
            return None

    hour, minute = time_parts
    parsed = datetime(date.year, date.month, date.day, hour, minute)
    if base_now.tzinfo is not None:
        parsed = parsed.replace(tzinfo=base_now.tzinfo)
    return parsed


def format_readable_datetime(value: datetime) -> str:
    weekday = value.strftime("%A")
    month = value.strftime("%B")
    hour = value.strftime("%I").lstrip("0") or "12"
    minute = "" if value.minute == 0 else f":{value.minute:02d}"
    meridiem = value.strftime("%p")
    return f"{weekday}, {month} {value.day}, {value.year} at {hour}{minute} {meridiem}"


def _parse_iso_like_datetime(text: str) -> datetime | None:
    normalized = text if "t" in text else text.replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _parse_time_of_day(text: str) -> tuple[int, int] | None:
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = match.group(3)
    if hour < 1 or hour > 12 or minute > 59:
        return None
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return hour, minute


def _weekday_from_text(text: str) -> int | None:
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    for label, index in weekdays.items():
        if label in text:
            return index
    return None


def _parse_month_day(text: str, now: datetime) -> datetime | None:
    month_names = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    match = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})\b",
        text,
    )
    if not match:
        return None
    month = month_names[match.group(1)]
    day = int(match.group(2))
    year = now.year
    try:
        candidate = datetime(year, month, day)
    except ValueError:
        return None
    if candidate.date() < now.date():
        try:
            candidate = datetime(year + 1, month, day)
        except ValueError:
            return None
    return candidate

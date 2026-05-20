"""Shared Pydantic data models used across the entire pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CompanyQuery(BaseModel):
    importer_name: str
    country: str
    hs_code: str | None = None
    category_hint: str | None = None
    cleaned_name: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    row_index: int = 0


class PhoneResult(BaseModel):
    number: str  # E.164 format
    source: str
    confidence: int
    phone_type: str = "UNKNOWN"  # MOBILE, FIXED_LINE, FIXED_LINE_OR_MOBILE, VOIP, TOLL_FREE
    audit_id: str = ""


class EmailResult(BaseModel):
    address: str
    source: str
    confidence: int
    validation_level: str = "syntax"  # syntax, mx, smtp
    audit_id: str = ""


class SocialUrls(BaseModel):
    facebook: str | None = None
    linkedin: str | None = None
    instagram: str | None = None
    twitter: str | None = None
    whatsapp: str | None = None


class GpsCoords(BaseModel):
    lat: float
    lon: float


class AuditEntry(BaseModel):
    audit_id: str
    source_name: str
    query: str
    url: str = ""
    status: str  # hit, miss, blocked, error
    latency_ms: int = 0
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    notes: str = ""


class SourceResult(BaseModel):
    source_name: str
    phones: list[PhoneResult] = Field(default_factory=list)
    emails: list[EmailResult] = Field(default_factory=list)
    website: str | None = None
    social_urls: SocialUrls = Field(default_factory=SocialUrls)
    address: str | None = None
    gps: GpsCoords | None = None
    audit_entries: list[AuditEntry] = Field(default_factory=list)
    notes: str = ""
    candidate_urls: list[str] = Field(default_factory=list)


class EnrichedRow(BaseModel):
    # Original fields
    importer_name: str
    country: str
    hs_code: str | None = None
    category_hint: str | None = None
    cleaned_name: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    row_index: int = 0

    # Normalized
    normalized_name: str = ""
    country_iso2: str = ""
    inferred_sector: str = ""

    # Contact data (up to 8 phones, 5 emails — sorted by confidence desc)
    phone_1: str = ""
    phone_1_source: str = ""
    phone_1_confidence: int = 0
    phone_1_type: str = ""
    phone_1_audit_id: str = ""

    phone_2: str = ""
    phone_2_source: str = ""
    phone_2_confidence: int = 0
    phone_2_type: str = ""
    phone_2_audit_id: str = ""

    phone_3: str = ""
    phone_3_source: str = ""
    phone_3_confidence: int = 0
    phone_3_type: str = ""
    phone_3_audit_id: str = ""

    phone_4: str = ""
    phone_4_source: str = ""
    phone_4_confidence: int = 0
    phone_4_type: str = ""
    phone_4_audit_id: str = ""

    phone_5: str = ""
    phone_5_source: str = ""
    phone_5_confidence: int = 0
    phone_5_type: str = ""
    phone_5_audit_id: str = ""

    phone_6: str = ""
    phone_6_source: str = ""
    phone_6_confidence: int = 0
    phone_6_type: str = ""
    phone_6_audit_id: str = ""

    phone_7: str = ""
    phone_7_source: str = ""
    phone_7_confidence: int = 0
    phone_7_type: str = ""
    phone_7_audit_id: str = ""

    phone_8: str = ""
    phone_8_source: str = ""
    phone_8_confidence: int = 0
    phone_8_type: str = ""
    phone_8_audit_id: str = ""

    email_1: str = ""
    email_1_source: str = ""
    email_1_confidence: int = 0
    email_1_validation: str = ""
    email_1_audit_id: str = ""

    email_2: str = ""
    email_2_source: str = ""
    email_2_confidence: int = 0
    email_2_validation: str = ""
    email_2_audit_id: str = ""

    email_3: str = ""
    email_3_source: str = ""
    email_3_confidence: int = 0
    email_3_validation: str = ""
    email_3_audit_id: str = ""

    email_4: str = ""
    email_4_source: str = ""
    email_4_confidence: int = 0
    email_4_validation: str = ""
    email_4_audit_id: str = ""

    email_5: str = ""
    email_5_source: str = ""
    email_5_confidence: int = 0
    email_5_validation: str = ""
    email_5_audit_id: str = ""

    website: str = ""
    facebook_url: str = ""
    linkedin_url: str = ""
    instagram_url: str = ""
    whatsapp_number: str = ""

    address: str = ""
    gps_lat: float | None = None
    gps_lon: float | None = None

    search_status: str = "error"  # enriched, partial, not_found, ambiguous, error, name_too_generic
    overall_confidence: int = 0
    notes: str = ""
    processed_at: datetime = Field(default_factory=datetime.utcnow)

    # Audit trail
    audit_ids: str = ""  # comma-separated audit_ids

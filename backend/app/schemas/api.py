"""API request/response schemas (transport DTOs)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..domain.enums import ExtractorMethod, LocationType, Side


class OccurrenceRef(BaseModel):
    execution_id: str
    side: Side
    location_type: LocationType
    canonical_path: str
    key: str | None = None
    raw_value: str  # client sends the observed value; server re-validates
    data_type: str = "string"
    wrapper: str | None = None


class RuleCreateRequest(BaseModel):
    variable_name: str
    producer: OccurrenceRef
    consumers: list[OccurrenceRef] = Field(default_factory=list)
    extractor_method: ExtractorMethod
    extractor_expression: str
    match_number: int = 1
    default_value: str = "__NOT_FOUND__"
    expert_override: bool = False


class RuleUpdateRequest(BaseModel):
    variable_name: str | None = None
    extractor_method: ExtractorMethod | None = None
    extractor_expression: str | None = None
    match_number: int | None = None
    default_value: str | None = None
    enabled: bool | None = None
    consumers: list[OccurrenceRef] | None = None
    expert_override: bool | None = None


class GenerateRequest(BaseModel):
    num_threads: int = 1
    loops: int = 1
    parameterize_host: bool = True
    include_cache_manager: bool = True
    include_static_secrets: bool = False
    keep_user_agent: bool = False


class ValidateRequest(BaseModel):
    # Secret/property values supplied at runtime, e.g. {"x_tokenguid": "..."}.
    # These are passed to JMeter as -Jname=value and never persisted.
    properties: dict[str, str] = {}


class ValidationIssueDTO(BaseModel):
    code: str
    detail: str


class RuleValidationResponse(BaseModel):
    valid: bool
    issues: list[ValidationIssueDTO] = Field(default_factory=list)
    resolved_value_masked: str | None = None

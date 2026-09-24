"""Canonical internal domain models.

Correlation logic runs against these typed models, never against raw Newman
dictionaries. Duplicate/ordered headers and query parameters are preserved as
lists of pairs (not dicts) because order and multiplicity are significant for
faithful JMX reconstruction.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    BodyMode,
    CandidateState,
    Classification,
    Confidence,
    DataType,
    ExtractorMethod,
    HandlingHint,
    HealthLevel,
    LocationType,
    ReadinessState,
    RuleOrigin,
    RuleState,
    Side,
)


class Pair(BaseModel):
    """An ordered name/value pair (header, query param, form field, cookie)."""

    model_config = ConfigDict(frozen=False)
    name: str
    value: str


class NormalizedRequest(BaseModel):
    method: str
    protocol: str = "https"
    host: str = ""
    port: int | None = None
    path: str = "/"
    path_segments: list[str] = Field(default_factory=list)
    raw_url: str = ""
    query: list[Pair] = Field(default_factory=list)
    headers: list[Pair] = Field(default_factory=list)
    cookies: list[Pair] = Field(default_factory=list)
    body_mode: BodyMode = BodyMode.NONE
    raw_body: str | None = None
    parsed_body: object | None = None  # parsed JSON (dict/list) when applicable
    form_data: list[Pair] = Field(default_factory=list)
    files: list[dict[str, str]] = Field(default_factory=list)  # metadata only
    content_type: str | None = None


class NormalizedResponse(BaseModel):
    status: str = ""
    code: int = 0
    headers: list[Pair] = Field(default_factory=list)
    cookies: list[Pair] = Field(default_factory=list)
    content_type: str | None = None
    raw_body_text: str = ""
    parsed_body: object | None = None
    body_size: int = 0
    decoding_warnings: list[str] = Field(default_factory=list)
    present: bool = True


class Assertion(BaseModel):
    name: str
    failed: bool = False
    error_message: str | None = None


class NormalizedExecution(BaseModel):
    id: str
    original_index: int
    cursor_position: int | None = None
    item_path: list[str] = Field(default_factory=list)  # folder/item names
    item_name: str = ""
    method: str = "GET"
    normalized_url: str = ""
    request: NormalizedRequest
    response: NormalizedResponse | None = None
    assertions: list[Assertion] = Field(default_factory=list)
    request_error: str | None = None  # Newman requestError (network/timeout)
    started_at: str | None = None
    duration_ms: float | None = None

    @property
    def has_usable_response(self) -> bool:
        return self.response is not None and self.response.present


class BusinessSignal(BaseModel):
    """A business-level problem found behind an HTTP 2xx response.

    HTTP success is not proof of a successful business flow. These are heuristic
    indicators (isSuccess=false, negative status codes, access-denied, empty
    producer datasets, failed assertions) surfaced for review; they never
    silently block correlation unless a configured success rule proves failure.
    """

    execution_id: str
    execution_index: int
    name: str
    kind: str  # success_false | error_message | negative_status | access_denied | empty_dataset | assertion_failed
    detail: str
    field_path: str | None = None
    severity: str = "review"  # "review" | "warning"


class RunHealth(BaseModel):
    total_executions: int = 0
    # --- Transport health ---
    status_distribution: dict[str, int] = Field(default_factory=dict)
    missing_responses: int = 0
    network_errors: int = 0
    timeouts: int = 0
    auth_failure_ratio: float = 0.0
    server_error_ratio: float = 0.0
    content_type_mismatches: int = 0
    # --- Business / scenario health ---
    newman_failures: int = 0
    assertion_failures: int = 0
    business_signals: list[BusinessSignal] = Field(default_factory=list)
    empty_datasets: int = 0
    # --- Roll-up ---
    level: HealthLevel = HealthLevel.OK
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class NormalizedRun(BaseModel):
    run_id: str
    filename: str
    collection_name: str = ""
    started_at: str | None = None
    executions: list[NormalizedExecution] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    health: RunHealth = Field(default_factory=RunHealth)


class ValueOccurrence(BaseModel):
    execution_id: str
    execution_index: int
    side: Side
    location_type: LocationType
    canonical_path: str            # JSONPath / header name / query key / etc.
    key: str | None = None         # human-readable key when relevant
    raw_value: str
    normalized_value: str          # comparison-normalized (e.g. trimmed)
    data_type: DataType = DataType.STRING
    char_start: int | None = None
    char_end: int | None = None
    wrapper: str | None = None     # e.g. "Bearer " prefix that wraps the value


class AlignmentPair(BaseModel):
    signature: str
    baseline_execution_id: str | None = None
    comparison_execution_id: str | None = None
    baseline_index: int | None = None
    comparison_index: int | None = None
    status: str  # matched | missing_in_comparison | extra_in_comparison | ambiguous
    ambiguous_candidates: list[str] = Field(default_factory=list)


class AlignmentReport(BaseModel):
    pairs: list[AlignmentPair] = Field(default_factory=list)
    matched: int = 0
    missing: int = 0
    extra: int = 0
    reordered: int = 0
    ambiguous: int = 0
    coverage: float = 0.0


class Evidence(BaseModel):
    """Explainable factors behind a candidate's confidence classification."""

    factors: list[str] = Field(default_factory=list)   # positive reasons
    penalties: list[str] = Field(default_factory=list)  # negative reasons
    score: float = 0.0
    baseline_value_masked: str = ""
    comparison_value_masked: str | None = None


class CorrelationCandidate(BaseModel):
    id: str
    variable_name: str
    producer: ValueOccurrence
    consumers: list[ValueOccurrence] = Field(default_factory=list)
    extractor_method: ExtractorMethod
    extractor_expression: str
    match_number: int = 1
    transformations: list[str] = Field(default_factory=list)
    classification: Classification = Classification.CORRELATION
    confidence: Confidence = Confidence.LOW
    evidence: Evidence = Field(default_factory=Evidence)
    warnings: list[str] = Field(default_factory=list)
    state: CandidateState = CandidateState.SUGGESTED


class CorrelationRule(BaseModel):
    """An approved / editable rule used for generation."""

    id: str
    variable_name: str
    origin: RuleOrigin = RuleOrigin.AUTOMATIC
    state: RuleState = RuleState.ENABLED
    producer: ValueOccurrence
    consumers: list[ValueOccurrence] = Field(default_factory=list)
    extractor_method: ExtractorMethod
    extractor_expression: str
    match_number: int = 1
    default_value: str = "__NOT_FOUND__"
    transformations: list[str] = Field(default_factory=list)
    classification: Classification = Classification.CORRELATION
    confidence: Confidence = Confidence.MEDIUM
    evidence: Evidence = Field(default_factory=Evidence)
    warnings: list[str] = Field(default_factory=list)
    expert_override: bool = False
    source_candidate_id: str | None = None


class ValueInsight(BaseModel):
    """A discovered value that is NOT a proven correlation.

    Covers parameterization candidates, external credentials, cookie-managed
    values, noise, and values needing manual review. Each carries an
    explainable reason and a recommended handling so nothing is presented as an
    auto-applied correlation without evidence.
    """

    id: str
    classification: Classification
    handling: HandlingHint
    variable_name: str | None = None
    location: ValueOccurrence
    occurrences: list[ValueOccurrence] = Field(default_factory=list)
    value_a_masked: str = ""
    value_b_masked: str | None = None
    differs_across_runs: bool = False
    reason: str = ""
    recommended_handling: str = ""
    warnings: list[str] = Field(default_factory=list)


class ClassificationSummary(BaseModel):
    """Headline counts shown in the result summary. Correlations are the ONLY
    values that receive a JMeter extractor; every other bucket is surfaced
    separately so an unproven value is never counted as a correlation."""

    correlations: int = 0
    parameterizations: int = 0
    external_credentials: int = 0
    cookie_managed: int = 0
    noise: int = 0
    review_required: int = 0

    def total(self) -> int:
        return (
            self.correlations
            + self.parameterizations
            + self.external_credentials
            + self.cookie_managed
            + self.noise
            + self.review_required
        )


class SamplerResult(BaseModel):
    label: str
    code: str = ""
    success: bool = False
    message: str = ""
    assertion_failure: str | None = None


class JMeterValidationReport(BaseModel):
    """Result of actually executing the plan with Apache JMeter 5.6.3.

    A plan is only 'validated' once JMeter has run it; XML generation and
    structural checks are never enough. This report is faithful: if samplers
    failed (e.g. the target host is unreachable from the runner) it says so.
    """

    executed: bool = False
    status: str = "validation_failed"  # JmxStatus: validated | validation_failed
    return_code: int | None = None
    timed_out: bool = False
    jmeter_version: str = "5.6.3"
    samplers_total: int = 0
    samplers_success: int = 0
    samplers_failed: int = 0
    assertion_failures: int = 0
    error_ratio: float = 0.0
    correlation_variables: list[str] = Field(default_factory=list)
    variables_extracted: list[str] = Field(default_factory=list)
    variables_missing: list[str] = Field(default_factory=list)
    required_properties: list[str] = Field(default_factory=list)
    properties_supplied: list[str] = Field(default_factory=list)
    sampler_results: list[SamplerResult] = Field(default_factory=list)
    log_errors: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class ReadinessReport(BaseModel):
    """Whether the capture pair is fit for automatic correlation.

    A pair is never READY just because every request returned 2xx. Transport
    health, assertion/business failures, matched-request coverage, producer
    availability, and scenario consistency all feed the decision.
    """

    state: ReadinessState = ReadinessState.READY
    reasons: list[str] = Field(default_factory=list)          # why not fully ready
    blockers: list[str] = Field(default_factory=list)         # hard NOT_READY causes
    scenario_warnings: list[str] = Field(default_factory=list)

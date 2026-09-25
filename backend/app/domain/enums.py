"""Enumerations for the correlation domain model."""

from __future__ import annotations

from enum import Enum


class Side(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"


class LocationType(str, Enum):
    PATH = "path"
    QUERY = "query"
    HEADER = "header"
    COOKIE = "cookie"
    JSON_BODY = "json_body"
    FORM = "form"
    TEXT_BODY = "text_body"
    XML_BODY = "xml_body"


class DataType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    NULL = "null"


class ExtractorMethod(str, Enum):
    JSON_PATH = "json_path"          # JMeter JSON Extractor (JSONPath)
    JSON_JMESPATH = "json_jmespath"  # JMeter JSON JMESPath Extractor
    XPATH2 = "xpath2"                # XPath2 Extractor
    REGEX = "regex"                  # Regular Expression Extractor
    BOUNDARY = "boundary"            # Boundary Extractor
    JSR223 = "jsr223"                # Advanced fallback (Groovy)


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    REJECTED = "rejected"


class CandidateState(str, Enum):
    SUGGESTED = "suggested"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class RuleState(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    DELETED = "deleted"


class RuleOrigin(str, Enum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


class BodyMode(str, Enum):
    NONE = "none"
    RAW = "raw"
    JSON = "json"
    URLENCODED = "urlencoded"
    FORMDATA = "formdata"
    TEXT = "text"
    XML = "xml"
    BINARY = "binary"


class AnalysisMode(str, Enum):
    TWO_RUN = "two_run"
    SINGLE_RUN = "single_run"


class HealthLevel(str, Enum):
    OK = "ok"
    WARNING = "warning"
    BLOCKER = "blocker"


class Classification(str, Enum):
    """Evidence-based classification of a discovered value.

    A value is only CORRELATION when a producer response and a later request
    consumer are proven across the captures. Everything else is classified so
    the UI never presents an unproven value as an auto-applied correlation.
    """

    CORRELATION = "correlation"
    PARAMETERIZATION = "parameterization"
    EXTERNAL_CREDENTIAL = "external_credential"
    COOKIE_MANAGED = "cookie_managed"
    NOISE = "noise"
    REVIEW_REQUIRED = "review_required"


class ReadinessState(str, Enum):
    """Whether a capture pair is fit for automatic correlation."""

    READY = "ready"
    READY_WITH_REVIEW = "ready_with_review"
    NOT_READY = "not_ready"


class JmxStatus(str, Enum):
    """Lifecycle of a generated plan. 'Validated' is reserved for a plan that
    Apache JMeter 5.6.3 has actually executed successfully - XML generation and
    structural checks alone are never 'validated'."""

    DRAFT = "draft"                        # preview only, not persisted
    GENERATED = "generated"                # persisted, structurally valid XML
    VALIDATED = "validated"                # JMeter executed it successfully
    VALIDATION_FAILED = "validation_failed"  # JMeter executed it, run failed


class HandlingHint(str, Enum):
    """Recommended handling for a non-correlation value."""

    JMETER_EXTRACTOR = "jmeter_extractor"       # proven correlation
    COOKIE_MANAGER = "cookie_manager"           # handled automatically
    USER_DEFINED_VARIABLE = "user_defined_var"  # parameterization
    CSV_DATA_SET = "csv_data_set"               # parameterization (per-thread)
    JMETER_PROPERTY = "jmeter_property"          # external credential / secret
    IGNORE = "ignore"                            # noise
    MANUAL_REVIEW = "manual_review"              # review required


class VariableSource(str, Enum):
    """Where a discovered Postman variable's value would come from, in precedence order."""

    SUPPLIED = "supplied"
    ENVIRONMENT = "environment"
    COLLECTION = "collection"
    DYNAMIC = "dynamic"          # Postman built-in, e.g. {{$guid}} - resolved by Newman itself
    SCRIPT = "script"            # set at runtime by a pre-request/test script (pm.environment.set, ...)
    UNRESOLVED = "unresolved"


class UnsupportedFeatureKind(str, Enum):
    LOCAL_DATA_FILE = "local_data_file"
    INTERACTIVE_AUTH = "interactive_auth"
    CLIENT_CERTIFICATE = "client_certificate"
    NON_HTTP_PROTOCOL = "non_http_protocol"
    DYNAMIC_REQUEST_CONSTRUCTION = "dynamic_request_construction"

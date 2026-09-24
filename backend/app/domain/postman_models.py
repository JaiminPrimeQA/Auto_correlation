"""Domain models for the Postman-collection inspection stage (Phase 1).

These never carry a variable's *value* - only its name, source, and whether
it is classified sensitive - so the inspection response can never leak a
secret regardless of how it is presented.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .enums import UnsupportedFeatureKind, VariableSource


class PostmanVariable(BaseModel):
    name: str
    source: VariableSource
    sensitive: bool
    locations: list[str] = Field(default_factory=list)


class PostmanFolder(BaseModel):
    id: str
    name: str
    path: str
    request_count: int


class UnsupportedFeature(BaseModel):
    kind: UnsupportedFeatureKind
    detail: str
    location: str


class CollectionInspection(BaseModel):
    collection_name: str
    folders: list[PostmanFolder] = Field(default_factory=list)
    variables: list[PostmanVariable] = Field(default_factory=list)
    unresolved_variable_names: list[str] = Field(default_factory=list)
    request_count_estimate: int = 0
    target_domains: list[str] = Field(default_factory=list)
    domain_warnings: list[str] = Field(default_factory=list)
    unsupported_features: list[UnsupportedFeature] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

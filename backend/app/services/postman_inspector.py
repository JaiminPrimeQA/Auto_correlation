# backend/app/services/postman_inspector.py
"""Orchestrate the Postman-collection inspection stage.

Parses the collection (and optional environment), discovers folders and
variables, detects unsupported features, and validates every statically
resolvable target against the public-HTTPS destination policy - all without
sending a single HTTP request.
"""

from __future__ import annotations

from ..core.config import Settings
from ..domain.postman_models import CollectionInspection
from .postman_domain_extractor import extract_target_domains
from .postman_folder_extractor import count_requests, extract_folders
from .postman_parser import parse_collection, parse_environment
from .postman_script_variables import script_set_variable_names
from .postman_unsupported import detect_unsupported_features
from .postman_variable_extractor import extract_variable_references
from .postman_variable_resolver import collection_variable_values, resolve_variables, unresolved_names


def inspect_collection(
    *,
    collection_raw: bytes,
    collection_filename: str,
    environment_raw: bytes | None,
    environment_filename: str | None,
    settings: Settings,
) -> CollectionInspection:
    parsed_collection = parse_collection(collection_raw, filename=collection_filename, settings=settings)
    collection_data = parsed_collection.data
    warnings = list(parsed_collection.warnings)

    environment_values: dict[str, str] = {}
    environment_data: dict | None = None
    if environment_raw is not None:
        parsed_env = parse_environment(
            environment_raw, filename=environment_filename or "environment.json", settings=settings,
        )
        environment_values = parsed_env.values
        environment_data = parsed_env.data
        warnings.extend(parsed_env.warnings)

    collection_variables = collection_variable_values(collection_data)

    folders = extract_folders(collection_data)
    references = extract_variable_references(collection_data)
    variables = resolve_variables(
        references, collection_variables=collection_variables, environment_values=environment_values,
        script_set=script_set_variable_names(collection_data),
    )
    domain_report = extract_target_domains(
        collection_data,
        environment_values={**collection_variables, **environment_values},
        settings=settings,
    )

    return CollectionInspection(
        collection_name=str(collection_data.get("info", {}).get("name") or "Untitled collection"),
        folders=folders,
        variables=variables,
        unresolved_variable_names=unresolved_names(variables),
        request_count_estimate=count_requests(collection_data.get("item", [])),
        target_domains=domain_report.resolved_domains,
        domain_warnings=domain_report.warnings,
        unsupported_features=detect_unsupported_features(collection_data, environment_data),
        warnings=warnings,
    )

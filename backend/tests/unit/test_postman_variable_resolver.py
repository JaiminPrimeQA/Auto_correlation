from app.domain.enums import VariableSource
from app.services.postman_variable_extractor import VariableReference
from app.services.postman_variable_resolver import (
    collection_variable_values,
    resolve_variables,
    unresolved_names,
)


def test_precedence_supplied_beats_environment_beats_collection():
    refs = [
        VariableReference("a", "loc"),
        VariableReference("b", "loc"),
        VariableReference("c", "loc"),
        VariableReference("d", "loc"),
    ]
    variables = resolve_variables(
        refs,
        collection_variables={"c": "from-collection", "d": "from-collection"},
        environment_values={"b": "from-env", "d": "from-env"},
        supplied={"a": "from-supplied", "d": "from-supplied"},
    )
    by_name = {v.name: v.source for v in variables}
    assert by_name["a"] == VariableSource.SUPPLIED
    assert by_name["b"] == VariableSource.ENVIRONMENT
    assert by_name["c"] == VariableSource.COLLECTION
    assert by_name["d"] == VariableSource.SUPPLIED  # supplied wins even though present everywhere


def test_unknown_variable_is_unresolved():
    refs = [VariableReference("mystery", "loc")]
    variables = resolve_variables(refs, collection_variables={}, environment_values={})
    assert variables[0].source == VariableSource.UNRESOLVED
    assert unresolved_names(variables) == ["mystery"]


def test_dynamic_postman_variables_are_never_unresolved():
    refs = [VariableReference("$guid", "loc"), VariableReference("$timestamp", "loc")]
    variables = resolve_variables(refs, collection_variables={}, environment_values={})
    assert all(v.source == VariableSource.DYNAMIC for v in variables)
    assert unresolved_names(variables) == []


def test_sensitive_names_are_flagged():
    refs = [VariableReference("password", "loc"), VariableReference("api_key", "loc"), VariableReference("username", "loc")]
    variables = resolve_variables(refs, collection_variables={}, environment_values={"username": "alice"})
    by_name = {v.name: v.sensitive for v in variables}
    assert by_name["password"] is True
    assert by_name["api_key"] is True
    assert by_name["username"] is False


def test_locations_are_deduplicated_and_aggregated_across_references():
    refs = [VariableReference("token", "Login > header"), VariableReference("token", "Login > header"),
            VariableReference("token", "Profile > header")]
    variables = resolve_variables(refs, collection_variables={}, environment_values={"token": "x"})
    assert variables[0].locations == ["Login > header", "Profile > header"]


def test_results_are_sorted_by_name():
    refs = [VariableReference("zeta", "loc"), VariableReference("alpha", "loc")]
    variables = resolve_variables(refs, collection_variables={}, environment_values={})
    assert [v.name for v in variables] == ["alpha", "zeta"]


def test_collection_variable_values_extracts_key_value_pairs():
    data = {
        "variable": [
            {"key": "host", "value": "example.com"},
            {"key": "", "value": "ignored-empty-key"},
            {"value": "ignored-missing-key"},
            "not-a-dict",
        ],
    }
    assert collection_variable_values(data) == {"host": "example.com"}


def test_collection_variable_values_handles_missing_variable_list():
    assert collection_variable_values({}) == {}


def test_collection_variable_values_canonicalizes_non_string_values_like_supplied_values():
    data = {
        "variable": [
            {"key": "flag", "value": True},
            {"key": "off", "value": False},
            {"key": "count", "value": 1},
            {"key": "ratio", "value": 1.5},
            {"key": "nothing", "value": None},
            {"key": "absent"},
            {"key": "name", "value": "raw"},
        ],
    }
    assert collection_variable_values(data) == {
        "flag": "true",
        "off": "false",
        "count": "1",
        "ratio": "1.5",
        "nothing": "",
        "absent": "",
        "name": "raw",
    }


def test_script_set_names_resolve_as_script_not_unresolved():
    from app.domain.enums import VariableSource
    from app.services.postman_variable_extractor import VariableReference
    from app.services.postman_variable_resolver import resolve_variables, unresolved_names

    refs = [VariableReference(name="token", location="Profile.header.Authorization"),
            VariableReference(name="missing", location="Profile.url")]
    variables = resolve_variables(refs, collection_variables={}, environment_values={}, script_set={"token"})
    by_name = {v.name: v for v in variables}
    assert by_name["token"].source == VariableSource.SCRIPT
    assert unresolved_names(variables) == ["missing"]


def test_static_values_take_precedence_over_script_set():
    from app.domain.enums import VariableSource
    from app.services.postman_variable_extractor import VariableReference
    from app.services.postman_variable_resolver import resolve_variables

    refs = [VariableReference(name="token", location="x")]
    variables = resolve_variables(refs, collection_variables={}, environment_values={"token": "seed"},
                                  script_set={"token"})
    assert variables[0].source == VariableSource.ENVIRONMENT

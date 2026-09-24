import json

from app.core.config import Settings
from app.services.execution_job_service import prepare_run_material
from tests.fixtures.postman_builders import pm_collection, pm_environment, pm_request


def test_prepare_run_material_precedence_collection_lt_environment_lt_supplied():
    collection = pm_collection(
        "Demo",
        [pm_request("Ping", "GET", "https://{{host}}/{{path}}/{{token}}")],
        variables=[
            {"key": "host", "value": "collection-host"},
            {"key": "path", "value": "collection-path"},
        ],
    )
    environment = pm_environment("Env", {"host": "env-host", "token": "env-token"})

    collection_data, environment_data, variable_values = prepare_run_material(
        collection_raw=json.dumps(collection).encode(),
        collection_filename="c.json",
        environment_raw=json.dumps(environment).encode(),
        environment_filename="e.json",
        supplied_values={"token": "supplied-token"},
        settings=Settings(),
    )

    assert collection_data["info"]["name"] == "Demo"
    assert environment_data is not None
    assert environment_data["name"] == "Env"
    # Collection value wins when nothing overrides it.
    assert variable_values["path"] == "collection-path"
    # Environment overrides collection.
    assert variable_values["host"] == "env-host"
    # Supplied overrides both collection and environment.
    assert variable_values["token"] == "supplied-token"


def test_prepare_run_material_without_environment_returns_none_data():
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://93.184.216.34/x")])

    collection_data, environment_data, variable_values = prepare_run_material(
        collection_raw=json.dumps(collection).encode(),
        collection_filename="c.json",
        environment_raw=None,
        environment_filename=None,
        supplied_values={},
        settings=Settings(),
    )

    assert collection_data["info"]["name"] == "Demo"
    assert environment_data is None
    assert variable_values == {}

from app.services.postman_script_variables import script_set_variable_names
from tests.fixtures.postman_builders import pm_collection, pm_folder, pm_request


def _with_test_script(request: dict, *lines: str) -> dict:
    request["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": list(lines)}}]
    return request


def test_finds_names_set_by_every_supported_setter():
    login = _with_test_script(
        pm_request("Login", "POST", "https://api.example.com/login"),
        'pm.environment.set("token", pm.response.json().token);',
        "pm.collectionVariables.set('session_id', 'x');",
        "pm.globals.set(`tenant`, 'y');",
        'pm.variables.set("local_value", 1);',
        'postman.setEnvironmentVariable("legacy_env", "z");',
        'postman.setGlobalVariable("legacy_global", "z");',
    )
    names = script_set_variable_names(pm_collection("C", [login]))
    assert names == {"token", "session_id", "tenant", "local_value", "legacy_env", "legacy_global"}


def test_ignores_getters_and_unset():
    req = _with_test_script(
        pm_request("R", "GET", "https://api.example.com/"),
        'pm.environment.get("read_only");',
        'pm.environment.unset("gone");',
    )
    assert script_set_variable_names(pm_collection("C", [req])) == set()


def test_walks_nested_folders_and_collection_level_events():
    inner = _with_test_script(pm_request("Inner", "GET", "https://api.example.com/"), 'pm.environment.set("deep", 1);')
    collection = pm_collection("C", [pm_folder("Outer", [pm_folder("Mid", [inner])])])
    collection["event"] = [{"listen": "prerequest", "script": {"exec": ['pm.variables.set("root_level", 1);']}}]
    assert script_set_variable_names(collection) == {"deep", "root_level"}


def test_accepts_exec_as_a_single_string():
    req = pm_request("R", "GET", "https://api.example.com/")
    req["event"] = [{"listen": "test", "script": {"exec": 'pm.environment.set("single", 1);'}}]
    assert script_set_variable_names(pm_collection("C", [req])) == {"single"}

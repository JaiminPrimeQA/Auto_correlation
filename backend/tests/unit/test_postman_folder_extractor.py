# backend/tests/unit/test_postman_folder_extractor.py
from app.services.postman_folder_extractor import count_requests, extract_folders
from tests.fixtures.postman_builders import pm_collection, pm_folder, pm_request


def test_extracts_nested_folders_with_paths_and_counts():
    login = pm_request("Login", "POST", "https://api.example.com/login")
    refresh = pm_request("Refresh", "POST", "https://api.example.com/refresh")
    profile = pm_request("Get Profile", "GET", "https://api.example.com/me")
    collection = pm_collection("Demo", [
        pm_folder("Auth", [login, pm_folder("Tokens", [refresh])]),
        profile,
    ])

    folders = extract_folders(collection)
    by_path = {f.path: f for f in folders}
    assert set(by_path) == {"Auth", "Auth > Tokens"}
    assert by_path["Auth"].request_count == 2  # Login + nested Refresh
    assert by_path["Auth > Tokens"].request_count == 1
    assert by_path["Auth"].id == "auth"
    assert by_path["Auth > Tokens"].id == "auth-tokens"


def test_no_folders_returns_empty_list():
    collection = pm_collection("Demo", [pm_request("Ping", "GET", "https://api.example.com/ping")])
    assert extract_folders(collection) == []


def test_count_requests_counts_all_nested_leaves():
    items = [
        pm_request("A", "GET", "https://x/a"),
        pm_folder("F", [pm_request("B", "GET", "https://x/b"), pm_folder("G", [pm_request("C", "GET", "https://x/c")])]),
    ]
    assert count_requests(items) == 3


def test_numeric_folder_name_does_not_crash():
    # A hand-edited or buggy-exporter collection could have a non-string
    # folder name (e.g. an int); _slugify's .lower() call must not crash.
    folder = pm_folder(42, [pm_request("Ping", "GET", "https://api.example.com/ping")])
    folders = extract_folders(pm_collection("Demo", [folder]))
    assert len(folders) == 1
    assert folders[0].name == "42"
    assert folders[0].id == "42"


def test_slug_collision_is_disambiguated():
    # "A B" and "A-B" both slugify to "a-b"; ids must remain unique since a
    # later phase uses `id` to let testers select a folder to scope to.
    folder_a = pm_folder("A B", [pm_request("Ping", "GET", "https://api.example.com/1")])
    folder_b = pm_folder("A-B", [pm_request("Ping", "GET", "https://api.example.com/2")])
    folders = extract_folders(pm_collection("Demo", [folder_a, folder_b]))
    ids = [f.id for f in folders]
    assert len(ids) == len(set(ids))
    assert "a-b" in ids

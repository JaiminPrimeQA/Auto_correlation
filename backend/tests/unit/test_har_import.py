import base64
import json

import pytest

from app.core.config import Settings
from app.core.errors import ProblemException
from app.services import har_import
from app.services.har_import import Decision
from app.utils.masking import is_credential_field
from tests.fixtures.har_builders import API, APP, entry, har, login_flow


def parse(entries, **kwargs):
    return har_import.parse_har(har(entries, **kwargs), filename="flow.har", settings=Settings())


def by_url(recording):
    return {(r.method, r.url.split("/", 3)[-1]): r for r in recording.reviewed}


def decisions(recording):
    return [(r.reason, r.decision) for r in recording.reviewed]


class TestClassification:
    def test_static_files_trackers_and_preflights_are_removed(self):
        reasons = [r.reason for r in parse(login_flow()).reviewed if r.decision == Decision.REMOVED]
        assert reasons == ["JavaScript", "CSS", "Image", "Static file", "CORS preflight", "Tracking beacon",
                           "Tracker", "Image"]

    def test_app_api_and_pages_are_kept_across_the_sites_domains(self):
        kept = [r.url for r in parse(login_flow()).reviewed if r.decision == Decision.KEEP]
        assert kept == [f"{APP}/login", f"{API}/api/auth/login", f"{API}/api/users/me"]

    def test_third_party_api_is_listed_unticked(self):
        reviewed = next(r for r in parse(login_flow()).reviewed if "vendor" in r.url)
        assert (reviewed.decision, reviewed.reason) == (Decision.OPTIONAL, "Third-party service")

    def test_failed_login_behind_http_200_is_unticked_with_its_flag(self):
        logins = [r for r in parse(login_flow()).reviewed if r.url.endswith("/login") and r.method == "POST"]
        assert [(r.decision, r.reason) for r in logins] == [
            (Decision.OPTIONAL, "Failed attempt: isLoginSuccessful is false"), (Decision.KEEP, "API call"),
        ]

    def test_http_errors_are_unticked(self):
        recording = parse([entry("GET", f"{API}/api/x", status=500, response="{}")])
        assert recording.reviewed[0].reason == "Failed attempt: HTTP 500"

    def test_recordings_without_chrome_metadata_use_mime_and_extension(self):
        recording = parse([
            entry("GET", f"{APP}/bundle.abc.js", mime="text/plain"),
            entry("GET", f"{APP}/render", mime="image/webp"),
            entry("GET", f"{APP}/api/data"),
        ], pages=("not a url",))
        assert [r.decision for r in recording.reviewed] == [Decision.REMOVED, Decision.REMOVED, Decision.KEEP]

    def test_post_returning_an_image_is_not_treated_as_static(self):
        assert parse([entry("POST", f"{APP}/api/render", mime="image/png", body="{}")]).reviewed[0].decision == Decision.KEEP

    def test_query_strings_are_left_out_of_the_review_list(self):
        assert parse([entry("GET", f"{APP}/go?token=abc")]).reviewed[0].url == f"{APP}/go"


    def test_api_paths_named_assets_are_kept(self):
        recording = parse([
            entry("GET", f"{API}/api/assets/123"),
            entry("GET", f"{APP}/static/lang.json"),
            entry("POST", f"{API}/upload/avatar.png", body="{}"),
        ])
        assert [r.decision for r in recording.reviewed] == [Decision.KEEP, Decision.REMOVED, Decision.KEEP]

    def test_an_app_on_a_tracker_domain_is_not_removed_from_its_own_recording(self):
        recording = parse([entry("GET", "https://one.newrelic.com/api/x")], pages=("https://one.newrelic.com/",))
        assert recording.reviewed[0].decision == Decision.KEEP

    def test_non_web_requests_are_removed(self):
        recording = parse([entry("GET", "chrome-extension://abc/x"), entry("GET", "data:text/plain,hi")])
        assert [r.reason for r in recording.reviewed] == ["Not a web request", "Not a web request"]

    def test_cancelled_request_is_unticked(self):
        reviewed = parse([entry("GET", f"{API}/api/x", status=0)]).reviewed[0]
        assert (reviewed.decision, reviewed.reason) == (Decision.OPTIONAL, "Failed attempt: no response")

    def test_malformed_entries_do_not_crash(self):
        bad = entry("GET", "http://[bad/x")
        odd = entry("POST", f"{API}/x", body="{}")
        odd["request"]["postData"]["text"] = 5
        odd["request"]["headers"] = 7
        odd["response"]["content"]["text"] = 9
        recording = parse([bad, odd])
        assert recording.reviewed[0].reason == "Not a web request"
        assert recording.cleaned_bytes({1}) == 0
        assert har_import.to_report(recording, {1})["run"]["executions"]

    def test_deeply_nested_response_does_not_crash(self):
        assert parse([entry("GET", f"{API}/x", response="[" * 200_000)]).reviewed

    def test_first_party_ignores_requests_outside_a_page(self):
        outside = entry("GET", "https://widgets.other.com/app", mime="text/html")
        page = entry("GET", f"{APP}/home", mime="text/html")
        page["pageref"] = "page_1"
        recording = parse([outside, page], pages=("Doc title",))
        assert [r.decision for r in recording.reviewed] == [Decision.OPTIONAL, Decision.KEEP]


class TestCookies:
    def test_only_cookies_an_earlier_response_supplied_are_kept(self):
        token = "tok-from-response-123456"
        recording = parse([
            entry("GET", f"{API}/a", response=json.dumps({"token": token})),
            entry("GET", f"{API}/b", response="{}",
                  headers=[{"name": "Cookie", "value": f"session=live-secret-value; token={token}"}]),
            entry("GET", f"{API}/c", response="{}", headers=[{"name": "Cookie", "value": "session=live-secret-value"}]),
        ])
        executions = har_import.to_report(recording, {0, 1, 2})["run"]["executions"]
        assert executions[1]["request"]["header"] == [{"key": "Cookie", "value": f"token={token}"}]
        assert executions[2]["request"]["header"] == []


class TestRuntimeValues:
    def request(self, entry_):
        recording = parse([entry_])
        return har_import.to_report(recording, {0})["run"]["executions"][0]["request"]

    def dated(self, query, when="2026-11-06T09:36:01.028Z"):
        recorded = entry("GET", f"{API}/api/x?{query}", response="{}")
        recorded["startedDateTime"] = when
        return self.request(recorded)["url"].split("?")[1]

    @pytest.mark.parametrize("query,expected", [
        ("calendarYear=2026&calendarMonth=11", "calendarYear=${__time(yyyy,)}&calendarMonth=${__time(M,)}"),
        ("month=11&day=06", "month=${__time(M,)}&day=${__time(dd,)}"),
        ("startDate=2026-11-06", "startDate=${__time(yyyy-MM-dd,)}"),
        ("year=2025&pageSize=2026", "year=2025&pageSize=2026"),
        ("birthday=6", "birthday=6"),
        ("flag&a=1", "flag&a=1"),
    ])
    def test_dates_of_the_recording_become_live(self, query, expected):
        assert self.dated(query) == expected

    def test_entry_without_a_timestamp_keeps_its_url(self):
        assert self.request(entry("GET", f"{API}/api/x?year=2026"))["url"].endswith("year=2026")

    def test_login_username_can_be_supplied_at_run_time(self):
        body = json.dumps({"Username": "a,b", "LoginMethod": "password"})
        raw = self.request(entry("POST", f"{API}/api/auth/login", body=body))["body"]["raw"]
        assert json.loads(raw) == {"Username": "${__P(Username,a\\,b)}", "LoginMethod": "password"}

    def test_login_form_username_can_be_supplied_at_run_time(self):
        form = self.request(entry("POST", f"{API}/signin", body="email=a%40b.c&x=1",
                                  post_mime="application/x-www-form-urlencoded"))["body"]["urlencoded"]
        assert form == [{"key": "email", "value": "${__P(email,a@b.c)}"}, {"key": "x", "value": "1"}]

    def test_username_outside_login_stays_as_recorded(self):
        body = json.dumps({"Username": "tester"})
        assert self.request(entry("POST", f"{API}/api/profile", body=body))["body"]["raw"] == body


class TestRedirects:
    def chain(self):
        return [
            entry("GET", f"{APP}/", status=302, mime="text/html", redirect="/next", resource="document"),
            entry("GET", f"{APP}/next", status=301, mime="text/html", redirect=f"{APP}/end", resource="document"),
            entry("GET", f"{APP}/end", mime="text/html", resource="document", response='<input value="abc123">'),
        ]

    def test_only_the_first_hop_stays(self):
        assert [r.decision for r in parse(self.chain()).reviewed] == [Decision.KEEP, Decision.REMOVED, Decision.REMOVED]

    def test_first_hop_takes_the_final_response(self):
        recording = parse(self.chain())
        execution = har_import.to_report(recording, {0})["run"]["executions"][0]
        assert execution["request"]["url"] == f"{APP}/"
        assert (execution["response"]["code"], execution["response"]["stream"]) == (200, '<input value="abc123">')

    def test_unmatched_redirect_is_left_as_recorded(self):
        recording = parse([entry("GET", f"{APP}/", status=302, mime="text/html", redirect="/gone")])
        assert recording.final_entry == {}


class TestReport:
    def report(self, entries, selected=None):
        recording = parse(entries)
        return har_import.to_report(recording, recording.default_selection() if selected is None else selected)

    def test_pseudo_and_fetch_metadata_headers_are_dropped(self):
        executions = self.report(login_flow())["run"]["executions"]
        assert [h["key"] for h in executions[-1]["request"]["header"]] == ["x-tokenguid"]

    def test_json_body_is_raw(self):
        request = self.report([entry("POST", f"{API}/x", body='{"a": 1}')])["run"]["executions"][0]["request"]
        assert request["body"] == {"mode": "raw", "raw": '{"a": 1}'}

    def test_form_body_is_decoded_into_fields(self):
        request = self.report([entry("POST", f"{API}/x", body="a=1%202&b=", post_mime="application/x-www-form-urlencoded")])[
            "run"]["executions"][0]["request"]
        assert request["body"]["urlencoded"] == [{"key": "a", "value": "1 2"}, {"key": "b", "value": ""}]

    def test_base64_text_is_decoded_and_binary_is_dropped(self):
        text = base64.b64encode(b'{"a": "b"}').decode()
        executions = self.report([
            entry("GET", f"{API}/text", encoding="base64", response=text),
            entry("POST", f"{API}/file", mime="application/pdf", encoding="base64", response=text, body="{}"),
        ])["run"]["executions"]
        assert [e["response"]["stream"] for e in executions] == ['{"a": "b"}', ""]

    def test_requests_stay_in_recorded_order_whatever_the_selection_order(self):
        recording = parse([entry("GET", f"{API}/a"), entry("GET", f"{API}/b")])
        names = [e["item"]["name"] for e in har_import.to_report(recording, {1, 0})["run"]["executions"]]
        assert names == ["GET /a", "GET /b"]


class TestLimits:
    def test_not_a_har(self):
        with pytest.raises(ProblemException, match="not a HAR"):
            har_import.parse_har(b'{"run": {}}', filename="x.json", settings=Settings())

    def test_empty_recording(self):
        with pytest.raises(ProblemException, match="no recorded requests"):
            parse([])

    def test_over_the_recording_limit(self):
        with pytest.raises(ProblemException, match="1 MB recording limit"):
            har_import.parse_har(b"x" * (2 * 1024 * 1024), filename="x", settings=Settings(max_har_bytes=1024 * 1024))

    def test_huge_strings_do_not_trip_the_report_limit(self):
        big = "x" * (Settings().max_scalar_length + 1)
        assert parse([entry("GET", f"{APP}/main.js", mime="application/javascript", response=big)])

    def test_cleaned_size_ignores_removed_files(self):
        recording = parse([
            entry("GET", f"{APP}/main.js", mime="application/javascript", response="x" * 5000),
            entry("POST", f"{API}/x", body="abcd", response="123456"),
        ])
        assert recording.size_bytes > 5000
        assert recording.cleaned_bytes(recording.default_selection()) == 10

    def test_selection_is_checked(self):
        recording = parse(login_flow())
        with pytest.raises(ProblemException, match="at least one"):
            har_import.check_selection(recording, set())
        with pytest.raises(ProblemException, match="can't be selected"):
            har_import.check_selection(recording, {1})


class TestSecondRecording:
    def test_same_calls_are_chosen_without_its_failed_attempt(self):
        first = parse(login_flow()[11:])  # the good login and the API call
        second = parse(login_flow())
        assert har_import.match_selection(first, first.default_selection(), second) == {11, 12}

    def test_ticked_third_party_call_is_matched(self):
        first = parse(login_flow())
        second = parse(login_flow())
        assert 8 in har_import.match_selection(first, first.default_selection() | {8}, second)


class TestWarnings:
    def test_missing_cookies_and_bodies_are_reported(self):
        warnings = parse([entry("GET", f"{API}/a")]).warnings
        assert any("no recorded response body" in w for w in warnings)
        assert any("no cookies" in w for w in warnings)

    def test_challenge_step_is_reported(self):
        warnings = parse([entry("GET", f"{APP}/MFALanding.aspx", mime="text/html", response="x")]).warnings
        assert any("MFA" in w for w in warnings)

    def test_challenge_behind_a_redirect_is_reported(self):
        warnings = parse([
            entry("GET", f"{APP}/withdraw", status=302, mime="text/html", redirect="/MFALanding.aspx"),
            entry("GET", f"{APP}/MFALanding.aspx", mime="text/html", response="x"),
        ]).warnings
        assert any("MFA" in w for w in warnings)

    def test_cookie_header_silences_the_cookie_warning(self):
        recording = parse([entry("GET", f"{API}/a", response="{}", headers=[{"name": "Cookie", "value": "a=b"}])])
        assert recording.warnings == []


class TestCredentialFields:
    @pytest.mark.parametrize("name,path,expected", [
        ("Code", "/api/auth/login", True),
        ("pin", "/signin", True),
        ("otp", "/account/log-in", True),
        ("Code", "/api/products", False),
        ("code", None, False),
        ("password", "/anything", True),
        ("Username", "/api/auth/login", False),
    ])
    def test_login_codes_count_as_credentials(self, name, path, expected):
        assert is_credential_field(name, path) is expected



def test_report_round_trips_as_json():
    recording = parse(login_flow())
    assert json.dumps(har_import.to_report(recording, recording.default_selection()))

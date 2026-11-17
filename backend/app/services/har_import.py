"""Read a browser HAR recording: decide which requests are real API calls and
turn the chosen ones into a Newman-shaped report for the analysis pipeline.

Like the Newman parser, this only reads structure. It never executes anything
in the recording and never requests a URL found in it.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import PurePosixPath
from urllib.parse import SplitResult, parse_qsl, urljoin, urlsplit

from ..core.config import MIB, Settings
from ..core.errors import ProblemException, validation_error
from ..utils.json_safety import check_json_complexity, decode_json
from ..utils.masking import is_login_path


class Decision(str, Enum):
    KEEP = "keep"          # ticked in the review list
    OPTIONAL = "optional"  # listed, unticked
    REMOVED = "removed"    # never part of the plan


_STATIC_TYPES = {
    "script": "JavaScript", "stylesheet": "CSS", "image": "Image", "font": "Font", "media": "Media",
    "manifest": "Static file", "texttrack": "Static file", "websocket": "WebSocket", "ping": "Tracking beacon",
}
_STATIC_EXTENSIONS = {
    ".js": "JavaScript", ".mjs": "JavaScript", ".map": "JavaScript", ".css": "CSS",
    ".png": "Image", ".jpg": "Image", ".jpeg": "Image", ".gif": "Image", ".svg": "Image", ".webp": "Image",
    ".avif": "Image", ".bmp": "Image", ".ico": "Image",
    ".woff": "Font", ".woff2": "Font", ".ttf": "Font", ".otf": "Font", ".eot": "Font",
    ".mp3": "Media", ".mp4": "Media", ".webm": "Media", ".ogg": "Media", ".wav": "Media", ".m4a": "Media",
}
_STATIC_PATH = re.compile(r"/(assets|static)/", re.I)
_TRACKER_PATH = re.compile(r"^/cdn-cgi/", re.I)
_TRACKER_DOMAINS = (
    "google-analytics.com", "analytics.google.com", "googletagmanager.com", "doubleclick.net",
    "nr-data.net", "newrelic.com", "cloudflareinsights.com", "hotjar.com", "segment.io", "segment.com",
    "mixpanel.com", "amplitude.com", "sentry.io", "datadoghq.com", "clarity.ms", "fullstory.com",
    "bugsnag.com", "facebook.net",
)
_FAILURE_FLAG = re.compile(r"success|succeeded|isvalid|^ok$", re.I)
_CHALLENGE_PATH = re.compile(r"mfa|otp|captcha", re.I)
_SECOND_LEVEL_SUFFIXES = {"co", "com", "org", "net", "ac", "gov", "edu"}
_TEXT_MIME = re.compile(r"text|json|xml|html|javascript|urlencoded", re.I)
_THIRD_PARTY = "Third-party service"
_USER_FIELD = re.compile(r"^(user(_?name|_?id)?|email|login(_?id)?)$", re.I)
_DAY_FIELD = re.compile(r"(^|[_-])day$|[a-z]Day$")


@dataclass
class ReviewedEntry:
    index: int
    method: str
    url: str
    status: int
    mime: str
    decision: Decision
    reason: str


@dataclass
class HarRecording:
    filename: str
    size_bytes: int
    entries: list[dict]
    reviewed: list[ReviewedEntry]
    warnings: list[str] = field(default_factory=list)
    # Redirect-chain head index -> index of the response JMeter ends up with.
    final_entry: dict[int, int] = field(default_factory=dict)
    # Redirect-chain head index -> the redirecting hops, head first.
    redirect_hops: dict[int, list[int]] = field(default_factory=dict)

    def default_selection(self) -> set[int]:
        return {r.index for r in self.reviewed if r.decision == Decision.KEEP}

    def counts(self) -> Counter:
        return Counter(r.decision.value for r in self.reviewed)

    def removed_by_reason(self) -> dict[str, int]:
        return dict(Counter(r.reason for r in self.reviewed if r.decision == Decision.REMOVED).most_common())

    def cleaned_bytes(self, selected: set[int]) -> int:
        return sum(self._entry_bytes(i) for i in selected)

    def _entry_bytes(self, index: int) -> int:
        final = self.entries[self.final_entry.get(index, index)]
        return len(_string(_post_data(self.entries[index]), "text")) + len(_string(_content(final), "text"))


def _section(entry: dict, section: str) -> dict:
    value = entry.get(section)
    return value if isinstance(value, dict) else {}


def _string(section: dict, key: str) -> str:
    value = section.get(key)
    return value if isinstance(value, str) else ""


def _content(entry: dict) -> dict:
    content = _section(entry, "response").get("content")
    return content if isinstance(content, dict) else {}


def _post_data(entry: dict) -> dict:
    post = _section(entry, "request").get("postData")
    return post if isinstance(post, dict) else {}


def _mime(entry: dict) -> str:
    return str(_content(entry).get("mimeType") or "").split(";")[0].strip().lower()


def _url(entry: dict) -> str:
    return str(_section(entry, "request").get("url") or "")


def _method(entry: dict) -> str:
    return str(_section(entry, "request").get("method") or "GET").upper()


def _status(entry: dict) -> int:
    status = _section(entry, "response").get("status")
    return status if isinstance(status, int) else 0


def _parts(url: str) -> SplitResult:
    try:
        return urlsplit(url)
    except ValueError:  # a malformed IPv6 host
        return urlsplit("")


def _host(url: str) -> str:
    return (_parts(url).hostname or "").lower()


def _site_name(host: str) -> str:
    """'demo.testewallet.com' and 'api.testewallet.net' both give 'testewallet'."""
    labels = host.split(".")
    if len(labels) < 2 or host.replace(".", "").isdigit():
        return host
    if len(labels) > 2 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL_SUFFIXES:
        return labels[-3]
    return labels[-2]


def _first_party_sites(log: dict, entries: list[dict]) -> set[str]:
    pages = log.get("pages")
    titles = [p.get("title") for p in pages if isinstance(p, dict)] if isinstance(pages, list) else []
    sites = {_site_name(_host(t)) for t in titles if isinstance(t, str) and t.startswith(("http://", "https://"))}
    sites.discard("")
    if sites:
        return sites
    # Requests outside a page (a browser's new-tab widgets) have no pageref.
    in_a_page = [e for e in entries if e.get("pageref")] or entries
    first_page = next((e for e in in_a_page if _mime(e) == "text/html"), in_a_page[0] if in_a_page else None)
    return {_site_name(_host(_url(first_page)))} if first_page else set()


def _is_tracker(host: str) -> bool:
    return any(host == d or host.endswith(f".{d}") for d in _TRACKER_DOMAINS)


def _static_reason(entry: dict) -> str | None:
    if _method(entry) == "OPTIONS":
        return "CORS preflight"
    kind = _STATIC_TYPES.get(str(entry.get("_resourceType") or ""))
    if kind:
        return kind
    if _method(entry) in ("GET", "HEAD"):
        path = _parts(_url(entry)).path
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in _STATIC_EXTENSIONS:
            return _STATIC_EXTENSIONS[suffix]
        if suffix and _STATIC_PATH.search(path):  # /api/assets/123 is an API, /assets/lang.json a file
            return "Static file"
        mime = _mime(entry)
        if mime.startswith("image/"):
            return "Image"
        if "font" in mime:
            return "Font"
        if mime.startswith(("audio/", "video/")):
            return "Media"
        if mime == "text/css":
            return "CSS"
        if "javascript" in mime:
            return "JavaScript"
    return None


def _decoded_text(entry: dict) -> str:
    content = _content(entry)
    text = _string(content, "text")
    if content.get("encoding") != "base64":
        return text
    if not _TEXT_MIME.search(_mime(entry)):
        return ""  # binary: nothing a text extractor could read
    try:
        return base64.b64decode(text).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return ""


def _failure_reason(entry: dict) -> str | None:
    status = _status(entry)
    if status == 0:
        return "Failed attempt: no response"
    if status >= 400:
        return f"Failed attempt: HTTP {status}"
    if "json" not in _mime(entry):
        return None
    try:
        body = json.loads(_decoded_text(entry) or "null")
    except (ValueError, RecursionError):
        return None
    if not isinstance(body, dict):
        return None
    nodes = [body, body["data"]] if isinstance(body.get("data"), dict) else [body]
    for node in reversed(nodes):  # the most specific flag names the failure best
        for key, value in node.items():
            if value is False and _FAILURE_FLAG.search(key):
                return f"Failed attempt: {key} is false"
    return None


def _follow_redirects(entries: list[dict]) -> tuple[dict[int, int], dict[int, list[int]], set[int]]:
    """Match each recorded 3xx with the request the browser made next, returning
    (chain head -> last response), (chain head -> redirecting hops) and the hops
    JMeter will follow by itself."""
    final: dict[int, int] = {}
    chains: dict[int, list[int]] = {}
    hops: set[int] = set()
    for index, entry in enumerate(entries):
        if index in hops or not 300 <= _status(entry) < 400:
            continue
        current = index
        while 300 <= _status(entries[current]) < 400:
            location = str(_section(entries[current], "response").get("redirectURL") or "")
            if not location:
                break
            target = urljoin(_url(entries[current]), location)
            next_hop = next((i for i in range(current + 1, len(entries))
                             if i not in hops and _url(entries[i]) == target and _method(entries[i]) != "OPTIONS"), None)
            if next_hop is None:
                break
            hops.add(next_hop)
            chains.setdefault(index, []).append(current)
            current = next_hop
        if current != index:
            final[index] = current
    return final, chains, hops


def _review(entry: dict, index: int, sites: set[str], hops: set[int]) -> ReviewedEntry:
    url = _url(entry)
    host = _host(url)
    first_party = _site_name(host) in sites
    decision, reason = Decision.KEEP, "API call"
    static = _static_reason(entry)
    if _parts(url).scheme not in ("http", "https"):
        decision, reason = Decision.REMOVED, "Not a web request"
    elif static:
        decision, reason = Decision.REMOVED, static
    elif (_is_tracker(host) and not first_party) or _TRACKER_PATH.match(_parts(url).path):
        decision, reason = Decision.REMOVED, "Tracker"
    elif index in hops:
        decision, reason = Decision.REMOVED, "Redirect followed automatically"
    elif not first_party:
        decision, reason = Decision.OPTIONAL, _THIRD_PARTY
    else:
        failure = _failure_reason(entry)
        if failure:
            decision, reason = Decision.OPTIONAL, failure
        elif _mime(entry) == "text/html":
            reason = "Page"
    parts = _parts(url)
    return ReviewedEntry(
        index=index, method=_method(entry),
        url=f"{parts.scheme}://{parts.netloc}{parts.path}",  # query strings can carry tokens
        status=_status(entry), mime=_mime(entry), decision=decision, reason=reason,
    )


def _warnings(entries: list[dict], reviewed: list[ReviewedEntry], final_entry: dict[int, int]) -> list[str]:
    kept = [r for r in reviewed if r.decision == Decision.KEEP]
    warnings: list[str] = []
    no_body = sum(
        1 for r in kept
        if _status(entries[r.index]) not in (204, 304)
        and not _content(entries[final_entry.get(r.index, r.index)]).get("text")
    )
    if no_body:
        warnings.append(f"{no_body} kept request(s) have no recorded response body, so they cannot supply "
                        "correlated values.")
    if not any(h["key"].lower() == "cookie" for e in entries for h in _pairs(_section(e, "request").get("headers"))):
        warnings.append("This recording has no cookies. JMeter's Cookie Manager will handle them at run time. "
                        "Chrome's default export also removes Authorization headers: if your app uses them, "
                        "export the HAR with sensitive data.")
    # A redirect can lead into the challenge, so look at where each request ends up.
    if any(_CHALLENGE_PATH.search(_parts(_url(entries[final_entry.get(r.index, r.index)])).path) for r in kept):
        warnings.append("This flow includes a captcha, MFA or OTP step, which can't be replayed automatically.")
    return warnings


def ensure_recording_fits(size: int, *, filename: str, settings: Settings) -> None:
    if size > settings.max_har_bytes:
        raise ProblemException(
            status=413, code="payload_too_large", title="Payload too large",
            detail=f"'{filename}' exceeds the {settings.max_har_bytes // MIB} MB recording limit.",
        )


def parse_har(raw: bytes, *, filename: str, settings: Settings) -> HarRecording:
    ensure_recording_fits(len(raw), filename=filename, settings=settings)
    try:
        document = decode_json(raw)
    except RecursionError as exc:
        raise validation_error("File is nested too deeply to read.") from exc
    log = document.get("log") if isinstance(document, dict) else None
    if not isinstance(log, dict) or not isinstance(log.get("entries"), list):
        raise validation_error(
            f"'{filename}' is not a HAR recording: 'log.entries' is missing.",
            errors=[{"path": "$.log.entries", "detail": "Expected an array."}],
        )
    # A recording legitimately holds huge strings (bundles, images as base64).
    check_json_complexity(document, settings.model_copy(update={"max_scalar_length": settings.max_har_bytes}))
    entries = [e for e in log["entries"] if isinstance(e, dict) and _url(e)]
    if not entries:
        raise validation_error(f"'{filename}' has no recorded requests.")

    sites = _first_party_sites(log, entries)
    final_entry, redirect_hops, hops = _follow_redirects(entries)
    reviewed = [_review(e, i, sites, hops) for i, e in enumerate(entries)]
    return HarRecording(
        filename=filename, size_bytes=len(raw), entries=entries, reviewed=reviewed, final_entry=final_entry,
        redirect_hops=redirect_hops,
        warnings=_warnings(entries, reviewed, final_entry),
    )


def check_selection(recording: HarRecording, selected: set[int]) -> None:
    if not selected:
        raise validation_error("Select at least one request to convert.")
    selectable = {r.index for r in recording.reviewed if r.decision != Decision.REMOVED}
    unknown = selected - selectable
    if unknown:
        raise validation_error(f"Request {min(unknown)} can't be selected from this recording.")


def _signature(entry: dict) -> tuple[str, str, str]:
    return _method(entry), _host(_url(entry)), _parts(_url(entry)).path


def match_selection(first: HarRecording, selected: set[int], second: HarRecording) -> set[int]:
    """The review list belongs to the first recording: choose the same requests
    from the second, leaving out what was removed or failed there."""
    wanted = {_signature(first.entries[i]) for i in selected}
    return {
        r.index for r in second.reviewed
        if _signature(second.entries[r.index]) in wanted
        and (r.decision == Decision.KEEP or r.reason == _THIRD_PARTY)
    }


def _pairs(items: object) -> list[dict]:
    if not isinstance(items, list):
        return []
    return [{"key": str(i["name"]), "value": str(i.get("value", ""))}
            for i in items if isinstance(i, dict) and i.get("name") is not None]


def _correlatable_cookies(header: str, seen_responses: str) -> str:
    """Only cookies an earlier response supplied can be correlated. The rest are
    the browser's own session state: the Cookie Manager handles those at run
    time, and a recorded value must not end up in the plan."""
    cookies = (c.strip() for c in header.split(";"))
    return "; ".join(c for c in cookies if c.partition("=")[2] and c.partition("=")[2] in seen_responses)


def _request_headers(entry: dict, seen_responses: str) -> list[dict]:
    """HTTP/2 pseudo-headers and browser fetch metadata are not replayable."""
    headers = []
    for h in _pairs(_section(entry, "request").get("headers")):
        name = h["key"].lower()
        if name.startswith((":", "sec-")) or name == "priority":
            continue
        if name == "cookie":
            h = {**h, "value": _correlatable_cookies(h["value"], seen_responses)}
            if not h["value"]:
                continue
        headers.append(h)
    return headers


def _runtime_user(name: str, value: str) -> str:
    """The recorded login stays the default; -JUsername=... picks another."""
    default = value.replace("\\", "\\\\").replace(",", "\\,")
    return f"${{__P({re.sub(r'[^A-Za-z0-9_]', '_', name)},{default})}}"


def _is_user_field(name: str, value: object) -> bool:
    return isinstance(value, str) and bool(value) and bool(_USER_FIELD.match(name))


def _json_with_runtime_user(text: str) -> str:
    try:
        body = json.loads(text)
    except (ValueError, RecursionError):
        return text
    if not isinstance(body, dict) or not any(_is_user_field(k, v) for k, v in body.items()):
        return text
    return json.dumps({k: _runtime_user(k, v) if _is_user_field(k, v) else v for k, v in body.items()})


def _request_body(entry: dict) -> dict | None:
    post = _post_data(entry)
    text = _string(post, "text")
    login = is_login_path(_parts(_url(entry)).path)
    if "urlencoded" in str(post.get("mimeType") or "").lower():
        items = [{"key": k, "value": v} for k, v in parse_qsl(text, keep_blank_values=True)] or _pairs(post.get("params"))
        if login:
            items = [{**i, "value": _runtime_user(i["key"], i["value"])} if _is_user_field(i["key"], i["value"]) else i
                     for i in items]
        return {"mode": "urlencoded", "urlencoded": items}
    if not text:
        return None
    return {"mode": "raw", "raw": _json_with_runtime_user(text) if login else text}


def _recorded_day(entry: dict) -> date | None:
    try:
        return datetime.fromisoformat(_string(entry, "startedDateTime")).date()
    except ValueError:
        return None


def _live_date(key: str, value: str, day: date) -> str:
    """A date frozen in a URL goes stale, so it becomes today's date at run time."""
    if value == day.isoformat():
        return "${__time(yyyy-MM-dd,)}"
    name = key.lower()
    if "year" in name and value == str(day.year):
        return "${__time(yyyy,)}"
    if "month" in name and value in (str(day.month), f"{day.month:02}"):
        return "${__time(M,)}" if value == str(day.month) else "${__time(MM,)}"
    if _DAY_FIELD.search(key) and value in (str(day.day), f"{day.day:02}"):
        return "${__time(d,)}" if value == str(day.day) else "${__time(dd,)}"
    return value


def _url_with_live_dates(entry: dict) -> str:
    url = _url(entry)
    base, separator, query = url.partition("?")
    day = _recorded_day(entry)
    if not separator or day is None:
        return url
    params = []
    for chunk in query.split("&"):
        key, equals, value = chunk.partition("=")
        params.append(f"{key}={_live_date(key, value, day)}" if equals else chunk)
    return f"{base}?{'&'.join(params)}"


def _hop_headers(recording: HarRecording, index: int) -> list[dict]:
    """What the redirects in between said. JMeter sees these on the same sample."""
    out: list[dict] = []
    for hop in recording.redirect_hops.get(index, []):
        response = _section(recording.entries[hop], "response")
        out.append({"key": "Location", "value": _string(response, "redirectURL")})
        out.extend(h for h in _pairs(response.get("headers")) if h["key"].lower() == "set-cookie")
    return out


def to_report(recording: HarRecording, selected: set[int]) -> dict:
    executions = []
    seen_responses = ""
    for index in sorted(selected):
        entry = recording.entries[index]
        final = recording.entries[recording.final_entry.get(index, index)]
        response = _section(final, "response")
        response_text = _decoded_text(final)
        seen_responses += response_text
        parts = _parts(_url(entry))
        request: dict = {
            "method": _method(entry), "url": _url_with_live_dates(entry),
            "header": _request_headers(entry, seen_responses),
        }
        body = _request_body(entry)
        if body:
            request["body"] = body
        executions.append({
            "item": {"name": f"{_method(entry)} {parts.path or '/'}"},
            "request": request,
            "response": {
                "code": _status(final),
                "status": str(response.get("statusText") or ""),
                "header": [*_hop_headers(recording, index), *_pairs(response.get("headers"))],
                "cookie": _pairs(response.get("cookies")),
                "stream": response_text,
                "responseTime": final.get("time") if isinstance(final.get("time"), (int, float)) else None,
            },
        })
    return {
        "collection": {"info": {"name": recording.filename}},
        "run": {"id": recording.filename, "executions": executions},
    }

"""The seven required integration scenarios as (baseline, comparison) reports."""

from __future__ import annotations

from .builders import execution, header, raw_json_body, report


def scenario_login_token(run: str) -> dict:
    """A login returns a different token per run; a later request sends it in Authorization."""
    token = "tok_AAAA1111BBBB2222CCCC3333" if run == "baseline" else "tok_ZZZZ9999YYYY8888XXXX7777"
    execs = [
        execution(
            "Login", "POST", "https://api.example.com/auth/login",
            req_headers=[header("Content-Type", "application/json")],
            req_body=raw_json_body({"user": "alice", "pass": "hunter2"}),
            resp_body={"token": token, "expiresIn": 3600},
            position=0,
        ),
        execution(
            "Get Profile", "GET", "https://api.example.com/me",
            req_headers=[header("Authorization", f"Bearer {token}")],
            resp_body={"id": 1, "name": "Alice"},
            position=1,
        ),
    ]
    return report("Login Flow", execs)


def scenario_record_id(run: str) -> dict:
    """A create returns a different record id used later in path, query, and JSON body."""
    rid = "rec_11112222" if run == "baseline" else "rec_99998888"
    execs = [
        execution(
            "Create Order", "POST", "https://api.example.com/orders",
            req_headers=[header("Content-Type", "application/json")],
            req_body=raw_json_body({"item": "widget", "qty": 2}),
            resp_code=201, resp_status="Created",
            resp_body={"orderId": rid, "status": "new"},
            position=0,
        ),
        execution(
            "Get Order", "GET", f"https://api.example.com/orders/{rid}",
            resp_body={"orderId": rid, "status": "new"},
            position=1,
        ),
        execution(
            "Search Order", "GET", f"https://api.example.com/orders?ref={rid}",
            resp_body={"results": []},
            position=2,
        ),
        execution(
            "Add Note", "POST", "https://api.example.com/notes",
            req_headers=[header("Content-Type", "application/json")],
            req_body=raw_json_body({"orderId": rid, "note": "hello"}),
            resp_code=201, resp_status="Created",
            resp_body={"noteId": "n1"},
            position=3,
        ),
    ]
    return report("Order Flow", execs)


def scenario_echoed_record_id(run: str) -> dict:
    """A later read echoes an ID created earlier; only the create is a producer."""
    rid = "rec_11112222" if run == "baseline" else "rec_99998888"
    execs = [
        execution(
            "Create Order", "POST", "https://api.example.com/orders",
            resp_code=201, resp_status="Created",
            resp_body={"id": rid},
            position=0,
        ),
        execution(
            "Get Order", "GET", f"https://api.example.com/orders/{rid}",
            resp_body={"id": rid, "status": "new"},
            position=1,
        ),
        execution(
            "Get Payments", "GET", f"https://api.example.com/orders/{rid}/payments",
            resp_body={"items": []},
            position=2,
        ),
        execution(
            "Get Order Again", "GET", f"https://api.example.com/orders/{rid}",
            resp_body={"id": rid, "status": "new"},
            position=3,
        ),
    ]
    return report("Echoed Order Flow", execs)


def scenario_csrf_html(run: str) -> dict:
    """A CSRF token appears in an HTML response and is later posted in a form."""
    csrf = "csrf_ABCDEF123456" if run == "baseline" else "csrf_GHIJKL789012"
    html = f'<html><input name="csrf" value="{csrf}"></html>'
    execs = [
        execution(
            "Load Form", "GET", "https://api.example.com/form",
            resp_headers=[header("Content-Type", "text/html")],
            resp_body=html,
            position=0,
        ),
        execution(
            "Submit Form", "POST", "https://api.example.com/submit",
            req_headers=[header("Content-Type", "application/json")],
            req_body=raw_json_body({"csrf": csrf, "value": "x"}),
            resp_body={"ok": True},
            position=1,
        ),
    ]
    return report("CSRF Flow", execs)


def scenario_session_cookie(run: str) -> dict:
    """A session cookie set via Set-Cookie, handled by the Cookie Manager."""
    sid = "sess_1234567890abcdef" if run == "baseline" else "sess_fedcba0987654321"
    execs = [
        execution(
            "Login", "POST", "https://api.example.com/login",
            resp_headers=[
                header("Content-Type", "application/json"),
                header("Set-Cookie", f"SESSIONID={sid}; Path=/; HttpOnly"),
            ],
            resp_body={"ok": True},
            position=0,
        ),
        execution(
            "Dashboard", "GET", "https://api.example.com/dashboard",
            req_headers=[header("Cookie", f"SESSIONID={sid}")],
            resp_body={"widgets": 3},
            position=1,
        ),
    ]
    return report("Cookie Flow", execs)


def scenario_static_false_positive(run: str) -> dict:
    """A repeated STATIC value present identically in both runs must NOT be correlated."""
    tenant = "acme-corp"  # identical across runs -> static
    execs = [
        execution(
            "Config", "GET", "https://api.example.com/config",
            resp_body={"tenant": tenant, "region": "us-east"},
            position=0,
        ),
        execution(
            "Use Tenant", "GET", f"https://api.example.com/tenants/{tenant}/info",
            resp_body={"ok": True},
            position=1,
        ),
    ]
    return report("Static Flow", execs)


def scenario_repeated_request(run: str) -> dict:
    """A repeated request name requiring occurrence-aware alignment."""
    t1 = "tok_first_AAAA1111" if run == "baseline" else "tok_first_BBBB2222"
    t2 = "tok_second_CCCC3333" if run == "baseline" else "tok_second_DDDD4444"
    execs = [
        execution("Token", "POST", "https://api.example.com/token",
                  resp_body={"token": t1}, position=0),
        execution("Use", "GET", "https://api.example.com/use",
                  req_headers=[header("Authorization", f"Bearer {t1}")],
                  resp_body={"ok": 1}, position=1),
        execution("Token", "POST", "https://api.example.com/token",
                  resp_body={"token": t2}, position=2),
        execution("Use", "GET", "https://api.example.com/use",
                  req_headers=[header("Authorization", f"Bearer {t2}")],
                  resp_body={"ok": 2}, position=3),
    ]
    return report("Repeated Flow", execs)


def scenario_all_401(run: str) -> dict:
    """Comparison run where every request is 401 -> auto mode must be blocked."""
    ok = run == "baseline"
    def one(name, url, pos):
        return execution(
            name, "GET", url,
            req_headers=[header("Authorization", "Bearer tok_expired")],
            resp_code=200 if ok else 401,
            resp_status="OK" if ok else "Unauthorized",
            resp_body={"data": "value"} if ok else {"error": "unauthorized"},
            position=pos,
        )
    execs = [one(f"Req {i}", f"https://api.example.com/r{i}", i) for i in range(7)]
    return report("Auth Flow", execs)

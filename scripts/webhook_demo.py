"""Local seven-request webhook demo with fresh identifiers on every journey."""

from __future__ import annotations

import argparse
import json
import secrets
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class WebhookServer(ThreadingHTTPServer):
    merchant_guid = ""
    webhook_id = 0
    journeys = 0


class Handler(BaseHTTPRequestHandler):
    def send_json(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        url = urlsplit(self.path)
        name = url.path.rsplit("/", 1)[-1]
        server = self.server
        if name == "GetAllMerchants":
            server.journeys += 1
            server.merchant_guid = str(uuid.uuid4())
            server.webhook_id = 0
            rows = [{"merchantID": 5, "merchantGUID": "unused-merchant-guid", "name": "Other"},
                    {"merchantID": 6, "merchantGUID": server.merchant_guid, "name": "Demo"}]
            if server.journeys % 2 == 0:
                rows.reverse()
            self.send_json(200, rows)
        elif name == "GetWebhookEvents":
            self.send_json(200, [{"value": 1, "name": "CUSTOMER.CREATED"}])
        elif name == "GetStatus":
            self.send_json(200, [{"value": 1, "name": "Success"}])
        else:
            query = parse_qs(url.query)
            valid = (query.get("merchantsGuid") == [server.merchant_guid]
                     and query.get("webhookID") == [str(server.webhook_id)] and server.webhook_id != 0)
            if name not in ("GetWebhookNotificationReportDataById", "GetWebhookNotificationHistory"):
                self.send_json(404, {"error": "Unknown route"})
            elif not valid:
                self.send_json(422, {"error": "Query must use the current merchant GUID and webhook ID"})
            else:
                self.send_json(200, {"isSuccess": True})

    def do_POST(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            self.send_json(400, {"error": "Invalid JSON"})
            return
        name = urlsplit(self.path).path.rsplit("/", 1)[-1]
        server = self.server
        if name == "GetWebhookNotificationReportData":
            if body.get("MerchantID") != "6":
                self.send_json(422, {"error": "MerchantID must be the string 6"})
                return
            server.webhook_id = 100000 + secrets.randbelow(900000)
            self.send_json(200, {"webhookId": server.webhook_id, "merchantGUID": server.merchant_guid})
        elif name == "UpdateWebhookNotificationStatus":
            valid = (type(body.get("webhookId")) is int and body.get("webhookId") == server.webhook_id
                     and body.get("merchantsGuid") == server.merchant_guid and server.webhook_id != 0)
            self.send_json(200 if valid else 422, {"isSuccess": valid})
        else:
            self.send_json(404, {"error": "Unknown route"})

    def log_message(self, *args):
        pass


def collection(port: int) -> dict:
    names = ["GetAllMerchants", "GetWebhookEvents", "GetStatus", "GetWebhookNotificationReportData",
             "GetWebhookNotificationReportDataById", "GetWebhookNotificationHistory", "UpdateWebhookNotificationStatus"]
    items = []
    for index, name in enumerate(names):
        request = {"method": "GET", "url": "{{baseUrl}}/" + name}
        tests = ["pm.test('HTTP 200', () => pm.response.to.have.status(200));"]
        if index == 0:
            tests += ["const merchant = pm.response.json().find(x => x.merchantID === 6);",
                      "pm.collectionVariables.set('merchantID', merchant.merchantID);",
                      "pm.collectionVariables.set('merchantGUID', merchant.merchantGUID);"]
        if index in (3, 6):
            request["method"] = "POST"
            request["header"] = [{"key": "Content-Type", "value": "application/json"}]
            raw = '{"MerchantID":"{{merchantID}}"}' if index == 3 else '{"webhookId":{{webhookID}},"merchantsGuid":"{{merchantGUID}}"}'
            request["body"] = {"mode": "raw", "raw": raw, "options": {"raw": {"language": "json"}}}
        if index == 3:
            tests += ["pm.collectionVariables.set('webhookID', pm.response.json().webhookId);"]
        if index in (4, 5):
            request["url"] += "?merchantsGuid={{merchantGUID}}&webhookID={{webhookID}}"
        if index in (4, 5, 6):
            tests += ["pm.test('Business success', () => pm.expect(pm.response.json().isSuccess).to.eql(true));"]
        items.append({"name": name, "request": request,
                      "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": tests}}]})
    return {"info": {"name": "Local Webhook Correlation Demo",
                     "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "variable": [{"key": "baseUrl", "value": f"http://127.0.0.1:{port}"}], "item": items}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()
    print(f"Webhook demo listening on http://127.0.0.1:{args.port}", flush=True)
    WebhookServer(("127.0.0.1", args.port), Handler).serve_forever()

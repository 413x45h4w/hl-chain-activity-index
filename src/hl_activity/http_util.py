"""Small HTTP helpers. Stdlib only so the collector has one dependency (PyYAML)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class HttpError(RuntimeError):
    pass


def request_json(url, method="GET", body=None, headers=None, timeout=15):
    data = None
    hdrs = {"User-Agent": "hl-chain-activity/0.1", "Accept": "application/json"}
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(400).decode("utf-8", "replace")
        raise HttpError(f"HTTP {exc.code} {url} {detail}") from exc
    except Exception as exc:
        raise HttpError(f"{type(exc).__name__} {url} {exc}") from exc
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HttpError(f"non-json from {url}: {raw[:180]!r}") from exc


def json_rpc(url, method, params, timeout=15, req_id=1):
    payload = request_json(
        url,
        method="POST",
        body={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params},
        timeout=timeout,
    )
    if not isinstance(payload, dict):
        raise HttpError(f"rpc {method} on {url} returned {type(payload).__name__}")
    if payload.get("error"):
        raise HttpError(f"rpc {method} {url} {payload['error']}")
    return payload.get("result")


def first_success(endpoints, fn):
    errors = []
    for endpoint in endpoints:
        try:
            return fn(endpoint), endpoint
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    raise HttpError(" | ".join(errors) if errors else "no endpoints configured")

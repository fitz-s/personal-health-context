"""OAuth authorization-code consent on a localhost redirect, with the refresh token kept in the Keychain.

One flow for every vendor whose API the owner connects (WHOOP, Oura): `login` serves the redirect registered for the
owner's app on http://localhost:47822, exchanges the code and stores the refresh token; `access_token` redeems it and
stores the replacement first, because these vendors rotate refresh tokens on every use.
"""
from __future__ import annotations

import http.server
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass

from .config import keychain_get, keychain_set

PORT = 47822


class OAuthError(Exception):
    """A bounded vendor-API failure code (auth, rate limit, unreachable); also raised by the sync modules."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Provider:
    name: str  # also the Keychain prefix (phctx-<name>-client-id / -client-secret / -refresh-token) and redirect path
    auth_url: str
    token_url: str
    scopes: str
    refresh_scope: str | None = None  # WHOOP asks for scope=offline on refresh

    @property
    def redirect(self) -> str:
        return f'http://localhost:{PORT}/{self.name}/callback'

    def key(self, part: str) -> str:
        return f'phctx-{self.name}-{part}'


def _post(p: Provider, form: dict) -> dict:
    req = urllib.request.Request(p.token_url, data=urllib.parse.urlencode(form).encode(),
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise OAuthError(f'{p.name}_auth' if e.code in (400, 401) else f'{p.name}_http_{e.code}') from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise OAuthError(f'{p.name}_unreachable') from e


def client(p: Provider) -> tuple[str, str]:
    cid, secret = keychain_get(p.key('client-id')), keychain_get(p.key('client-secret'))
    if not cid or not secret:
        raise OAuthError(f'{p.name}_client_missing')
    return cid, secret


def connected(p: Provider) -> bool:
    return keychain_get(p.key('refresh-token')) is not None


def login(p: Provider, open_browser=webbrowser.open, timeout: float = 600) -> dict:
    """One-time owner consent. Prints the consent URL (to open in the browser that holds the vendor session)."""
    cid, secret = client(p)
    state = secrets.token_urlsafe(16)
    got: dict = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            q = urllib.parse.parse_qs(url.query)
            ok = url.path == f'/{p.name}/callback' and q.get('state') == [state]
            if ok:
                got.update(code=(q.get('code') or [''])[0], error=(q.get('error') or [''])[0])
            self.send_response(200 if ok else 400)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(f'{p.name} connected; you can close this tab.'.encode() if ok else b'Unexpected request.')

        def log_message(self, *a):  # no request logging: the query carries the authorization code
            pass

    server = http.server.HTTPServer(('127.0.0.1', PORT), Callback)
    server.timeout = 5
    url = p.auth_url + '?' + urllib.parse.urlencode({'response_type': 'code', 'client_id': cid,
                                                     'redirect_uri': p.redirect, 'scope': p.scopes, 'state': state})
    print(url, flush=True)
    open_browser(url)
    deadline = time.monotonic() + timeout
    try:
        while not got and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not got.get('code'):
        raise OAuthError(f'{p.name}_consent_' + (got.get('error') or 'timeout'))
    tokens = _post(p, {'grant_type': 'authorization_code', 'code': got['code'], 'redirect_uri': p.redirect,
                       'client_id': cid, 'client_secret': secret})
    keychain_set(p.key('refresh-token'), tokens['refresh_token'])
    return {'status': 'connected', 'provider': p.name, 'scope': tokens.get('scope')}


def access_token(p: Provider) -> str:
    cid, secret = client(p)
    refresh = keychain_get(p.key('refresh-token'))
    if not refresh:
        raise OAuthError(f'{p.name}_not_connected')
    form = {'grant_type': 'refresh_token', 'refresh_token': refresh, 'client_id': cid, 'client_secret': secret}
    if p.refresh_scope:
        form['scope'] = p.refresh_scope
    tokens = _post(p, form)
    keychain_set(p.key('refresh-token'), tokens['refresh_token'])
    return tokens['access_token']

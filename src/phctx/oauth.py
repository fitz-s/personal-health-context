"""OAuth authorization-code consent on a localhost redirect, with the refresh token kept in the Keychain.

One flow for every vendor whose API the owner connects by OAuth (WHOOP): `login` serves the redirect registered for the
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
# WHOOP's Cloudflare front refuses urllib's default User-Agent (HTTP 403 "error code: 1010").
USER_AGENT = 'phctx/0.4 (personal health context)'


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
    state_chars: int = 22  # WHOOP requires an 8-character state

    @property
    def redirect(self) -> str:
        return f'http://localhost:{PORT}/{self.name}/callback'

    def key(self, part: str) -> str:
        return f'phctx-{self.name}-{part}'


def _post(p: Provider, form: dict) -> dict:
    req = urllib.request.Request(p.token_url, data=urllib.parse.urlencode(form).encode(),
                                 headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': USER_AGENT})
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


def login(providers: list[Provider], open_browser=webbrowser.open, timeout: float = 600) -> dict:
    """One-time owner consent for each provider on one localhost server (all redirects share the port).

    Consent starts at http://localhost:47822/<name>/start, which creates the authorization request when it is
    opened: a vendor login request expires (WHOOP's after about 30 minutes), so a consent page opened in advance and
    signed into later fails. Prints each start URL; returns once every provider answered or `timeout` passed."""
    clients = {p.name: (p, *client(p)) for p in providers}
    issued: dict[str, set[str]] = {n: set() for n in clients}
    got: dict[str, dict] = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            name, _, step = url.path.strip('/').partition('/')
            if name in clients and name not in got and step == 'start':
                p, cid, _ = clients[name]
                state = secrets.token_urlsafe(p.state_chars)[:p.state_chars]
                issued[name].add(state)
                self.send_response(302)
                self.send_header('Location', p.auth_url + '?' + urllib.parse.urlencode(
                    {'response_type': 'code', 'client_id': cid, 'redirect_uri': p.redirect, 'scope': p.scopes,
                     'state': state}))
                self.end_headers()
                return
            q = urllib.parse.parse_qs(url.query)
            ok = name in clients and name not in got and step == 'callback' and (q.get('state') or [''])[0] in issued[name]
            if ok:
                got[name] = {'code': (q.get('code') or [''])[0], 'error': (q.get('error') or [''])[0]}
            self.send_response(200 if ok else 400)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            # The code has not been exchanged yet, so this page only says whether consent came back; the CLI reports
            # the final outcome.
            msg = (f'{name}: consent received, finishing on the Mac.' if ok and got[name]['code'] else
                   f'{name}: NOT connected ({got[name]["error"] or "no code"}). Open /{name}/start to try again.'
                   if ok else 'Unexpected request.')
            if ok and not got[name]['code']:
                del got[name]  # a refused attempt does not end the wait: a fresh start may follow
            self.wfile.write(msg.encode())

        def log_message(self, *a):  # no request logging: the query carries the authorization code
            pass

    server = http.server.HTTPServer(('127.0.0.1', PORT), Callback)
    server.timeout = 5
    for name in clients:
        start = f'http://localhost:{PORT}/{name}/start'
        print(start, flush=True)
        open_browser(start)
    deadline = time.monotonic() + timeout
    out = {}
    try:
        while len(out) < len(clients) and time.monotonic() < deadline:
            server.handle_request()
            for name in [n for n in got if n not in out]:
                p, cid, secret = clients[name]
                try:  # exchange at once: authorization codes are short-lived
                    tokens = _post(p, {'grant_type': 'authorization_code', 'code': got[name]['code'],
                                       'redirect_uri': p.redirect, 'client_id': cid, 'client_secret': secret})
                    keychain_set(p.key('refresh-token'), tokens['refresh_token'])
                    out[name] = 'connected'
                except OAuthError as e:
                    out[name] = e.code
    finally:
        server.server_close()
    return {name: out.get(name, 'consent_timeout') for name in clients}


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

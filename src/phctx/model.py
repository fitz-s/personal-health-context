"""Background model backends. One capable-model policy; no silent downgrade to a weaker model.

Backends:
- `none`       — no model configured: the worker still schedules and records, but never claims reasoning ran.
- `codex_cli`  — the user's own authorized Codex CLI login (ChatGPT account), strong model, tools = this
                 project's MCP server over stdio in the READ-ONLY profile. No new paid service.
- `router`     — the local OpenAI-compatible router (loopback only; key from the Keychain), a bounded tool-use loop
                 that calls this project's read-only tools in-process.
- `scripted`   — deterministic test double passed in code (config.load rejects it as a production backend).

Every backend returns a candidate dict matching contracts/insight_candidate.schema.json or raises ModelError.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import jsonschema

from .config import keychain_get
from .store import dump

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_SCHEMA = json.loads((ROOT / 'contracts' / 'insight_candidate.schema.json').read_text())


class ModelError(Exception):
    """`trace` keeps whatever tool calls ran before the failure: a timeout or late CLI failure is not proof that no
    turn happened."""
    def __init__(self, code: str, trace: list | None = None):
        self.code = code
        self.trace = trace or []
        super().__init__(code)


@dataclass
class Call:
    backend: str
    model_id: str
    prompt_sha256: str
    candidate: dict
    trace: list[dict]


def background_prompt() -> str:
    return (ROOT / 'prompts' / 'background.md').read_text()


def validate_candidate(c: dict) -> dict:
    try:
        jsonschema.validate(c, CANDIDATE_SCHEMA)
    except jsonschema.ValidationError as e:
        raise ModelError('candidate_schema_invalid') from e
    return c


# Only this project's MCP tools reach the model: shell, browser, computer use, apps, plugins, sub-agents,
# image generation and web search are disabled. (Codex routes MCP calls through its exec layer, which
# therefore stays on; the read-only sandbox applies and no shell tool is exposed.)
CODEX_FEATURES_OFF = ['shell_tool', 'browser_use', 'browser_use_external', 'computer_use', 'apps', 'plugins',
                      'multi_agent', 'image_generation', 'in_app_browser', 'goals', 'code_mode_host', 'hooks',
                      'remote_plugin', 'skill_search']


def _codex_home(home: str, mcp_python: str, config_path: str | None, profile: str, file_auth: Path | None) -> None:
    """Isolated CODEX_HOME holding only this project's MCP server. Production (the background worker) takes the login
    from the OS keyring (cli_auth_credentials_store = "keyring": fails closed, independent of CODEX_HOME), so no
    auth.json is read, copied or linked. `file_auth` is for the synthetic eval harness only: a SYMLINK to that file."""
    store = 'keyring'
    if file_auth is not None:
        if not file_auth.is_file():
            raise ModelError('codex_auth_missing')
        os.symlink(file_auth.resolve(), Path(home) / 'auth.json')
        store = 'file'
    server = '' if config_path is None else (
        '[mcp_servers.phctx]\n'
        # Stands in for the user approving the host's write confirmation (ChatGPT asks before write tools);
        # headless exec otherwise cancels open-world/write calls. Permissions stay server-side.
        'default_tools_approval_mode = "approve"\n'
        f'command = {json.dumps(mcp_python)}\n'
        f'args = ["-m", "phctx", "mcp", "--profile", {json.dumps(profile)}]\n'
        f'env = {{ PHCTX_CONFIG = {json.dumps(config_path)}, PYTHONPATH = {json.dumps(str(ROOT / "src"))} }}\n'
        'tool_timeout_sec = 120\n')
    (Path(home) / 'config.toml').write_text(
        f'cli_auth_credentials_store = "{store}"\nweb_search = "disabled"\n' + server +
        '[features]\n' + ''.join(f'{f} = false\n' for f in CODEX_FEATURES_OFF))


def run_codex(prompt: str, *, model_id: str, config_path: str | None, profile: str = 'readonly',
              output_schema: dict | None = None, timeout: int = 900, cwd: str | None = None,
              developer_instructions: str | None = None, images: list[str] | None = None,
              reasoning_effort: str | None = None, result_cap: int = 4000,
              file_auth: Path | None = None) -> tuple[str, list[dict]]:
    """Run one Codex exec turn against the phctx MCP server. Returns (final_message, tool_trace).

    `cwd` is only Codex's working directory; the final message, schema and CODEX_HOME live in owned private
    temp dirs that are removed on every exit path. The final message is returned in memory only.
    """
    exe = shutil.which('codex') or os.path.expanduser('~/.npm-global/bin/codex')
    if not Path(exe).exists():
        raise ModelError('codex_cli_missing')
    home = tempfile.mkdtemp(prefix='phctx-codex-')
    own = tempfile.mkdtemp(prefix='phctx-codex-work-')
    try:
        _codex_home(home, str(ROOT / '.venv' / 'bin' / 'python'), config_path, profile, file_auth)
        last = Path(own) / 'last_message.txt'
        cmd = [exe, 'exec', '--skip-git-repo-check', '--ephemeral', '-s', 'read-only', '-m', model_id, '--json',
               '-C', cwd or own, '-o', str(last)]
        if reasoning_effort:
            cmd += ['-c', f'model_reasoning_effort="{reasoning_effort}"']
        if developer_instructions:
            cmd += ['-c', 'developer_instructions=' + json.dumps(developer_instructions)]
        for img in images or []:
            cmd += ['-i', img]
        if output_schema is not None:
            sp = Path(own) / 'schema.json'
            sp.write_text(json.dumps(output_schema))
            cmd += ['--output-schema', str(sp)]
        cmd += ['--', '-']  # prompt on stdin (never in argv/ps); '--' stops variadic -i from eating it
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout,
                                  env={**os.environ, 'CODEX_HOME': home})
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode(errors='replace') if isinstance(e.stdout, bytes) else e.stdout or ''
            raise ModelError('model_timeout', _trace(out, result_cap)) from e
        trace = _trace(proc.stdout, result_cap)
        if proc.returncode != 0 or not last.exists():
            quota = 'usage limit' in (proc.stderr + proc.stdout).lower()
            raise ModelError('model_quota_exhausted' if quota else 'model_call_failed', trace)
        return last.read_text(), trace
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(own, ignore_errors=True)


def _trace(stdout: str, result_cap: int) -> list[dict]:
    trace = []
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        item = ev.get('item') or {}
        if ev.get('type') == 'item.completed' and item.get('type') == 'mcp_tool_call':
            res = item.get('result') or {}
            text = ''.join(c.get('text', '') for c in res.get('content', []) if isinstance(c, dict))
            trace.append({'tool': item.get('tool'), 'arguments': item.get('arguments'),
                          'is_error': bool(item.get('error')) or '"error":' in text[:200],
                          'result_text': text[:result_cap]})
        elif ev.get('type') == 'item.completed' and item.get('type') not in {'agent_message', 'reasoning', None}:
            trace.append({'other_item': item.get('type'), 'detail': str(item)[:300]})
        if ev.get('type') in {'error', 'turn.failed'}:
            trace.append({'event': ev.get('type'), 'detail': str(ev)[:500]})
    return trace


# Strict-mode structured output needs every property listed as required and nullable where optional.
CODEX_CANDIDATE_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['decision', 'question_id', 'topic', 'why_now', 'what_changed', 'unknowns', 'next_step',
                 'evidence_ids', 'source_policies'],
    'properties': {
        'decision': {'type': 'string', 'enum': ['silence', 'surface']},
        'question_id': {'type': ['string', 'null']},
        'topic': {'type': ['string', 'null']}, 'why_now': {'type': ['string', 'null']},
        'what_changed': {'type': ['string', 'null']}, 'unknowns': {'type': ['string', 'null']},
        'next_step': {'type': ['string', 'null']},
        'evidence_ids': {'type': 'array', 'items': {'type': 'string'}},
        'source_policies': {'type': 'array', 'items': {'type': 'string', 'enum': ['durable', 'ephemeral', 'blocked']}},
    }}


def normalize(raw: dict) -> dict:
    """Drop nulls from the strict-mode shape so the result validates against the canonical candidate schema."""
    c = {k: v for k, v in raw.items() if v is not None}
    if c.get('decision') == 'silence':
        c = {k: v for k, v in c.items() if k in {'decision', 'question_id', 'topic'}}
    else:
        c.setdefault('evidence_versions', {})
    return c


EVIDENCE = re.compile(r'\b(?:(?:rec|obs)_[0-9a-f]{32,64}|obj:[0-9a-f]{64}(?:#p[1-9][0-9]{0,4})?)\b')
ROUTER_URL = 'http://127.0.0.1:20128/v1/chat/completions'
ROUTER_KEY = 'phctx-router-key'  # Keychain service (account phctx); read per call, never stored or logged
RETRY = {429, 500, 502, 503, 504}


def _post(body: dict, key: str, timeout: float) -> dict:
    """One chat/completions call. Loopback only, so never through an HTTP(S)_PROXY."""
    req = urllib.request.Request(ROUTER_URL, json.dumps(body).encode(),
                                 {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(4):
        try:
            with opener.open(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            why = f'http_{e.code}'
            if e.code not in RETRY or attempt == 3:
                raise ModelError('model_quota_exhausted' if e.code == 429 else 'model_call_failed',
                                 [{'event': 'router_error', 'detail': why}]) from None
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if attempt == 3:
                raise ModelError('model_call_failed', [{'event': 'router_error', 'detail': type(e).__name__}]) from None
        time.sleep(10 * 2 ** attempt)
    raise AssertionError('unreachable')


def run_router(system: str, user: str, *, model_id: str, reasoning_effort: str, tools=None,
               parse: Callable[[str], Any] | None = None, max_turns: int = 12, max_seconds: float = 480,
               max_tokens: int = 400_000, result_cap: int = 12_000) -> tuple[Any, list[dict]]:
    """Bounded tool-use loop. `tools` (a phctx.tools.Tools) lists and enforces what the model may call; its profile is
    the permission. Past max_turns, 80% of max_seconds or max_tokens the model gets one last turn with tools off.
    `parse(text, returned)` turns the final text into the result, `returned` being every tool result text of this run;
    a ModelError from it earns one repair turn. Returns (result, trace):
    every model turn with its token usage and every tool call with its arguments and truncated result."""
    key = keychain_get(ROUTER_KEY)
    if not key:
        raise ModelError('router_key_missing')
    specs = [{'type': 'function', 'function': {'name': t['name'], 'description': t['description'],
                                               'parameters': t['inputSchema']}} for t in tools.listed()] if tools else []
    messages: list[dict] = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
    trace: list[dict] = []
    used = turns = 0
    start = time.monotonic()
    last = repaired = False
    while True:
        left = max_seconds - (time.monotonic() - start)
        if left <= 0:
            raise ModelError('model_timeout', trace)
        body: dict = {'model': model_id, 'messages': messages, 'reasoning_effort': reasoning_effort}
        if specs:
            body.update(tools=specs, tool_choice='none' if last else 'auto')
        sent = time.monotonic()
        try:
            r = _post(body, key, min(left, 300))
        except ModelError as e:
            raise ModelError(e.code, trace + e.trace) from None
        waited = round(time.monotonic() - sent, 1)
        usage = r.get('usage') or {}
        used += usage.get('total_tokens') or 0
        msg = ((r.get('choices') or [{}])[0]).get('message') or {}
        calls = [] if last else msg.get('tool_calls') or []
        trace.append({'turn': len([t for t in trace if 'turn' in t]), 'prompt_tokens': usage.get('prompt_tokens', 0),
                      'completion_tokens': usage.get('completion_tokens', 0), 'tool_calls': len(calls),
                      'seconds': waited})
        if calls:
            messages.append({'role': 'assistant', 'content': msg.get('content'), 'tool_calls': calls})
            for call in calls:
                name, raw = call['function']['name'], call['function'].get('arguments') or '{}'
                try:
                    args = json.loads(raw)
                except ValueError:
                    args, res = raw, None
                else:
                    res = tools.call(name, args)
                data = res.data if res else {'error': 'invalid_arguments', 'message': 'arguments must be a JSON object'}
                text = dump(data)
                if len(text) > result_cap:
                    text = (text[:result_cap] + f'…[truncated: {result_cap} of {len(text)} chars shown; narrow the '
                            'query, select fewer columns or page]')
                messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': text})
                trace.append({'tool': name, 'arguments': args, 'is_error': res is None or res.is_error,
                              'result_text': text})
            turns += 1
            if turns >= max_turns or used >= max_tokens or time.monotonic() - start > 0.8 * max_seconds:
                last = True
                messages.append({'role': 'user', 'content': 'Investigation budget reached. Tools are now off: give '
                                                            'your final answer from what you have read.'})
            continue
        text = msg.get('content') or ''
        if parse is None:
            return text, trace
        try:
            return parse(text, ''.join(t['result_text'] for t in trace if 'tool' in t)), trace
        except ModelError as e:
            if repaired:
                raise ModelError(e.code, trace) from None
            repaired = last = True
            why = getattr(e.__cause__, 'message', None) or (str(e.__cause__) if e.__cause__ else e.code)
            messages += [{'role': 'assistant', 'content': text},
                         {'role': 'user', 'content': f'That reply is not a valid candidate ({why}). Reply with only '
                                                     'the JSON object.'}]


def parse_candidate(text: str, returned: str = '') -> dict:
    """The final reply is the candidate JSON, optionally inside one code fence. Every evidence id must be a stored
    evidence reference (not a receipt or a name) that a tool returned in this run (not recalled or retyped)."""
    body = text.strip()
    if body.startswith('```'):
        body = body.strip('`').removeprefix('json').strip()
    try:
        raw = json.loads(body)
    except ValueError:
        raise ModelError('candidate_not_json') from None
    if not isinstance(raw, dict):
        raise ModelError('candidate_not_json')
    cand = validate_candidate(normalize(raw))
    for ref, version in list(cand.get('evidence_versions', {}).items()):
        if f'"{ref}":"{version}"' not in returned:
            cand['evidence_versions'].pop(ref)  # retyped wrong, never read: the gate checks current versions anyway
    seen = {m[0] for m in EVIDENCE.finditer(returned)}
    if bad := [e for e in cand.get('evidence_ids', []) if not EVIDENCE.fullmatch(e) or e not in seen]:
        raise ModelError('candidate_evidence_invalid') from ValueError(
            f'evidence_ids must be rec_…, obs_… or obj:… ids copied exactly from tool results of this run; not so: '
            f'{", ".join(bad[:3])}')
    return cand


def investigate(backend: str, model_id: str, task: str, *, config_path: str,
                scripted: Callable[[str], dict] | None = None, file_auth: Path | None = None, tools=None,
                reasoning_effort: str = 'medium') -> Call:
    prompt = background_prompt() + '\n\n## This run\n' + task + (
        '\n\nUse the phctx tools (read-only) to inspect evidence. Return ONLY the JSON candidate.')
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    if backend == 'scripted':
        if scripted is None:
            raise ModelError('scripted_backend_missing')
        return Call('scripted', 'scripted', digest, validate_candidate(scripted(task)), [])
    if backend == 'codex_cli':
        text, trace = run_codex(prompt, model_id=model_id, config_path=config_path,
                                output_schema=CODEX_CANDIDATE_SCHEMA, file_auth=file_auth)
        try:
            cand = normalize(json.loads(text))
        except ValueError as e:
            raise ModelError('candidate_not_json') from e
        return Call('codex_cli', model_id, digest, validate_candidate(cand), trace)
    if backend == 'router':
        cand, trace = run_router(background_prompt(), task, model_id=model_id, reasoning_effort=reasoning_effort,
                                 tools=tools, parse=parse_candidate)
        return Call('router', model_id, digest, cand, trace)
    raise ModelError('model_disabled')

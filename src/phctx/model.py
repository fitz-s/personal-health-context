"""Background model backends. One capable-model policy; no silent downgrade to a weaker model.

Backends:
- `none`       — no model configured: the worker still schedules and records, but never claims reasoning ran.
- `codex_cli`  — the user's own authorized Codex CLI login (ChatGPT account), strong model, tools = this
                 project's MCP server over stdio in the READ-ONLY profile. No new paid service.
- `openai_api` — Responses API with a Keychain key; only when the user has provisioned one.
- `scripted`   — deterministic test double for the scheduling tests (never used in production config).

Every backend returns a candidate dict matching contracts/insight_candidate.schema.json or raises ModelError.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_SCHEMA = json.loads((ROOT / 'contracts' / 'insight_candidate.schema.json').read_text())


class ModelError(Exception):
    def __init__(self, code: str):
        self.code = code
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


def _codex_home(mcp_python: str, config_path: str | None, profile: str) -> str:
    """Isolated CODEX_HOME: only the user's existing auth plus this project's MCP server; no other plugins."""
    home = tempfile.mkdtemp(prefix='phctx-codex-')
    auth = Path(os.environ.get('PHCTX_CODEX_AUTH', '~/.codex/auth.json')).expanduser()
    if not auth.exists():
        raise ModelError('codex_auth_missing')
    shutil.copyfile(auth, Path(home) / 'auth.json')
    os.chmod(Path(home) / 'auth.json', 0o600)
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
        'web_search = "disabled"\n' + server +
        '[features]\n' + ''.join(f'{f} = false\n' for f in CODEX_FEATURES_OFF))
    return home


def run_codex(prompt: str, *, model_id: str, config_path: str | None, profile: str = 'readonly',
              output_schema: dict | None = None, timeout: int = 900, cwd: str | None = None,
              developer_instructions: str | None = None, images: list[str] | None = None,
              reasoning_effort: str | None = None, result_cap: int = 4000) -> tuple[str, list[dict]]:
    """Run one Codex exec turn against the phctx MCP server. Returns (final_message, tool_trace)."""
    exe = shutil.which('codex') or os.path.expanduser('~/.npm-global/bin/codex')
    if not Path(exe).exists():
        raise ModelError('codex_cli_missing')
    home = _codex_home(str(ROOT / '.venv' / 'bin' / 'python'), config_path, profile)
    work = cwd or tempfile.mkdtemp(prefix='phctx-codex-work-')
    last = Path(work) / 'last_message.txt'
    cmd = [exe, 'exec', '--skip-git-repo-check', '--ephemeral', '-s', 'read-only', '-m', model_id, '--json',
           '-C', work, '-o', str(last)]
    if reasoning_effort:
        cmd += ['-c', f'model_reasoning_effort="{reasoning_effort}"']
    if developer_instructions:
        cmd += ['-c', 'developer_instructions=' + json.dumps(developer_instructions)]
    for img in images or []:
        cmd += ['-i', img]
    if output_schema is not None:
        sp = Path(work) / 'schema.json'
        sp.write_text(json.dumps(output_schema))
        cmd += ['--output-schema', str(sp)]
    cmd += ['--', '-']  # prompt on stdin (never in argv/ps); '--' stops variadic -i from eating it
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, 'CODEX_HOME': home})
    except subprocess.TimeoutExpired as e:
        raise ModelError('model_timeout') from e
    finally:
        shutil.rmtree(home, ignore_errors=True)
    trace = []
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        item = ev.get('item') or {}
        if ev.get('type') == 'item.completed' and item.get('type') == 'mcp_tool_call':
            res = item.get('result') or {}
            text = ''.join(c.get('text', '') for c in res.get('content', []) if isinstance(c, dict))
            trace.append({'tool': item.get('tool'), 'arguments': item.get('arguments'),
                          'is_error': bool(item.get('error')) or '"error":' in text[:200], 'result_text': text[:result_cap]})
        elif ev.get('type') == 'item.completed' and item.get('type') not in {'agent_message', 'reasoning', None}:
            trace.append({'other_item': item.get('type'), 'detail': str(item)[:300]})
        if ev.get('type') in {'error', 'turn.failed'}:
            trace.append({'event': ev.get('type'), 'detail': str(ev)[:500]})
    if proc.returncode != 0 or not last.exists():
        raise ModelError('model_call_failed')
    return last.read_text(), trace


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


def investigate(backend: str, model_id: str, task: str, *, config_path: str,
                scripted: Callable[[str], dict] | None = None) -> Call:
    prompt = background_prompt() + '\n\n## This run\n' + task + (
        '\n\nUse the phctx tools (read-only) to inspect evidence. Return ONLY the JSON candidate.')
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    if backend == 'scripted':
        if scripted is None:
            raise ModelError('scripted_backend_missing')
        return Call('scripted', 'scripted', digest, validate_candidate(scripted(task)), [])
    if backend == 'codex_cli':
        text, trace = run_codex(prompt, model_id=model_id, config_path=config_path,
                                output_schema=CODEX_CANDIDATE_SCHEMA)
        try:
            cand = normalize(json.loads(text))
        except ValueError as e:
            raise ModelError('candidate_not_json') from e
        return Call('codex_cli', model_id, digest, validate_candidate(cand), trace)
    if backend == 'openai_api':
        raise ModelError('openai_api_not_provisioned')
    raise ModelError('model_disabled')

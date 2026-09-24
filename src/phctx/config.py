"""Configuration: ~/.config/phctx/config.toml (0600). Secrets are Keychain references, never values."""
from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(os.environ.get('PHCTX_CONFIG_DIR', '~/.config/phctx')).expanduser()
DEFAULT_ROOT = Path('~/Library/Application Support/PersonalHealthContext').expanduser()
BACKENDS = {'none', 'codex_cli'}  # production-selectable; `scripted` is a test double passed in code only


class ConfigError(ValueError):
    pass


@dataclass
class Config:
    profile: str = 'synthetic'
    root: Path = DEFAULT_ROOT / 'synthetic'
    timezone: str = 'America/Chicago'
    tool_profile: str = 'full'
    allowed_download_hosts: list[str] = field(default_factory=list)
    max_interactive_bytes: int = 25 * 1024 * 1024
    synthetic_test_ca: Path | None = None
    trust_fake_ip_dns: bool = False
    model_enabled: bool = False
    model_backend: str = 'none'
    model_id: str = ''
    model_keychain_service: str = 'phctx-openai'
    daily_call_cap: int = 12
    worker_mode: str = 'shadow'
    change_check_seconds: int = 900
    minimum_semantic_interval_seconds: int = 21600
    normal_cooldown_hours: int = 72
    quiet_cooldown_hours: int = 168
    macos_notification_enabled: bool = False
    backup_dir: Path | None = None  # None: <root's parent>/backups, so a root elsewhere never backs up into the real one
    cloud_backup_dir: Path | None = None
    backup_keychain_service: str = 'phctx-backup'
    ingest_host: str = '0.0.0.0'
    ingest_port: int = 47821
    source: Path | None = None
    codex_file_auth: Path | None = None  # synthetic eval harness only; never read from config (production: keyring)

    def __post_init__(self) -> None:
        if self.backup_dir is None:
            self.backup_dir = Path(self.root).expanduser().parent / 'backups'


def load(path: Path | None = None) -> Config:
    path = path or Path(os.environ.get('PHCTX_CONFIG', CONFIG_DIR / 'config.toml')).expanduser()
    cfg = Config(source=path if path.exists() else None)
    if not path.exists():
        return cfg
    raw = tomllib.loads(path.read_text())
    app, files, model = raw.get('app', {}), raw.get('files', {}), raw.get('model', {})
    worker, attention, backup, ingest = (raw.get(k, {}) for k in ('worker', 'attention', 'backup', 'ingest'))
    cfg.profile = app.get('profile', cfg.profile)
    cfg.root = Path(app.get('root', DEFAULT_ROOT / cfg.profile)).expanduser()
    cfg.timezone = app.get('timezone', cfg.timezone)
    cfg.tool_profile = app.get('tool_profile', cfg.tool_profile)
    cfg.allowed_download_hosts = list(files.get('allowed_download_hosts', []))
    cfg.max_interactive_bytes = int(files.get('max_interactive_bytes', cfg.max_interactive_bytes))
    cfg.trust_fake_ip_dns = bool(files.get('trust_fake_ip_dns', False))
    ca = files.get('synthetic_test_ca')
    cfg.synthetic_test_ca = Path(ca).expanduser() if ca and cfg.profile == 'synthetic' else None
    cfg.model_enabled = bool(model.get('enabled', False))
    cfg.model_backend = model.get('backend', cfg.model_backend)
    if cfg.model_backend not in BACKENDS:
        raise ConfigError(f'[model] backend = {cfg.model_backend!r} is not supported; use one of {sorted(BACKENDS)}.')
    if 'daily_budget_usd' in model:
        raise ConfigError('[model] daily_budget_usd is not enforced (model cost is unknown); remove it and use '
                          'daily_call_cap.')
    cfg.model_id = model.get('model_id', '')
    cfg.model_keychain_service = model.get('api_key_keychain_service', cfg.model_keychain_service)
    cfg.daily_call_cap = int(model.get('daily_call_cap', cfg.daily_call_cap))
    cfg.worker_mode = worker.get('mode', cfg.worker_mode)
    cfg.change_check_seconds = int(worker.get('change_check_seconds', cfg.change_check_seconds))
    cfg.minimum_semantic_interval_seconds = int(worker.get('minimum_semantic_interval_seconds',
                                                           cfg.minimum_semantic_interval_seconds))
    cfg.normal_cooldown_hours = int(attention.get('normal_cooldown_hours', cfg.normal_cooldown_hours))
    cfg.quiet_cooldown_hours = int(attention.get('quiet_cooldown_hours', cfg.quiet_cooldown_hours))
    cfg.macos_notification_enabled = bool(attention.get('macos_notification_enabled', False))
    cfg.backup_dir = Path(backup.get('local_dir', cfg.root.parent / 'backups')).expanduser()
    cloud = backup.get('cloud_dir')
    cfg.cloud_backup_dir = Path(cloud).expanduser() if cloud else None
    cfg.backup_keychain_service = backup.get('keychain_service', cfg.backup_keychain_service)
    cfg.ingest_host = ingest.get('host', cfg.ingest_host)
    cfg.ingest_port = int(ingest.get('port', cfg.ingest_port))
    return cfg


def keychain_get(service: str, account: str = 'phctx') -> str | None:
    """Read a generic password from the login Keychain. Returns None when absent. Never logged."""
    proc = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', service, '-a', account, '-w'],
                          capture_output=True, text=True)
    return proc.stdout.rstrip('\n') if proc.returncode == 0 and proc.stdout.strip() else None


def keychain_set(service: str, secret: str, account: str = 'phctx') -> None:
    """`-w` as the last argument makes security(1) read the secret from stdin, so it never appears in argv/ps.
    Read that way, security(1) silently truncates at 128 characters and ends at a newline, so refuse both."""
    if len(secret) > 128 or '\n' in secret:
        raise ValueError('keychain secret must be at most 128 characters without newlines')
    proc = subprocess.run(['/usr/bin/security', 'add-generic-password', '-U', '-s', service, '-a', account, '-w'],
                          input=f'{secret}\n{secret}\n', capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError('keychain_write_failed')


class _Keychain:
    """Mapping-style view of the login Keychain (account 'phctx'); tests pass a plain dict instead."""

    def get(self, service: str) -> str | None:
        return keychain_get(service)

    def __setitem__(self, service: str, secret: str) -> None:
        keychain_set(service, secret)


KEYCHAIN = _Keychain()

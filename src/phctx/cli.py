"""phctx command line. Importing/inspecting never starts services; `mcp`, `ingest-server`, `worker` do."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load
from .store import Store, StoreError


def out(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def store_of(cfg) -> Store:
    return Store(cfg.root, cfg.profile)


def cmd_doctor(cfg, args) -> int:
    import platform
    import sqlite3
    import subprocess
    checks = {'version': __version__, 'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
              'config': str(cfg.source) if cfg.source else 'defaults (no config file)', 'profile': cfg.profile,
              'root': str(cfg.root), 'tool_profile': cfg.tool_profile}
    fv = subprocess.run(['/usr/bin/fdesetup', 'status'], capture_output=True, text=True).stdout.strip()
    checks['filevault'] = fv or 'unknown'
    checks['icloud_live_root'] = 'Mobile Documents' in str(cfg.root)
    usage = shutil.disk_usage(cfg.root.parent if cfg.root.parent.exists() else Path.home())
    checks['disk_free_gb'] = round(usage.free / 1e9, 1)
    checks['allowed_download_hosts'] = cfg.allowed_download_hosts or 'EMPTY: file capture will refuse all hosts'
    from .download import FAKE_IP, system_resolver
    import ipaddress
    try:
        probe = system_resolver('files.openai.com', 443)
        fake = any(ipaddress.ip_address(ip) in FAKE_IP for ip in probe)
    except OSError:
        probe, fake = [], None
    checks['dns_fake_ip_detected'] = fake
    checks['trust_fake_ip_dns'] = cfg.trust_fake_ip_dns
    checks['model'] = {'enabled': cfg.model_enabled, 'backend': cfg.model_backend, 'model_id': cfg.model_id}
    try:
        s = store_of(cfg)
        checks['store'] = s.status()
        mode = oct(os.stat(s.root).st_mode & 0o777)
        checks['root_mode'] = mode
    except StoreError as e:
        checks['store_error'] = e.code
    problems = []
    if checks['icloud_live_root']:
        problems.append('Live data root is inside iCloud Drive; move it (live WAL DB must not cloud-sync).')
    if 'On' not in checks['filevault']:
        problems.append('FileVault is not on; do not describe the disk as encrypted.')
    if fake and not cfg.trust_fake_ip_dns:
        problems.append('DNS returns fake-IP (198.18.0.0/15) addresses; file downloads will be refused unless '
                        '[files] trust_fake_ip_dns = true (or the proxy stops faking the file host).')
    if checks['disk_free_gb'] < 5:
        problems.append('Less than 5 GB free.')
    checks['problems'] = problems
    out(checks)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog='phctx', description=__doc__)
    p.add_argument('--config', type=Path)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    sub.add_parser('init')
    sub.add_parser('status')
    m = sub.add_parser('mcp', help='serve MCP over stdio (launched by the tunnel/client)')
    m.add_argument('--profile', choices=['full', 'readonly'])
    m.add_argument('--log', type=Path)
    ia = sub.add_parser('import-apple')
    ia.add_argument('zip', type=Path)
    ia.add_argument('--dry-run', action='store_true')
    ia.add_argument('--since')
    oa = sub.add_parser('sync-oura', help="pull the owner's Oura data (backfill on first run, then a trailing window)")
    oa.add_argument('--full', action='store_true', help='re-read every collection from the first day')
    ig = sub.add_parser('ingest-server')
    ig.add_argument('--host')
    ig.add_argument('--port', type=int)
    pd = sub.add_parser('pair-device')
    pd.add_argument('--host', help='LAN address the phone will use (default: detected)')
    rv = sub.add_parser('revoke-device')
    rv.add_argument('installation_id')
    w = sub.add_parser('worker')
    w.add_argument('--once', action='store_true')
    b = sub.add_parser('backup')
    b.add_argument('--dest', type=Path)
    b.add_argument('--encrypt', action='store_true', help='also write an encrypted archive to cloud_dir')
    r = sub.add_parser('restore')
    r.add_argument('snapshot', type=Path)
    r.add_argument('--new-root', type=Path, required=True)
    r.add_argument('--passphrase-keychain')
    rp = sub.add_parser('restore-premigration', help='build a NEW root from root/migrations/pre-v*.sqlite3')
    rp.add_argument('sqlite', type=Path)
    rp.add_argument('--new-root', type=Path, required=True)
    e = sub.add_parser('export')
    e.add_argument('dest', type=Path)
    sub.add_parser('verify')
    sub.add_parser('extract-pending')
    args = p.parse_args(argv)
    try:
        cfg = load(args.config)
    except ConfigError as e:
        print(json.dumps({'error': 'config_invalid', 'message': str(e)}, ensure_ascii=False), file=sys.stderr)
        return 2
    try:
        return dispatch(cfg, args)
    except StoreError as e:
        print(json.dumps({'error': e.code, 'message': str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1


def dispatch(cfg, args) -> int:
    c = args.command
    if c == 'doctor':
        return cmd_doctor(cfg, args)
    if c == 'init':
        out(store_of(cfg).status())
        return 0
    if c == 'status':
        from .worker import status as wstatus
        out({**store_of(cfg).status(), 'worker': wstatus(cfg)})
        return 0
    if c == 'mcp':
        from .mcp_server import serve_stdio, setup_logging
        setup_logging(args.log)
        serve_stdio(cfg, args.profile)
        return 0
    if c == 'import-apple':
        from .apple_export import import_export
        out(import_export(store_of(cfg), args.zip, dry_run=args.dry_run, since=args.since))
        return 0
    if c == 'sync-oura':
        from . import oura
        try:
            out(oura.sync(store_of(cfg), tz=cfg.timezone, full=args.full))
        except oura.OuraError as e:
            out({'error': e.code})
            return 1
        return 0
    if c == 'pair-device':
        from . import ingest
        from .config import KEYCHAIN
        s = store_of(cfg)
        hosts = ingest.lan_addresses()
        host = args.host or next((h for h in hosts if h != '127.0.0.1'), '127.0.0.1')
        _, _, fp = ingest.ensure_cert(cfg.root.parent / 'ingest-tls', hosts + ([host] if host not in hosts else []),
                                      KEYCHAIN)
        code = ingest.new_pairing_code(s)
        out({'host': host, 'port': cfg.ingest_port, 'cert_sha256': fp, 'pairing_code': code, 'expires_minutes': 10,
             'pair_url': f'phctx://pair?host={host}&port={cfg.ingest_port}&cert={fp}&code={code}'})
        return 0
    if c == 'revoke-device':
        from . import ingest
        ingest.revoke(store_of(cfg), args.installation_id)
        out({'revoked': args.installation_id})
        return 0
    if c == 'ingest-server':
        import logging
        from . import ingest
        from .config import KEYCHAIN
        from .mcp_server import setup_logging
        setup_logging(None)
        s = store_of(cfg)
        cert, key, fp = ingest.ensure_cert(cfg.root.parent / 'ingest-tls', ingest.lan_addresses(), KEYCHAIN)
        srv = ingest.make_server(s, args.host or cfg.ingest_host, args.port or cfg.ingest_port, cert, key)
        logging.getLogger('phctx.ingest').info('listening port=%s cert_sha256=%s', srv.server_address[1], fp)
        srv.serve_forever()
        return 0
    if c == 'worker':
        from .worker import run_forever, run_once
        if args.once:
            out(run_once(cfg))
        else:
            run_forever(cfg)
        return 0
    if c == 'backup':
        from .backup import backup
        out(backup(cfg, args.dest, encrypt=args.encrypt))
        return 0
    if c == 'restore':
        from .backup import restore
        out(restore(cfg, args.snapshot, args.new_root, args.passphrase_keychain))
        return 0
    if c == 'restore-premigration':
        from .backup import restore_premigration
        out(restore_premigration(cfg, args.sqlite, args.new_root))
        return 0
    if c == 'export':
        out(store_of(cfg).export(args.dest))
        return 0
    if c == 'extract-pending':
        from .extract import extract_pending
        out(extract_pending(store_of(cfg)))
        return 0
    if c == 'verify':
        import subprocess
        root = Path(__file__).resolve().parents[2]
        return subprocess.call([sys.executable, '-m', 'unittest', 'discover', '-s', str(root / 'tests')],
                               env={**os.environ, 'PYTHONPATH': str(root / 'src')})
    return 2


if __name__ == '__main__':
    raise SystemExit(main())

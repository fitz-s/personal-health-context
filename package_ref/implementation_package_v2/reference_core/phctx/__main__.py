"""Offline reference CLI. No model, network, account access or daemon installation."""
import argparse
import json
import sys
import tempfile
from pathlib import Path
from .store import Store, StoreError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    sub.add_parser('status')
    sub.add_parser('demo')
    backup = sub.add_parser('backup')
    backup.add_argument('destination', type=Path)
    args = parser.parse_args()
    if args.command == 'demo':
        with tempfile.TemporaryDirectory(prefix='phctx-synthetic-') as tmp:
            store = Store(tmp)
            receipt = store.put_record(request_id='demo-event', kind='event',
                text='SYNTHETIC: Dinner at 20:30; portion size unknown.',
                occurred_at='2026-09-22T20:30:00-05:00', payload={'synthetic': True})
            recovered = Store(tmp).search(query='Dinner')
            silent = store.queue_insight(request_id='demo-silence', candidate={'decision': 'silence'})
            print(json.dumps({'receipt': receipt, 'fresh_instance_recovered': len(recovered['records']) == 1,
                              'ordinary_day': silent, 'real_accounts_used': False}, indent=2))
        return 0
    if args.root is None:
        parser.error('--root is required outside the temporary synthetic demo')
    try:
        store = Store(args.root)
        value = store.backup(args.destination) if args.command == 'backup' else store.bootstrap()
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except StoreError as e:
        print(json.dumps({'error': e.code, 'message': str(e)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

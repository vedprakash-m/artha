#!/usr/bin/env python3
"""Preview or apply an exact, verified local credential migration; never contacts providers."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from lib.credential_migration import MigrationError, apply, plan, recover, recovery_plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parent.parent / '.tokens')
    parser.add_argument('--local', type=Path,
                        default=Path(os.environ.get('ARTHA_LOCAL_DIR', str(Path.home() / '.artha-local'))))
    parser.add_argument('--work', action='store_true', help='explicitly select the separate Work credential scope')
    parser.add_argument('--apply', metavar='REVIEW_HASH', help='apply precisely the reviewed file generation')
    parser.add_argument('--recover', type=Path, help='preview/import an encrypted recovery into OS records only')
    args = parser.parse_args()
    try:
        if args.recover:
            result = (recover(args.recover, args.local, args.apply, work=args.work) if args.apply
                      else recovery_plan(args.recover, work=args.work))
        else:
            result = (apply(args.source, args.local, args.apply, work=args.work) if args.apply
                      else plan(args.source, work=args.work))
        print(json.dumps(result, indent=2))
        return 0
    except MigrationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError):
        print('Credential migration could not be confirmed. Originals or encrypted recovery are retained; '
              'inspect the credential store and review a fresh plan.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

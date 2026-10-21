"""Expose loaded source identity so a healthy old process cannot pass deployment QA."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def source_revision():
    digest = hashlib.sha256()
    for folder in ('backend', 'gentactix', 'plugins'):
        for path in sorted((ROOT / folder).rglob('*')):
            if path.is_file() and path.suffix in ('.py', '.json', '.yml', '.mjs'):
                digest.update(str(path.relative_to(ROOT)).encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


LOADED_REVISION = source_revision()
STARTED_AT = datetime.now(timezone.utc).isoformat()


def status():
    disk = source_revision()
    return {'loaded_revision': LOADED_REVISION, 'disk_revision': disk,
            'restart_required': disk != LOADED_REVISION, 'pid': os.getpid(), 'started_at': STARTED_AT}

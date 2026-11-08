"""Extract TopCV listing cards using configuration from the repository .env file."""

import argparse
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from dotenv import dotenv_values

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collection.jobs.resume_controller import run_auto_collection

ROOT = Path(__file__).resolve().parents[3]


def load_config(root=ROOT):
    """Read .env with system-environment overrides, then validate before opening a run."""
    values = {**dotenv_values(Path(root) / '.env', encoding='utf-8-sig'), **os.environ}

    def read(name, convert=str, default=None):
        value = values.get(name)
        if value is None or not value.strip():
            return default
        try:
            return convert(value.strip())
        except ValueError as exc:
            raise ValueError(f'{name} has an invalid value: {value!r}') from exc

    headless = read('JOBS_HEADLESS', default='false').lower()
    if headless not in {'true', 'false'}:
        raise ValueError('JOBS_HEADLESS must be true or false')
    args = SimpleNamespace(
        max_jobs=read('JOBS_MAX_JOBS', int),
        max_pages=read('JOBS_END', int),
        start_page=read('JOBS_START', int),
        resume=read('JOBS_RESUME', Path),
        output_directory=read('JOBS_OUTPUT_DIRECTORY', Path),
        delay=read('JOBS_DELAY', float, 15),
        timeout=read('JOBS_TIMEOUT', float, 30),
        resume_cooldown=read('JOBS_RESUME_COOLDOWN', float, 30),
        channel=read('JOBS_CHANNEL', default='chrome'),
        headless=headless == 'true',
        jitter=5, max_retries=2, backoff=15, max_auto_wait=60,
    )
    if args.resume is not None and args.output_directory is not None:
        raise ValueError('Set only one of JOBS_RESUME and JOBS_OUTPUT_DIRECTORY')
    if args.channel not in {'chromium', 'chrome', 'msedge'}:
        raise ValueError('JOBS_CHANNEL must be chromium, chrome or msedge')
    for name, value in [('JOBS_START', args.start_page), ('JOBS_END', args.max_pages),
                        ('JOBS_MAX_JOBS', args.max_jobs)]:
        if value is not None and value < 1:
            raise ValueError(f'{name} must be positive')
    if args.start_page is not None and args.max_pages is not None and args.start_page > args.max_pages:
        raise ValueError('JOBS_START must be no greater than JOBS_END')
    if not all(math.isfinite(value) for value in [args.delay, args.timeout, args.resume_cooldown]):
        raise ValueError('JOBS_DELAY, JOBS_TIMEOUT and JOBS_RESUME_COOLDOWN must be finite')
    if args.delay < 3:
        raise ValueError('JOBS_DELAY must be >= 3')
    if args.timeout <= 0:
        raise ValueError('JOBS_TIMEOUT must be positive')
    if not 30 <= args.resume_cooldown <= 1800:
        raise ValueError('JOBS_RESUME_COOLDOWN must be 30..1800')
    return args


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        return run_auto_collection(load_config(ROOT), ROOT)
    except (ValueError, OSError, KeyError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())

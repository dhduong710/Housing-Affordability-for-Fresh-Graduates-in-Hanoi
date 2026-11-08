"""Checkpointed restarts with a fixed cooldown until target or source end."""

import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

from collection.jobs.collection_runner import open_collection, run_search_collection
from collection.jobs.utils.dataset_lock import dataset_lock
from collection.jobs.utils.rate_limit import RequestPacer, retry_after_seconds


def utc_clock():
    return datetime.now(UTC)


def retryable_stop(state):
    if state.manifest['result'] != 'stopped':
        return False
    reason = state.manifest.get('stop_reason') or ''
    return reason.startswith(('access_blocked: HTTP 403', 'challenge page:',
                              'retry_limit:', 'cooldown_too_long:'))


def required_deadline(state, now):
    """Preserve server cooldowns, including Retry-After on blocked responses."""
    deadline = now
    if value := state.manifest.get('not_before'):
        deadline = max(deadline, datetime.fromisoformat(value))
    requests = state.manifest.get('requests', [])
    if requests:
        record = requests[-1]
        at = datetime.fromisoformat(record['at'])
        seconds = retry_after_seconds(record.get('response_headers', {}).get('retry-after'), at)
        if seconds is not None:
            try:
                deadline = max(deadline, at + timedelta(seconds=seconds))
            except OverflowError:
                deadline = datetime.max.replace(tzinfo=UTC)
    return deadline


def emit(state, event, **extra):
    print(json.dumps({'event': event, 'run_directory': str(state.folder),
                      'records_collected': len(state.rows), **extra}), flush=True)


def run_auto_collection(args, root, *, clock=None, sleep=None):
    destination = args.resume or getattr(args, 'output_directory', None)
    guard = dataset_lock((Path(root) / destination).resolve()) if destination is not None else nullcontext()
    with guard:
        return _run_auto_collection(args, root, clock=clock, sleep=sleep)


def _run_auto_collection(args, root, *, clock=None, sleep=None):
    clock, sleep = clock or utc_clock, sleep or RequestPacer.sleep
    state = open_collection(args, root)
    control = state.manifest.setdefault('auto_resume', {'resumes_used': 0})
    control.update(target_page=args.max_pages,
                   base_cooldown_seconds=args.resume_cooldown, cooldown_mode='fixed')
    state.save()
    emit(state, 'auto_started', target_page=args.max_pages,
         resumes_used=control['resumes_used'])
    try:
        while True:
            # A completed target must not open another browser.
            if state.next_task(args.max_pages) is None:
                result, reason = ('limited', 'max_pages') if state.queue else ('completed', None)
                control.update(status='target_reached' if state.queue else 'source_exhausted',
                               next_resume_at=None)
                state.finish(result, reason)
                emit(state, 'auto_finished', status=control['status'])
                return 0

            pending = control.get('next_resume_at')
            if not pending and state.manifest['result'] in {'stopped', 'failed'} and not retryable_stop(state):
                control['status'] = 'non_retryable_error'
                state.save()
                emit(state, 'auto_finished', status=control['status'],
                     reason=state.manifest.get('stop_reason'))
                return 1
            stopped = retryable_stop(state)
            server_deadline = required_deadline(state, clock())
            must_wait = server_deadline > clock()
            if pending or stopped or must_wait:
                if pending:
                    deadline = max(datetime.fromisoformat(pending), server_deadline)
                else:
                    seconds = args.resume_cooldown
                    deadline = max(clock() + timedelta(seconds=seconds), server_deadline)
                control.update(status='waiting', next_resume_at=deadline.isoformat())
                state.manifest['not_before'] = deadline.isoformat()
                state.save()  # Persist the deadline BEFORE waiting; Ctrl+C cannot erase it.
                emit(state, 'auto_cooldown', resume_at=control['next_resume_at'],
                     next_page=state.next_task(args.max_pages)['page_number'])
                while (remaining := (deadline - clock()).total_seconds()) > 0:
                    sleep(min(30, remaining))
                control.update(resumes_used=control['resumes_used'] + 1,
                               next_resume_at=None, status='running')
                # Expired cooldowns are cleared only after the persisted deadline has elapsed.
                state.manifest.pop('not_before', None)
                state.save()  # Record the restart before launching, even if launch fails.

            code = run_search_collection(args, root, state=state)
            if code == 0:
                control.update(status=('target_reached' if state.manifest.get('stop_reason') == 'max_pages'
                                       else 'source_exhausted' if not state.queue else 'limited'),
                               next_resume_at=None)
                state.save()
                emit(state, 'auto_finished', status=control['status'])
                return 0
            if state.manifest['result'] == 'interrupted':
                control['status'] = 'interrupted'
                state.save()
                emit(state, 'auto_interrupted', resume_at=None)
                return 130
            if not retryable_stop(state):
                control['status'] = 'non_retryable_error'
                state.save()
                emit(state, 'auto_finished', status=control['status'],
                     reason=state.manifest.get('stop_reason'))
                return 1
    except KeyboardInterrupt:
        # Keep next_resume_at intact so restarting this command cannot shorten the wait.
        control['status'] = 'waiting' if control.get('next_resume_at') else 'interrupted'
        state.finish('interrupted', 'Interrupted by user; automatic resume checkpoint preserved')
        emit(state, 'auto_interrupted', resume_at=control.get('next_resume_at'))
        return 130

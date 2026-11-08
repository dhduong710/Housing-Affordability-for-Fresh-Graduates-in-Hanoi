"""List-only orchestration with durable progress and configurable coverage limits."""

import json
from datetime import datetime, timezone
from pathlib import Path

from collection.jobs.listing_client import ListingClient, SourceStopped
from collection.jobs.sources.pagination import page_url
from collection.jobs.utils.collection_state import CollectionState, utc_now


def collect(state, client, max_jobs=None, max_pages=None):
    """Extract all listing cards before clicking Next."""
    while True:
        task = state.next_task(max_pages)
        if task is None:
            state.flush()
            return ('limited', 'max_pages') if state.queue else ('completed', None)
        if max_jobs is not None and len(state.rows) >= max_jobs:
            state.flush()
            return ('limited', 'max_jobs')
        if task.get('initialized'):
            state.validate_task(task)  # Resume the checkpoint card order without refetching.
        else:
            html, timestamp = client.load(task)
            state.prepare(task, html, timestamp)
        while task['offset'] < len(task['cards']):
            if state.store_next_job(task, max_jobs) is None:
                if task['offset'] < len(task['cards']):
                    state.flush()
                    return ('limited', 'max_jobs')
                break
        state.finish_page(task)
        print(json.dumps({'event': 'page_complete', 'page': task['page_number'],
                          'records_collected': len(state.rows), 'pending_tasks': len(state.queue)}), flush=True)


def open_collection(args, root):
    """Load the existing run or create one; never start a browser here."""
    root = Path(root).resolve()
    if args.resume:
        state = CollectionState.resume(root, (root / args.resume).resolve())
        if (start_page := getattr(args, 'start_page', None)) is not None:
            state.begin_pass(start_page, args.max_pages)
        elif getattr(args, 'max_pages', None) is None:
            args.max_pages = state.manifest.get('auto_resume', {}).get('target_page')
        return state
    destination = getattr(args, 'output_directory', None)
    state = CollectionState.create(root, [{'name': 'all_jobs',
        'url': page_url('https://www.topcv.vn/tim-viec-lam-moi-nhat', 1)}],
        {'collection_scope': 'unfiltered_search_card', 'pagination_mode': 'click_next'},
        output_directory=destination)
    if (start_page := getattr(args, 'start_page', None)) is not None:
        state.queue[0].update(url=page_url(state.queue[0]['url'], start_page), page_number=start_page)
        state.save()
    return state


def run_search_collection(args, root, state=None):
    state = state if state is not None else open_collection(args, root)
    not_before = state.manifest.get('not_before')
    if not_before and datetime.fromisoformat(not_before) > datetime.now(timezone.utc):
        raise ValueError(f'Retry-After has not elapsed; resume after {not_before}')
    state.manifest['result'] = 'running'
    state.manifest.pop('stop_reason', None)
    state.manifest.pop('finished_at', None)
    state.manifest['sessions'].append({'started_at': utc_now(), 'max_jobs': args.max_jobs,
                                     'max_pages': args.max_pages, 'channel': args.channel,
        'request_policy': {'delay': args.delay, 'jitter': args.jitter, 'max_retries': args.max_retries,
                           'backoff': args.backoff, 'max_auto_wait': args.max_auto_wait, 'timeout': args.timeout},
        'pagination_mode': 'click_next', 'launch_mode': 'cdp', 'headless': args.headless})
    state.save()
    client = ListingClient(state, args.channel, args.headless, args.delay,
                           jitter=args.jitter, max_retries=args.max_retries,
                           backoff=args.backoff, max_auto_wait=args.max_auto_wait, timeout=args.timeout)
    try:
        result, reason = collect(state, client, args.max_jobs, args.max_pages)
    except SourceStopped as exc:
        result, reason = 'stopped', str(exc)
        state.data['errors'].append({'at': utc_now(), 'stage': 'source', 'error': reason})
    except KeyboardInterrupt:
        result, reason = 'interrupted', 'Interrupted by user; completed pages are checkpointed'
    except Exception as exc:
        result, reason = 'failed', str(exc)
        state.data['errors'].append({'at': utc_now(), 'stage': 'run', 'error': reason})
    finally:
        try:
            client.close()
        except Exception as exc:
            state.manifest.setdefault('warnings', []).append({'stage': 'browser_close', 'error': str(exc)})
    state.finish(result, reason)
    print(json.dumps({'result': result, 'reason': reason, 'records_collected': len(state.rows),
                      'run_directory': str(state.folder), 'pending_tasks': len(state.queue)}, ensure_ascii=False), flush=True)
    return 0 if result in {'completed', 'limited'} else 1

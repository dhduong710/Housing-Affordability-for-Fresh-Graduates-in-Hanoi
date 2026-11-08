"""Durable, resumable search collection. checkpoint.json is authoritative."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timezone
from pathlib import Path

from collection.common.storage import path_reference, write_csv, write_json
from collection.jobs.sources.pagination import next_numeric_page, page_url
from collection.jobs.sources.topcv_parser import (
    FIELDS,
    SCHEMA_VERSION,
    parse_search,
)

CHECKPOINT_BATCH_SIZE = 10


def utc_now():
    return datetime.now(UTC).isoformat()


def parser_fingerprint():
    base = Path(__file__).resolve().parents[1] / 'sources'
    return hashlib.sha256((base / 'topcv_parser.py').read_bytes()).hexdigest()


class CollectionState:
    def __init__(self, root, folder, data):
        self.root, self.folder, self.data = Path(root).resolve(), Path(folder).resolve(), data
        self.manifest = data['manifest']
        self.rows, self.queue = data['rows'], data['queue']
        # Rebuild observed and collected IDs on resume. In-memory rows may lead
        # the durable checkpoint by fewer than CHECKPOINT_BATCH_SIZE cards.
        self.seen_ids = {identifier for page in self.manifest['pages'] for identifier in page['job_ids']}
        self.stored_ids = {row['job_id'] for row in self.rows}
        self._cards_since_checkpoint = 0
        self._csv_dirty = bool(self.rows)  # Resume republishes CSV from the checkpoint.

    @classmethod
    def create(cls, root, profiles, config, output_directory=None):
        root = Path(root).resolve()
        run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ_topcv_browser')
        folder = Path(output_directory) if output_directory is not None else root / 'data' / 'raw' / 'jobs' / run_id
        folder = (root / folder).resolve()
        folder.mkdir(parents=True, exist_ok=False)
        manifest = {'run_id': run_id, 'started_at': utc_now(), 'schema_version': SCHEMA_VERSION,
                    'source': 'topcv', 'seed_url': profiles[0]['url'], 'profiles': profiles,
                    'config': config,
                    'parser_fingerprint': parser_fingerprint(), 'extraction_scope': 'search_card',
                    'parser_method': 'search_dom', 'result': 'running', 'pages': [], 'requests': [],
                    'sessions': [], 'data_directory': path_reference(folder, root)}
        queue = [{'profile_index': index, 'url': profile['url'], 'page_number': 1, 'offset': 0}
                 for index, profile in enumerate(profiles)]
        state = cls(root, folder, {'checkpoint_version': 5, 'manifest': manifest,
                                  'rows': [], 'queue': queue, 'errors': []})
        state.save()
        return state

    @classmethod
    def resume(cls, root, folder):
        data = json.loads((Path(folder) / 'checkpoint.json').read_text(encoding='utf-8'))
        manifest = data['manifest']
        if data.get('checkpoint_version') != 5 or manifest.get('schema_version') != SCHEMA_VERSION:
            raise ValueError('Checkpoint/schema version mismatch; start a new run')
        if manifest.get('parser_fingerprint') != parser_fingerprint():
            raise ValueError('Parser changed; start a new run instead of mixing extraction rules')
        ids = [row['job_id'] for row in data['rows']]
        if len(set(ids)) != len(ids) or any(set(row) != set(FIELDS) for row in data['rows']):
            raise ValueError('Invalid checkpoint rows or duplicate IDs')
        state = cls(root, folder, data)
        boundary = manifest.get('active_pass_start_index', 0)
        if not isinstance(boundary, int) or isinstance(boundary, bool) or not 0 <= boundary <= len(manifest['pages']):
            raise ValueError('Invalid collection pass boundary')
        tasks = state.queue + [task for previous in manifest.get('collection_passes', [])
                               for task in previous.get('superseded_queue', [])]
        for task in tasks:
            if task.get('initialized'):
                state.validate_task(task)
        manifest['data_directory'] = path_reference(state.folder, state.root)
        # Restore CSV from the validated checkpoint.
        state.save(export_csv=True)
        return state

    def begin_pass(self, start_page, target_page):
        """Start a new pass, archiving pending work and keeping all rows and IDs."""
        if len(self.manifest['profiles']) != 1:
            raise ValueError('A repeat pass requires a single listing profile')
        if (not isinstance(start_page, int) or isinstance(start_page, bool)
                or start_page < 1 or target_page is not None and
                (not isinstance(target_page, int) or isinstance(target_page, bool) or start_page > target_page)):
            raise ValueError('Start page must be positive and no greater than end page')
        boundary = len(self.manifest['pages'])
        self.manifest.setdefault('collection_passes', []).append({
            'started_at': utc_now(), 'start_page': start_page, 'target_page': target_page,
            'page_start_index': boundary, 'records_at_start': len(self.rows),
            'previous_result': self.manifest['result'], 'superseded_queue': deepcopy(self.queue)})
        self.manifest['active_pass_start_index'] = boundary
        self.queue[:] = [{'profile_index': 0,
            'url': page_url(self.manifest['profiles'][0]['url'], start_page),
            'page_number': start_page, 'offset': 0}]
        self.manifest['result'] = 'running'
        self.manifest.pop('stop_reason', None)
        self.manifest.pop('finished_at', None)
        self.manifest.setdefault('auto_resume', {}).update(status='ready', resumes_used=0, target_page=target_page)
        self.save()

    def next_task(self, max_pages):
        return next((task for task in self.queue if max_pages is None or task['page_number'] <= max_pages), None)

    @staticmethod
    def page_checksum(task, page):
        payload = {'cards': task['cards'], 'url': task['url'],
                   'page_number': task['page_number'], 'collected_at': task['collected_at'],
                   'next_url': page['next_url'],
                   'pagination': page['pagination']}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

    def validate_task(self, task):
        page = self.manifest['pages'][task['page_index']]
        if self.page_checksum(task, page) != task.get('payload_sha256'):
            raise ValueError('Saved page payload checksum mismatch')
        cards, offset = task['cards'], task['offset']
        if (not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= len(cards)
                or any(set(card) != set(FIELDS) for card in cards)
                or page['url'] != task['url'] or page['page_number'] != task['page_number']
                or page['card_count'] != len(cards)
                or set(page['job_ids']) != {card['job_id'] for card in cards}):
            raise ValueError('Invalid saved page cards or offset')

    def prepare(self, task, html, collected_at):
        """Commit the original card order and pagination before storing rows."""
        cards = parse_search(html, task['url'], collected_at)
        following, pagination = next_numeric_page(html, task['url'], task['page_number'])
        if not cards and not pagination['explicit_empty']:
            raise ValueError('No job cards parsed and no explicit empty result; inspect the source or parser')
        profile = self.manifest['profiles'][task['profile_index']]
        ids = {row['job_id'] for row in cards}
        new_ids = ids - self.seen_ids
        task.update(initialized=True, cards=cards,
                    collected_at=collected_at, page_index=len(self.manifest['pages']))
        self.manifest['pages'].append({'profile': profile['name'], 'page_number': task['page_number'],
            'url': task['url'], 'collected_at': collected_at,
            'job_ids': sorted(ids), 'card_count': len(cards), 'new_ids_vs_previous_pages': len(new_ids),
            'rows_kept': 0, 'duplicate_ids_skipped': 0,
            'processed_cards': 0, 'next_url': following, 'pagination': pagination})
        task['payload_sha256'] = self.page_checksum(task, self.manifest['pages'][task['page_index']])
        self.seen_ids.update(ids)
        self.save()

    def store_next_job(self, task, max_jobs):
        page = self.manifest['pages'][task['page_index']]
        while task['offset'] < len(task['cards']):
            card = task['cards'][task['offset']]
            if card['job_id'] in self.stored_ids:
                task['offset'] += 1
                page['processed_cards'] = task['offset']
                page['duplicate_ids_skipped'] += 1
                self._card_processed()
                continue
            if max_jobs is not None and len(self.rows) >= max_jobs:
                return None
            row = dict(card)
            self.rows.append(row)
            self.stored_ids.add(row['job_id'])
            self._csv_dirty = True
            page['rows_kept'] += 1
            task['offset'] += 1
            page['processed_cards'] = task['offset']
            self._card_processed()
            return row
        return None

    def _card_processed(self):
        self._cards_since_checkpoint += 1
        if self._cards_since_checkpoint >= CHECKPOINT_BATCH_SIZE:
            self.save()

    def flush(self):
        """Commit a partial batch, then bring CSV up to the committed rows."""
        if self._cards_since_checkpoint:
            self.save()
        self.publish()

    def finish_page(self, task):
        if task['offset'] < len(task['cards']):
            raise ValueError('Cannot advance before every job on this page is handled')
        page = self.manifest['pages'][task['page_index']]
        self.validate_task(task)
        following = page['next_url']
        if following:
            boundary = self.manifest.get('active_pass_start_index', 0)
            known = {pending['url'] for pending in self.queue} | {p['url'] for p in self.manifest['pages'][boundary:]}
            if following in known:
                raise ValueError('Next-page URL creates a pagination cycle')
        self.queue.remove(task)
        if following:
            self.queue.append({'profile_index': task['profile_index'], 'url': following,
                               'page_number': task['page_number'] + 1, 'offset': 0})
        self.save(export_csv=True)

    def save(self, *, export_csv=False):
        self.manifest.update(updated_at=utc_now(), records_collected=len(self.rows),
            errors_count=len(self.data['errors']), pending_tasks=len(self.queue),
            missing_counts={key: sum(row[key] in ('', None) for row in self.rows) for key in FIELDS})
        # Commit rows and offset together; CSV can be recovered from this state.
        write_json(self.folder / 'checkpoint.json', self.data)
        self._cards_since_checkpoint = 0
        if export_csv:
            self.publish()

    def publish(self):
        if self.rows and self._csv_dirty:
            write_csv(self.folder / 'jobs_topcv.csv', self.rows, FIELDS)
            self._csv_dirty = False

    def finish(self, result, reason=None):
        self.manifest['result'] = result
        self.manifest['finished_at'] = utc_now()
        if reason:
            self.manifest['stop_reason'] = reason
        else:
            self.manifest.pop('stop_reason', None)
        if self.manifest['sessions']:
            self.manifest['sessions'][-1].update(finished_at=utc_now(), result=result, reason=reason)
        self.save(export_csv=True)

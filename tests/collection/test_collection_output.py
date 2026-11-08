"""Fresh output selection, overwrite protection and subsequent resume."""

import json
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from collection.jobs.collection_runner import collect, open_collection
from collection.jobs.crawl_jobs import load_config, main
from collection.jobs.resume_controller import run_auto_collection
from collection.jobs.utils.dataset_lock import dataset_lock
from collection.jobs.utils.collection_state import CollectionState
from tests.collection.fixtures import FakeClient


class EnvironmentConfigurationTests(unittest.TestCase):
    def test_missing_or_blank_settings_use_defaults_and_environment_overrides_file(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            root = Path(temporary)
            args = load_config(root)
            self.assertEqual((args.delay, args.timeout, args.resume_cooldown), (15, 30, 30))
            self.assertEqual((args.channel, args.headless), ('chrome', False))
            self.assertEqual((args.jitter, args.max_retries, args.backoff, args.max_auto_wait),
                             (5, 2, 15, 60))
            self.assertEqual((args.max_pages, args.start_page, args.max_jobs,
                              args.resume, args.output_directory), (None,) * 5)
            (root / '.env').write_text('JOBS_END=10\nJOBS_HEADLESS=false\nJOBS_DELAY=\n',
                                      encoding='utf-8-sig')
            with patch.dict(os.environ, {'JOBS_END': '20', 'JOBS_HEADLESS': 'true'}):
                args = load_config(root)
            self.assertEqual((args.max_pages, args.headless, args.delay), (20, True, 15))

    def test_entrypoint_reads_env_for_fresh_run_resume_and_repeat_pass(self):
        class Client(FakeClient):
            def close(self):
                pass

        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            root = Path(temporary).resolve()
            env = root / '.env'
            folder = root / 'data/raw/jobs/team run'
            clients = []

            def browser(state, *args, **kwargs):
                client = Client(state)
                clients.append(client)
                return client

            with patch('collection.jobs.crawl_jobs.ROOT', root), \
                    patch('sys.argv', ['crawl_jobs.py']), \
                    patch('collection.jobs.collection_runner.ListingClient', side_effect=browser):
                env.write_text('JOBS_END=2\nJOBS_MAX_JOBS=2\nJOBS_HEADLESS=false\n'
                               'JOBS_DELAY=3\nJOBS_TIMEOUT=90\nJOBS_RESUME_COOLDOWN=60\n'
                               'JOBS_OUTPUT_DIRECTORY="data/raw/jobs/team run"\n', encoding='utf-8')
                self.assertEqual(main(), 0)
                saved = json.loads((folder / 'checkpoint.json').read_text())
                self.assertEqual(saved['queue'][0]['offset'], 2)
                session = saved['manifest']['sessions'][-1]
                self.assertFalse(session['headless'])
                self.assertEqual(session['request_policy']['timeout'], 90)
                self.assertEqual(saved['manifest']['auto_resume']['base_cooldown_seconds'], 60)

                env.write_text('JOBS_RESUME="data/raw/jobs/team run"\nJOBS_OUTPUT_DIRECTORY=\n'
                               'JOBS_MAX_JOBS=\nJOBS_END=\n', encoding='utf-8')
                self.assertEqual(main(), 0)
                saved = json.loads((folder / 'checkpoint.json').read_text())
                self.assertEqual([row['job_id'] for row in saved['rows']], ['1', '2', '3', '4'])
                self.assertEqual(clients[-1].calls, [('list', 2)])
                self.assertEqual(saved['manifest']['auto_resume']['target_page'], 2)

                with env.open('a', encoding='utf-8') as stream:
                    stream.write('JOBS_START=1\nJOBS_END=1\n')
                self.assertEqual(main(), 0)
                saved = json.loads((folder / 'checkpoint.json').read_text())
                self.assertEqual(len(saved['rows']), 4)
                self.assertEqual(clients[-1].calls, [('list', 1)])
                self.assertEqual(saved['manifest']['collection_passes'][-1]['start_page'], 1)

    def test_invalid_env_stops_before_opening_a_run(self):
        cases = [
            'JOBS_HEADLESS=maybe', 'JOBS_CHANNEL=invalid', 'JOBS_DELAY=nan',
            'JOBS_DELAY=2', 'JOBS_TIMEOUT=0', 'JOBS_TIMEOUT=inf',
            'JOBS_RESUME_COOLDOWN=20', 'JOBS_RESUME_COOLDOWN=1801',
            'JOBS_END=abc', 'JOBS_START=0', 'JOBS_END=-1', 'JOBS_MAX_JOBS=0',
            'JOBS_START=3\nJOBS_END=2',
            'JOBS_RESUME=existing\nJOBS_OUTPUT_DIRECTORY=new',
        ]
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            root = Path(temporary)
            with patch('collection.jobs.crawl_jobs.ROOT', root), \
                    patch('sys.argv', ['crawl_jobs.py']), \
                    patch('sys.stderr', new_callable=io.StringIO), \
                    patch('collection.jobs.crawl_jobs.run_auto_collection') as run:
                for text in cases:
                    with self.subTest(config=text):
                        (root / '.env').write_text(text, encoding='utf-8')
                        with self.assertRaises(SystemExit) as stopped:
                            main()
                        self.assertEqual(stopped.exception.code, 2)
                run.assert_not_called()
            self.assertFalse((root / 'data').exists())


class CollectionOutputTests(unittest.TestCase):
    def test_custom_output_starts_at_page_one_and_can_resume_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            args = SimpleNamespace(
                resume=None, output_directory=Path("batches/new_run")
            )
            state = open_collection(args, root)
            self.assertEqual(state.folder, root / "batches/new_run")
            self.assertEqual(state.queue[0]["page_number"], 1)
            self.assertEqual(state.seen_ids, set())
            self.assertEqual(state.stored_ids, set())
            self.assertEqual(
                collect(state, FakeClient(state), max_pages=1), ("limited", "max_pages")
            )
            error = {"stage": "source", "error": "fixture interruption"}
            state.data["errors"].append(error)
            state.save()
            restored = open_collection(SimpleNamespace(resume=state.folder), root)
            self.assertEqual(
                collect(restored, FakeClient(restored)), ("completed", None)
            )
            self.assertEqual(
                [row["job_id"] for row in restored.rows], ["1", "2", "3", "4"]
            )
            self.assertFalse((root / "reports").exists())
            checkpoint = json.loads((restored.folder / "checkpoint.json").read_text())
            manifest = checkpoint["manifest"]
            self.assertEqual(checkpoint["checkpoint_version"], 5)
            self.assertEqual(
                {p.name for p in restored.folder.iterdir()},
                {"checkpoint.json", "jobs_topcv.csv"},
            )
            self.assertEqual(manifest["records_collected"], 4)
            self.assertEqual(manifest["data_directory"], "batches/new_run")
            self.assertEqual(manifest["errors_count"], 1)
            self.assertEqual(checkpoint["errors"], [error])
            self.assertEqual(restored.data["errors"], [error])
            before = (state.folder / "checkpoint.json").read_bytes()
            with self.assertRaises(FileExistsError):
                open_collection(args, root)
            self.assertEqual((state.folder / "checkpoint.json").read_bytes(), before)

    def test_default_runs_are_isolated_and_never_reuse_previous_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = SimpleNamespace(resume=None, output_directory=None)
            first = open_collection(args, temporary)
            collect(first, FakeClient(first), max_jobs=1)
            before = (first.folder / "checkpoint.json").read_bytes()
            second = open_collection(args, temporary)
            self.assertNotEqual(first.folder, second.folder)
            self.assertFalse((Path(temporary) / "reports").exists())
            self.assertEqual(
                second.folder.parent, Path(temporary).resolve() / "data/raw/jobs"
            )
            self.assertEqual(second.rows, [])
            self.assertEqual(second.stored_ids, set())
            self.assertEqual((first.folder / "checkpoint.json").read_bytes(), before)

            moved_root = Path(temporary) / "another checkout"
            moved_folder = moved_root / "data/raw/jobs" / first.folder.name
            shutil.copytree(first.folder, moved_folder)
            restored = CollectionState.resume(moved_root, moved_folder)
            self.assertEqual(restored.manifest["data_directory"],
                             "data/raw/jobs/" + first.folder.name)
            self.assertEqual(restored.stored_ids, first.stored_ids)
            client = FakeClient(restored)
            self.assertEqual(collect(restored, client), ("completed", None))
            self.assertEqual(client.calls, [("list", 2)])

    def test_resume_inherits_target_and_saved_card_offset(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = SimpleNamespace(resume=None, output_directory=Path("batch"))
            state = open_collection(args, temporary)
            collect(state, FakeClient(state), max_jobs=1)
            state.manifest["auto_resume"] = {"target_page": 2, "resumes_used": 0}
            state.save()
            args = SimpleNamespace(resume=Path("batch"), max_pages=None)
            restored = open_collection(args, temporary)
            self.assertEqual(args.max_pages, 2)
            self.assertEqual(restored.queue[0]["offset"], 1)
            client = FakeClient(restored)
            collect(restored, client, max_pages=args.max_pages)
            self.assertEqual(client.calls, [("list", 2)])
            self.assertEqual(
                [row["job_id"] for row in restored.rows], ["1", "2", "3", "4"]
            )

    def test_dataset_lock_prevents_checkpoint_changes_and_other_processes(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = open_collection(SimpleNamespace(resume=None), temporary)
            before = (state.folder / "checkpoint.json").read_bytes()
            args = SimpleNamespace(resume=state.folder)
            source = str(Path(__file__).resolve().parents[2] / "src")
            code = (
                "import sys; sys.path.insert(0,sys.argv[1]); "
                "from collection.jobs.utils.dataset_lock import dataset_lock\n"
                "try:\n with dataset_lock(sys.argv[2]): pass\n"
                "except RuntimeError:\n sys.exit(0)\n"
                "sys.exit(2)"
            )
            with dataset_lock(state.folder):
                child = subprocess.run(
                    [sys.executable, "-c", code, source, str(state.folder)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(child.returncode, 0, child.stderr)
                with patch(
                    "collection.jobs.resume_controller.open_collection"
                ) as opened:
                    with self.assertRaisesRegex(RuntimeError, "already locked"):
                        run_auto_collection(args, temporary)
                    opened.assert_not_called()
            self.assertEqual((state.folder / "checkpoint.json").read_bytes(), before)
            with dataset_lock(state.folder):
                pass

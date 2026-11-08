"""Listing traversal, durable resume and repeat-pass behavior."""

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from collection.common.storage import replace_with_retry, write_csv, write_json
from collection.jobs.collection_runner import collect, open_collection, run_search_collection
from collection.jobs.utils.collection_state import CollectionState
from tests.collection.fixtures import (
    AT,
    NEXT,
    PROFILE,
    URL,
    FakeClient,
    UpdatedListings,
    html_page,
)


class SearchResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = CollectionState.create(self.root, [PROFILE], {})

    def tearDown(self):
        self.temporary.cleanup()

    def test_all_cards_are_saved_before_next_page_without_detail_requests(self):
        state = self.state

        class Ordered(FakeClient):
            def load(self, task):
                if task["page_number"] == 2:
                    self.rows_before_page_two = [row["job_id"] for row in state.rows]
                return super().load(task)

        client = Ordered(state)
        self.assertEqual(collect(state, client), ("completed", None))
        self.assertEqual(client.calls, [("list", 1), ("list", 2)])
        self.assertEqual(client.rows_before_page_two, ["1", "2", "3"])
        self.assertEqual([row["job_id"] for row in state.rows], ["1", "2", "3", "4"])
        self.assertEqual(
            [row["experience"] for row in state.rows],
            ["6 tháng", "3 năm", "Không rõ", "2 năm"],
        )
        self.assertTrue(all(row["location"] == "Hồ Chí Minh" for row in state.rows))
        self.assertEqual(state.queue, [])

    def test_page_cap_keeps_frontier_and_csv_is_recovered(self):
        self.assertEqual(
            collect(self.state, FakeClient(self.state), max_pages=1),
            ("limited", "max_pages"),
        )
        (self.state.folder / "jobs_topcv.csv").write_text("broken")
        restored = CollectionState.resume(self.root, self.state.folder)
        with (restored.folder / "jobs_topcv.csv").open(
            encoding="utf-8-sig", newline=""
        ) as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 3)
        self.assertEqual(restored.queue[0]["url"], NEXT)
        self.assertEqual(collect(restored, FakeClient(restored)), ("completed", None))

    def test_interrupt_after_checkpoint_commit_resumes_without_duplicate_row(self):
        publish = self.state.publish

        def interrupt():
            if len(self.state.rows) == 3:
                raise KeyboardInterrupt()
            publish()

        with patch.object(self.state, "publish", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                collect(self.state, FakeClient(self.state))
        restored = CollectionState.resume(self.root, self.state.folder)
        client = FakeClient(restored)
        self.assertEqual(collect(restored, client), ("completed", None))
        self.assertEqual(client.calls, [("list", 2)])
        self.assertEqual([row["job_id"] for row in restored.rows], ["1", "2", "3", "4"])

    def test_page_planner_continues_until_explicit_empty_with_fake_transport(self):
        state = self.state

        class WithoutCounter(FakeClient):
            def load(self, task):
                self.calls.append(("list", task["page_number"]))
                page = task["page_number"]
                if page <= 2:
                    html = html_page([(str(page), "3 năm")]).split(
                        '<span id="job-listing-paginate-text">'
                    )[0]
                    html += f'<a rel="next" data-href="?page={page + 1}">Next</a>'
                else:
                    html = '<div class="job-list-search-result">Không tìm thấy việc làm phù hợp</div>'
                return html, AT

        client = WithoutCounter(state)
        self.assertEqual(collect(state, client), ("completed", None))
        self.assertEqual(
            [item for item in client.calls if item[0] == "list"],
            [("list", 1), ("list", 2), ("list", 3)],
        )
        self.assertEqual(len(state.rows), 2)
        self.assertEqual(
            state.manifest["pages"][-1]["pagination"]["end_reason"], "explicit_empty"
        )

    def test_duplicate_only_page_respects_page_cap_and_resumes_to_next_page(self):
        state = self.state

        class Repeated(FakeClient):
            def load(self, task):
                self.calls.append(("list", task["page_number"]))
                page = task["page_number"]
                html = html_page([("1" if page <= 2 else "2", "3 năm")])
                html = html.split('<span id="job-listing-paginate-text">')[0]
                html += f'<span id="job-listing-paginate-text">{page} / 3 trang</span>'
                if page < 3:
                    html += f'<a rel="next" data-href="?page={page + 1}">Next</a>'
                return html, AT

        self.assertEqual(
            collect(state, Repeated(state), max_pages=2), ("limited", "max_pages")
        )
        self.assertEqual(state.queue[0]["page_number"], 3)
        page = state.manifest["pages"][1]
        self.assertEqual(page["new_ids_vs_previous_pages"], 0)
        self.assertEqual(page["rows_kept"], 0)
        self.assertEqual(page["duplicate_ids_skipped"], 1)
        self.assertEqual(page["processed_cards"], 1)
        restored = CollectionState.resume(self.root, state.folder)
        client = Repeated(restored)
        self.assertEqual(collect(restored, client, max_pages=3), ("completed", None))
        self.assertEqual(client.calls, [("list", 3)])
        self.assertEqual([row["job_id"] for row in restored.rows], ["1", "2"])
        self.assertEqual(restored.seen_ids, {"1", "2"})
        self.assertEqual(restored.stored_ids, {"1", "2"})

    def test_resume_rejects_corrupted_cards_and_pagination_before_fetching(self):
        collect(self.state, FakeClient(self.state), max_jobs=1)
        path = self.state.folder / "checkpoint.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        for kind in ["card", "pagination", "parser"]:
            with self.subTest(kind=kind):
                data = json.loads(json.dumps(original))
                if kind == "card":
                    data["queue"][0]["cards"][1]["job_title"] = "tampered"
                elif kind == "pagination":
                    data["manifest"]["pages"][0]["next_url"] = URL + "?page=99"
                else:
                    data["manifest"]["parser_fingerprint"] = "unrecognized-parser-revision"
                write_json(path, data)
                with self.assertRaisesRegex(ValueError, "Parser changed" if kind == "parser" else "checksum"):
                    CollectionState.resume(self.root, self.state.folder)


class FiftyCardClient(FakeClient):
    def load(self, task):
        if task["page_number"] != 1:
            return super().load(task)
        self.calls.append(("list", 1))
        return html_page([(str(i), "6 tháng") for i in range(1, 51)], following=True), AT

    def close(self):
        self.closed = True


class BatchCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_page_commits_batches_and_exports_csv_only_when_rows_change(self):
        state = CollectionState.create(self.root, [PROFILE], {})
        with patch("collection.jobs.utils.collection_state.write_json", wraps=write_json) as checkpoints, \
                patch("collection.jobs.utils.collection_state.write_csv", wraps=write_csv) as exports:
            self.assertEqual(collect(state, FiftyCardClient(state), max_pages=1),
                             ("limited", "max_pages"))
            self.assertEqual(checkpoints.call_count, 7)
            self.assertEqual(exports.call_count, 1)
            state.finish("limited", "max_pages")
            self.assertEqual(exports.call_count, 1)
        with (state.folder / "jobs_topcv.csv").open(encoding="utf-8-sig", newline="") as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 50)

        state.begin_pass(1, 1)
        with patch("collection.jobs.utils.collection_state.write_json", wraps=write_json) as checkpoints, \
                patch("collection.jobs.utils.collection_state.write_csv", wraps=write_csv) as exports:
            collect(state, FiftyCardClient(state), max_pages=1)
            self.assertEqual(checkpoints.call_count, 7)
            self.assertEqual(exports.call_count, 0)
        self.assertEqual(len(state.rows), 50)
        self.assertEqual(state.manifest["pages"][-1]["duplicate_ids_skipped"], 50)

    def test_abrupt_exit_replays_only_uncommitted_cards_without_refetching(self):
        class AbruptExit(BaseException):
            pass

        state = CollectionState.create(self.root, [PROFILE], {})
        original_store = state.store_next_job

        def exit_after_27(task, limit):
            row = original_store(task, limit)
            if task["offset"] == 27:
                raise AbruptExit()
            return row

        with patch.object(state, "store_next_job", side_effect=exit_after_27):
            with self.assertRaises(AbruptExit):
                collect(state, FiftyCardClient(state), max_pages=1)
        checkpoint = json.loads((state.folder / "checkpoint.json").read_text())
        self.assertEqual(checkpoint["queue"][0]["offset"], 20)
        self.assertEqual(len(checkpoint["rows"]), 20)
        self.assertFalse((state.folder / "jobs_topcv.csv").exists())

        restored = CollectionState.resume(self.root, state.folder)
        self.assertEqual(restored.stored_ids, {str(i) for i in range(1, 21)})
        self.assertEqual(restored.seen_ids, {str(i) for i in range(1, 51)})
        client = FiftyCardClient(restored)
        collect(restored, client, max_pages=1)
        self.assertEqual(client.calls, [])
        self.assertEqual([row["job_id"] for row in restored.rows], [str(i) for i in range(1, 51)])
        with (state.folder / "jobs_topcv.csv").open(encoding="utf-8-sig", newline="") as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 50)

    def test_limit_interrupt_and_error_flush_partial_batch_and_csv(self):
        for stop in ("limit", "interrupt", "error"):
            with self.subTest(stop=stop):
                root = self.root / stop
                state = CollectionState.create(root, [PROFILE], {})
                client = FiftyCardClient(state)
                args = SimpleNamespace(max_jobs=17 if stop == "limit" else None, max_pages=1,
                    channel="chrome", headless=True, delay=15, jitter=5, max_retries=3,
                    backoff=30, max_auto_wait=240, timeout=60)
                original_store = state.store_next_job

                def stop_after_17(task, limit):
                    row = original_store(task, limit)
                    if task["offset"] == 17:
                        if stop == "interrupt":
                            raise KeyboardInterrupt()
                        if stop == "error":
                            raise RuntimeError("fixture processing failure")
                    return row

                with patch("collection.jobs.collection_runner.ListingClient", return_value=client), \
                        patch.object(state, "store_next_job", side_effect=stop_after_17):
                    self.assertEqual(run_search_collection(args, root, state=state),
                                     0 if stop == "limit" else 1)
                self.assertTrue(client.closed)
                checkpoint = json.loads((state.folder / "checkpoint.json").read_text())
                self.assertEqual(checkpoint["queue"][0]["offset"], 17)
                self.assertEqual(len(checkpoint["rows"]), 17)
                self.assertEqual(checkpoint["manifest"]["result"],
                                 {"limit": "limited", "interrupt": "interrupted", "error": "failed"}[stop])
                with (state.folder / "jobs_topcv.csv").open(encoding="utf-8-sig", newline="") as stream:
                    self.assertEqual(len(list(csv.DictReader(stream))), 17)
                restored = CollectionState.resume(root, state.folder)
                following = FiftyCardClient(restored)
                collect(restored, following, max_pages=1)
                self.assertEqual(following.calls, [])
                self.assertEqual(len(restored.rows), 50)

    def test_interrupted_batch_commit_recovers_previous_checkpoint(self):
        class AbruptExit(BaseException):
            pass

        state = CollectionState.create(self.root, [PROFILE], {})

        def interrupt_commit(source, target):
            if Path(target).name == "checkpoint.json":
                pending = json.loads(Path(source).read_text(encoding="utf-8"))
                if len(pending["rows"]) == 30:
                    raise AbruptExit()
            return replace_with_retry(source, target)

        with patch("collection.common.storage.replace_with_retry", side_effect=interrupt_commit):
            with self.assertRaises(AbruptExit):
                collect(state, FiftyCardClient(state), max_pages=1)
        self.assertEqual(len(state.rows), 30)
        checkpoint = json.loads((state.folder / "checkpoint.json").read_text())
        self.assertEqual(len(checkpoint["rows"]), 20)
        self.assertEqual(checkpoint["queue"][0]["offset"], 20)
        restored = CollectionState.resume(self.root, state.folder)
        client = FiftyCardClient(restored)
        collect(restored, client, max_pages=1)
        self.assertEqual(client.calls, [])
        self.assertEqual([row["job_id"] for row in restored.rows], [str(i) for i in range(1, 51)])
        with (state.folder / "jobs_topcv.csv").open(encoding="utf-8-sig", newline="") as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 50)


class RepeatPassTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = CollectionState.create(self.root, [PROFILE], {})
        collect(self.state, FakeClient(self.state))
        self.state.finish("completed")
        self.original_rows = [dict(row) for row in self.state.rows]

    def test_restart_through_open_collection_keeps_history_and_deduplicates(self):
        args = SimpleNamespace(resume=self.state.folder, start_page=1, max_pages=2)
        state = open_collection(args, self.root)
        self.assertEqual(state.seen_ids, {"1", "2", "3", "4"})
        self.assertEqual(state.manifest["active_pass_start_index"], 2)
        self.assertEqual(
            collect(state, UpdatedListings(state), max_pages=2), ("completed", None)
        )
        self.assertEqual(state.rows[:4], self.original_rows)
        self.assertEqual(
            [row["job_id"] for row in state.rows], ["1", "2", "3", "4", "5", "6"]
        )
        self.assertEqual(
            [page["page_number"] for page in state.manifest["pages"]], [1, 2, 1, 2]
        )
        self.assertEqual(state.manifest["collection_passes"][0]["records_at_start"], 4)

    def test_pending_page_is_archived_when_another_pass_starts(self):
        self.state.begin_pass(1, 2)
        old_queue = [dict(task) for task in self.state.queue]
        self.state.begin_pass(2, None)
        self.assertEqual(
            self.state.manifest["collection_passes"][-1]["superseded_queue"], old_queue
        )
        self.assertEqual(self.state.queue[0]["page_number"], 2)
        self.assertIs(self.state.queue, self.state.data["queue"])
        self.assertEqual(self.state.rows, self.original_rows)

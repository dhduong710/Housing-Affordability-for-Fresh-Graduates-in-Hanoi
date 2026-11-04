"""The detail sampler saves each result and resumes without refetching it."""

import argparse
import contextlib
import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "collection"))

import housing_detail_sample as sample


class AllowAllRobots:
    def can_fetch(self, user_agent, url):
        return True

    def crawl_delay(self, user_agent):
        return None


class DetailSampleTests(unittest.TestCase):
    def test_standalone_collects_listing_pages_then_resumes_details(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = argparse.Namespace(input=None, pages=2, resume=None,
                                      max_details=1, delay=2.0, timeout=30,
                                      output_root=root / "output")
            detail_html = ('<div class="re__project-title">Example project</div>'
                           '<div class="re__pr-description">Example description</div>')

            def fetch(session, url, timeout, delay, run_dir, log):
                if url.endswith("/p1") or url.endswith("/p2"):
                    number = url[-1]
                    card = (f'<a data-product-id="{number}" '
                            f'href="/ban-can-ho-chung-cu-ha-noi/example-pr{number}">'
                            '<div class="re__card-title">Example apartment</div>'
                            '<span class="re__card-config-price">2 tỷ</span>'
                            '<span class="re__card-config-area">50 m²</span>'
                            '<span class="re__card-location">Q. Hà Đông</span>'
                            '</a>')
                    return card, 200, 0.1
                return detail_html, 200, 0.1

            with patch.object(sample, "session_for_site", return_value=contextlib.nullcontext(object())), \
                 patch.object(sample, "read_robots", return_value=AllowAllRobots()), \
                 patch.object(sample, "fetch_html", side_effect=fetch) as fetched, \
                 patch.object(sample.time, "sleep"):
                run_dir, first_log = sample.run(args)
                self.assertEqual((first_log["status"], first_log["completed_details"]), ("partial", 1))
                self.assertEqual(len(list((run_dir / "pages").glob("p*.jsonl"))), 2)
                self.assertTrue((run_dir / "listing_sample.csv").exists())
                args.resume = run_dir
                args.max_details = None
                _, second_log = sample.run(args)
            self.assertEqual((second_log["status"], second_log["completed_details"]), ("complete", 2))
            self.assertEqual(fetched.call_count, 4)

    def test_checkpoint_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "housing_raw.csv"
            columns = ["listing_id", "source_url", "page_url", "price_vnd",
                       "area_m2", "quality_flags", *sample.DETAIL_FIELDS]
            with source.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                for number in (1, 2):
                    writer.writerow({"listing_id": str(number),
                                     "source_url": f"https://batdongsan.com.vn/apartment-pr{number}",
                                     "page_url": f"https://batdongsan.com.vn/list/p{number}",
                                     "price_vnd": "2000000000", "area_m2": "50",
                                     "quality_flags": ""})
            html = ('<div class="re__project-title">Example project</div>'
                    '<div class="re__pr-description">Example description</div>')
            args = argparse.Namespace(input=source, pages=2, resume=None,
                                      max_details=1, delay=2.0, timeout=30,
                                      output_root=root / "output")
            with patch.object(sample, "session_for_site", return_value=contextlib.nullcontext(object())), \
                 patch.object(sample, "read_robots", return_value=AllowAllRobots()), \
                 patch.object(sample, "fetch_html", return_value=(html, 200, 0.1)) as fetch, \
                 patch.object(sample.time, "sleep"):
                run_dir, first_log = sample.run(args)
                self.assertEqual((first_log["status"], first_log["completed_details"]), ("partial", 1))
                args.resume = run_dir
                args.input = None
                args.max_details = None
                _, second_log = sample.run(args)
            self.assertEqual((second_log["status"], second_log["completed_details"]), ("complete", 2))
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(len(list((run_dir / "details").glob("*.json"))), 2)
            with (run_dir / "housing_detail_sample.csv").open(encoding="utf-8-sig", newline="") as handle:
                enriched = list(csv.DictReader(handle))
            self.assertEqual([row["project_name"] for row in enriched], ["Example project"] * 2)
            with source.open(encoding="utf-8-sig", newline="") as handle:
                original = list(csv.DictReader(handle))
            self.assertTrue(all(not row["project_name"] for row in original))


if __name__ == "__main__":
    unittest.main()

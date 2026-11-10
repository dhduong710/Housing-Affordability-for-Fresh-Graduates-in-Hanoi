"""Full-detail collection keeps all source rows and resumes from per-ID checkpoints."""

import argparse
import contextlib
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "collection"))

from housing_common import Listing
from housing_requests_detail_crawler import run


class AllowAllRobots:
    def can_fetch(self, agent, url):
        return True

    def crawl_delay(self, agent):
        return None


class FullDetailTests(unittest.TestCase):
    def test_partial_run_preserves_rows_then_resumes_without_refetching(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "listings.csv"
            columns = list(Listing.__dataclass_fields__)
            with source.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                for listing_id in ("1", "2", "3"):
                    writer.writerow({
                        "listing_id": listing_id,
                        "source_url": f"https://batdongsan.com.vn/ban-can-ho-chung-cu-ha-noi/test-pr{listing_id}",
                        "page_url": "https://batdongsan.com.vn/ban-can-ho-chung-cu-ha-noi/p1",
                        "price_text": "2 tỷ", "area_text": "50 m²",
                        "price_vnd": "2000000000", "area_m2": "50",
                    })
            original = source.read_bytes()
            args = argparse.Namespace(
                input=source, resume=None, output_root=root / "runs",
                max_details=2, delay=2.0, timeout=5,
                flush_every=2, max_consecutive_errors=5,
            )
            seen = []

            def fetch(session, url, timeout, delay, run_dir, log):
                seen.append(url[-1])
                if url.endswith("pr2"):
                    raise RuntimeError(f"HTTP 404 at {url}")
                return (
                    '<div class="re__project-title">Test project</div>'
                    '<div class="re__pr-description">Test description</div>',
                    200, 0.1,
                )

            with patch("housing_requests_detail_crawler.session_for_site",
                       return_value=contextlib.nullcontext(object())), \
                 patch("housing_requests_detail_crawler.read_robots",
                       return_value=AllowAllRobots()), \
                 patch("housing_requests_detail_crawler.fetch_html", side_effect=fetch), \
                 patch("housing_requests_detail_crawler.time.sleep"):
                run_dir, first = run(args)
                self.assertEqual(first["status"], "partial")
                self.assertEqual(first["detail_status_counts"],
                                 {"ok": 1, "pending": 1, "not_found": 1, "error": 0})
                args.resume = run_dir
                args.max_details = None
                _, second = run(args)

            self.assertEqual(second["status"], "complete")
            self.assertEqual(seen, ["1", "2", "3"])
            self.assertEqual(source.read_bytes(), original)
            with (run_dir / "housing_details_raw.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 3)
            self.assertEqual([row["detail_status"] for row in rows],
                             ["ok", "not_found", "ok"])
            self.assertEqual(rows[0]["project_name"], "Test project")
            self.assertEqual(rows[1]["project_name"], "")
            report = json.loads((run_dir / "quality_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["total_listings"], 3)


if __name__ == "__main__":
    unittest.main()

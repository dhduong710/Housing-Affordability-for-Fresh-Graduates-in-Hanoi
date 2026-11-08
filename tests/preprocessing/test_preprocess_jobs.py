"""Raw-to-processed CSV flows: domain policy, QA and data preservation."""

import csv
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from collection.common.storage import write_csv
from collection.jobs.sources.topcv_parser import FIELDS
from preprocessing.preprocess_jobs import DERIVED_FIELDS, run


class PipelineTests(unittest.TestCase):
    def row(
        self, identifier, location="Hà Nội", experience="1–3 năm", salary="12–15 triệu"
    ):
        row = dict.fromkeys(FIELDS, "")
        row.update(
            job_id=identifier,
            source="topcv",
            source_url="https://example.test/" + identifier,
            collected_at="2026-11-07T00:00:00+00:00",
            job_title="Senior Fresher",
            location=location,
            experience=experience,
            salary_text=salary,
        )
        return row

    def test_raw_csv_to_processed_applies_location_experience_and_salary_policy(self):
        cases = [
            (
                "HN",
                "6 tháng",
                "12–15 triệu",
                True,
                True,
                "12000000",
                "15000000",
                "VND",
                "month",
            ),
            (
                "Hà Nội",
                "2 năm",
                "500–800 USD/năm",
                True,
                True,
                "500",
                "800",
                "USD",
                "year",
            ),
            (
                "Hà Nội",
                "1–3 năm",
                "Từ 10 triệu",
                True,
                True,
                "10000000",
                "",
                "VND",
                "month",
            ),
            (
                "Hà Nội",
                "Trên 2 năm",
                "Tới 20 triệu",
                True,
                False,
                "",
                "20000000",
                "VND",
                "month",
            ),
            ("Hà Nội", "25 tháng", "Thỏa thuận", True, False, "", "", "", "month"),
            (
                "Hồ Chí Minh & 2 nơi khác",
                "Có kinh nghiệm",
                "Không lương",
                None,
                None,
                "",
                "",
                "",
                "month",
            ),
            (
                "Hồ Chí Minh",
                "Không yêu cầu",
                "50 USD/giờ",
                False,
                True,
                "50",
                "50",
                "USD",
                "hour",
            ),
            ("Toàn quốc", "Dưới 1 năm", "0 triệu", None, True, "", "", "VND", "month"),
            (
                "Hà Nội",
                "Từ 2 năm trở lên",
                "20–10 triệu",
                True,
                True,
                "",
                "",
                "VND",
                "month",
            ),
            (
                "Hà Nội",
                "Có kinh nghiệm",
                "12 triệu/tháng/năm",
                True,
                None,
                "12000000",
                "12000000",
                "VND",
                "",
            ),
            (
                "Hanoi",
                "18 tháng–3 năm",
                "10,5 tr/tháng",
                True,
                True,
                "10500000.0",
                "10500000.0",
                "VND",
                "month",
            ),
            ("Miền Bắc", "3–1 năm", "10–15", None, None, "", "", "", "month"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "raw.csv"
            write_csv(
                path,
                [
                    self.row(str(i), location, experience, salary)
                    for i, (location, experience, salary, *_) in enumerate(cases)
                ],
                FIELDS,
            )
            result = run(path, root=root)
            with Path(result["processed_csv"]).open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                processed = list(csv.DictReader(stream))
            self.assertEqual(len(processed), len(cases))
            for case, record in zip(cases, processed):
                with self.subTest(case=case):
                    _, _, _, hanoi, entry, low, high, currency, period = case
                    eligible = (
                        False
                        if hanoi is False or entry is False
                        else True
                        if hanoi is True and entry is True
                        else None
                    )

                    def boolean(value):
                        return "" if value is None else str(value).lower()

                    self.assertEqual(
                        (
                            record["is_hanoi"],
                            record["is_entry_level"],
                            record["is_eligible"],
                        ),
                        tuple(map(boolean, [hanoi, entry, eligible])),
                    )
                    self.assertEqual(
                        tuple(
                            record[name]
                            for name in [
                                "salary_min",
                                "salary_max",
                                "salary_currency",
                                "salary_period",
                            ]
                        ),
                        (low, high, currency, period),
                    )

    def test_outputs_preserve_raw_rows_duplicates_and_are_reproducible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            raw_dir = root / "data/raw/jobs/run1"
            raw_dir.mkdir(parents=True)
            path = raw_dir / "jobs_topcv.csv"
            rows = [
                self.row("001"),
                self.row("002", location="Hồ Chí Minh & 2 nơi khác"),
                self.row("003", experience="3 năm"),
                self.row("001"),
            ]
            rows[0]["company"] = "NA"
            rows[1]["company"] = "nan"
            rows[2]["company"] = "NULL"
            write_csv(path, rows, FIELDS)
            before = path.read_bytes()
            result = run(raw_dir, root=root)
            output = Path(result["processed_csv"])
            contents = output.read_bytes()
            with output.open(encoding="utf-8-sig", newline="") as stream:
                processed = list(csv.DictReader(stream))
            self.assertEqual(list(processed[0]), FIELDS + DERIVED_FIELDS)
            self.assertEqual([{k: row[k] for k in FIELDS} for row in processed], rows)
            self.assertEqual(processed[0]["job_id"], "001")
            self.assertEqual(processed[0]["salary_period"], "month")
            self.assertEqual(processed[1]["is_hanoi"], "")
            report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
            self.assertEqual(report["input_file"], "data/raw/jobs/run1/jobs_topcv.csv")
            self.assertEqual(report["output_file"], "data/processed/jobs/run1/salary_clean.csv")
            self.assertEqual(report["output_columns"], list(processed[0]))
            self.assertEqual(report["processed_schema_version"], 2)
            self.assertEqual(report["salary_policy"]["missing_period"], "month")
            self.assertEqual(report["row_counts"]["dropped"], 0)
            self.assertEqual(report["row_counts"]["duplicate_source_job_id_rows"], 1)
            self.assertEqual(report["row_counts"]["eligible_hanoi_entry_level"], 2)
            self.assertEqual(report["input_sha256"], hashlib.sha256(before).hexdigest())
            self.assertEqual(
                report["classification"]["is_eligible"],
                {"true": 2, "false": 1, "unknown": 1},
            )
            run(raw_dir, root=root)
            self.assertEqual(output.read_bytes(), contents)
            self.assertEqual(path.read_bytes(), before)

            checkout = root / "another checkout"
            shutil.copytree(Path(__file__).resolve().parents[2] / "src", checkout / "src",
                            ignore=shutil.ignore_patterns("__pycache__"))
            copied_raw = checkout / "data/raw/jobs/run1/jobs_topcv.csv"
            copied_raw.parent.mkdir(parents=True)
            copied_raw.write_bytes(before)
            completed = subprocess.run(
                [sys.executable, "-B", str(checkout / "src/preprocessing/preprocess_jobs.py"),
                 "--input", "data/raw/jobs/run1"], cwd=root,
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            copied_output = checkout / "data/processed/jobs/run1/salary_clean.csv"
            self.assertEqual(copied_output.read_bytes(), contents)
            copied_report = json.loads((checkout / "reports/preprocessing/jobs/run1/quality_report.json")
                                       .read_text(encoding="utf-8"))
            self.assertEqual(copied_report, report)
            with self.assertRaisesRegex(ValueError, "outside the raw"):
                run(raw_dir, root=root, output_directory=raw_dir / "processed")
            with self.assertRaisesRegex(ValueError, "derived columns"):
                run(output, root=root)

    def test_pandas_reader_rejects_duplicate_headers_and_malformed_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "raw.csv"
            for text in [
                "job_id\n1\n",
                ",".join(FIELDS + ["job_id"]) + "\n",
                ",".join(FIELDS) + "\n1,topcv\n",
                ",".join(FIELDS) + "\n" + ",".join(["value"] * 11) + "\n",
            ]:
                with self.subTest(text=text):
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        run(path, root=root)
            write_csv(path, [], FIELDS)
            self.assertEqual(run(path, root=root)["processed"], 0)

    def test_nullable_booleans_and_decimal_values_survive_csv_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "raw.csv"
            rows = [
                self.row("01", experience="Không yêu cầu", salary="$1,000.50/tháng"),
                self.row(
                    "02", location="", experience="Có kinh nghiệm", salary="Thỏa thuận"
                ),
                self.row("03", salary="Tới 20 triệu"),
            ]
            write_csv(path, rows, FIELDS)
            result = run(path, root=root)
            with Path(result["processed_csv"]).open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                output = list(csv.DictReader(stream))
            self.assertEqual(output[0]["salary_min"], "1000.50")
            self.assertEqual(output[0]["experience_min_months"], "0")
            self.assertEqual(output[0]["is_eligible"], "true")
            self.assertEqual(output[1]["is_eligible"], "")
            self.assertEqual(output[1]["salary_min"], "")
            self.assertEqual(output[2]["salary_min"], "")
            self.assertEqual(output[2]["salary_max"], "20000000")


if __name__ == "__main__":
    unittest.main()

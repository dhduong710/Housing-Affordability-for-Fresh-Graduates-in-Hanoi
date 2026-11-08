"""Annotate a raw jobs CSV without changing or dropping its records."""

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import pandas as pd

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collection.common.storage import path_reference, replace_with_retry, write_json
from collection.jobs.sources.topcv_parser import FIELDS
from preprocessing.job_parsers import (
    POLICY_VERSION,
    parse_experience,
    parse_location,
    parse_salary,
)

ROOT = Path(__file__).resolve().parents[2]
DERIVED_FIELDS = [
    'location_normalized', 'is_hanoi', 'is_multi_location',
    'experience_min_months', 'experience_max_months', 'is_entry_level',
    'salary_min', 'salary_max', 'salary_currency', 'salary_period',
    'is_eligible',
]


BOOLEAN_FIELDS = ['is_hanoi', 'is_multi_location', 'is_entry_level', 'is_eligible']
NUMBER_FIELDS = ['experience_min_months', 'experience_max_months', 'salary_min', 'salary_max']


def transform(raw):
    """Apply domain parsers, then combine nullable boolean evidence with Kleene AND."""
    location = pd.DataFrame(raw['location'].map(parse_location).tolist(),
                            index=raw.index, columns=DERIVED_FIELDS[:3], dtype=object)
    experience = pd.DataFrame(raw['experience'].map(parse_experience).tolist(),
                              index=raw.index, columns=DERIVED_FIELDS[3:6], dtype=object)
    salary = pd.DataFrame([parse_salary(text, source) for text, source in
                           zip(raw['salary_text'], raw['source'])],
                          index=raw.index, columns=DERIVED_FIELDS[6:10], dtype=object)
    processed = pd.concat([raw, location, experience, salary], axis=1)
    for name in BOOLEAN_FIELDS[:-1]:
        processed[name] = processed[name].astype('boolean')
    processed['is_eligible'] = processed['is_hanoi'] & processed['is_entry_level']
    return processed


def write_processed_csv(path, frame):
    """Use Pandas CSV export while preserving atomic replacement and exact decimals."""
    export = frame.copy()
    for name in BOOLEAN_FIELDS:
        export[name] = export[name].astype('string').str.lower()
    for name in NUMBER_FIELDS:
        export[name] = export[name].map(lambda value:
            '' if pd.isna(value) else format(value, 'f') if isinstance(value, Decimal) else str(value))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8-sig', newline='',
                                          dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            export.to_csv(stream, index=False, na_rep='', lineterminator='\r\n')
            stream.flush()
            os.fsync(stream.fileno())
        replace_with_retry(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def run(input_path, root=ROOT, output_directory=None):
    root = Path(root).resolve()
    input_path = (root / input_path).resolve()
    if input_path.is_dir():
        input_path /= 'jobs_topcv.csv'
    if input_path.suffix.lower() != '.csv':
        raise ValueError('Input must be a raw CSV or its run directory')
    payload = input_path.read_bytes()  # One consistent input snapshot, even if a crawler is still running.
    # Validate structure before Pandas can rename duplicate headers or pad short rows.
    reader = csv.reader(io.StringIO(payload.decode('utf-8-sig'), newline=''), strict=True)
    fields = next(reader, [])
    if len(fields) != len(set(fields)) or not set(FIELDS).issubset(fields):
        raise ValueError('Input must have unique headers and all 10 raw job columns')
    if set(fields) & set(DERIVED_FIELDS):
        raise ValueError('Input already contains derived columns; use the raw CSV')
    if any(len(row) != len(fields) for row in reader if row):
        raise ValueError('Malformed CSV row: column count does not match the header')
    raw = pd.read_csv(io.BytesIO(payload), encoding='utf-8-sig', dtype=str,
                      keep_default_na=False, on_bad_lines='error')
    run_id = input_path.parent.name if input_path.name == 'jobs_topcv.csv' else input_path.stem
    output = (root / output_directory).resolve() if output_directory else root / 'data/processed/jobs' / run_id
    report_directory = root / 'reports/preprocessing/jobs' / run_id
    # Reject raw-directory destinations before creating directories or replacing artifacts.
    raw_root = root / 'data/raw'
    for directory in [output, report_directory]:
        if directory.is_relative_to(raw_root.resolve()) or directory == input_path.parent:
            raise ValueError('Processed output and reports must be outside the raw input directory')
    if (output / 'salary_clean.csv').resolve() == input_path:
        raise ValueError('Processed output cannot overwrite the raw input file')
    processed = transform(raw)
    eligible = processed['is_eligible'].fillna(False)
    numeric = processed[['salary_min', 'salary_max']].notna().any(axis=1)
    closed_monthly_vnd = (processed[['salary_min', 'salary_max']].notna().all(axis=1)
                          & processed['salary_currency'].eq('VND')
                          & processed['salary_period'].eq('month'))

    report = {
        'processed_schema_version': 2, 'output_columns': fields + DERIVED_FIELDS,
        'policy_version': POLICY_VERSION, 'run_id': run_id,
        'input_file': path_reference(input_path, root), 'input_sha256': hashlib.sha256(payload).hexdigest(),
        'rules_sha256': hashlib.sha256(Path(__file__).with_name('job_parsers.py').read_bytes()).hexdigest(),
        'experience_policy': {'interval_months': [0, 24], 'match': 'nonempty_intersection',
                              'uses_job_title': False},
        'salary_policy': {'missing_period': 'month', 'default_period_basis': 'project assumption for unspecified listing-card periods',
                          'currency_conversion': False, 'topcv_local_money_units': 'infer VND',
                          'missing_bound': 'null'},
        'row_counts': {'raw': len(raw), 'processed': len(processed), 'dropped': 0,
                       'duplicate_source_job_id_rows': int(raw.duplicated(['source', 'job_id']).sum()),
                       'eligible_hanoi_entry_level': int(eligible.sum()),
                       'salary_numeric': int(numeric.sum()),
                       'eligible_salary_numeric': int((eligible & numeric).sum()),
                       'salary_closed_monthly_vnd': int(closed_monthly_vnd.sum()),
                       'eligible_salary_closed_monthly_vnd': int((eligible & closed_monthly_vnd).sum())},
        'classification': {name: {'true': int(processed[name].eq(True).sum()),
                                   'false': int(processed[name].eq(False).sum()),
                                   'unknown': int(processed[name].isna().sum())}
                           for name in BOOLEAN_FIELDS},
        'eligible_fraction': round(int(eligible.sum()) / len(raw), 6) if len(raw) else None,
        'missing_raw': {name: int(raw[name].str.strip().eq('').sum()) for name in FIELDS},
        'missing_derived': {name: int((processed[name].isna() | processed[name].eq('')).sum())
                            for name in DERIVED_FIELDS},
        'output_file': path_reference(output / 'salary_clean.csv', root),
        'limitations': [
            'Eligibility uses the location and experience displayed on listing cards.',
            'Experience eligibility means a nonempty intersection with 0-24 months.',
            'A multi-location listing may include Hanoi alongside other workplaces.',
            'Unspecified salary periods use month; amounts retain the advertised currency and pay basis.',
            'Analysis requires sample validation, occupation grouping and selection of comparable salary records.',
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    write_processed_csv(output / 'salary_clean.csv', processed)
    report['output_sha256'] = hashlib.sha256((output / 'salary_clean.csv').read_bytes()).hexdigest()
    write_json(report_directory / 'quality_report.json', report)
    return {'processed_csv': str(output / 'salary_clean.csv'),
            'report': str(report_directory / 'quality_report.json'), **report['row_counts']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='Raw run directory or CSV; relative to repository root')
    parser.add_argument('--output-directory', type=Path, help='Processed directory; default: data/processed/jobs/<run_id>')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.input, output_directory=args.output_directory), ensure_ascii=False))
    except (OSError, ValueError, csv.Error) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

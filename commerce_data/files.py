"""Read bounded local files; retain source hashes without publishing raw rows."""

import csv
import hashlib
import json
import platform
import zipfile
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path

from .core import ContractError, analyze, digest


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def check_headers(headers):
    if not headers or any(not isinstance(h, str) or not h or h != h.strip() for h in headers) or len(set(headers)) != len(headers):
        raise ContractError("headers_must_be_nonempty_unique_strings")
    return headers


def records(headers, values):
    for row_number, row in enumerate(values, start=2):
        if len(row) != len(headers):
            raise ContractError("column_count_mismatch_at_row:" + str(row_number))
        yield row_number, dict(zip(headers, row))


@contextmanager
def open_rows(path, sheet=None):
    path = Path(path)
    if path.stat().st_size > 100_000_000:
        raise ContractError("file_size_limit_exceeded")
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            headers = check_headers(next(reader, []))
            yield headers, records(headers, reader), {"reader": "stdlib.csv"}
    elif path.suffix.lower() == ".xlsx":
        if not sheet:
            raise ContractError("xlsx_sheet_must_be_explicit")
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > 1000 or sum(i.file_size for i in archive.infolist()) > 600_000_000:
                raise ContractError("xlsx_expansion_limit_exceeded")
        import openpyxl
        import openpyxl.xml
        if not openpyxl.xml.DEFUSEDXML:
            raise ContractError("defusedxml_required")
        # Formula text is not evaluated; mapped numeric formulas fail validation.
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=False, keep_links=False)
        try:
            if sheet not in workbook.sheetnames:
                raise ContractError("sheet_not_found")
            reader = workbook[sheet].iter_rows(values_only=True)
            headers = check_headers(list(next(reader, [])))
            yield headers, records(headers, reader), {
                "reader": "openpyxl", "openpyxl": version("openpyxl"),
                "et-xmlfile": version("et-xmlfile"), "defusedxml": version("defusedxml"),
            }
        finally:
            workbook.close()
    else:
        raise ContractError("only_csv_and_xlsx_supported")


def analyze_file(path, mapping_path, start, split, end, sheet=None,
                 confirmed=False, required_capability="record_sales"):
    source_hash, mapping_hash = sha256(path), sha256(mapping_path)
    mapping = json.loads(Path(mapping_path).read_text(encoding="utf-8"))
    with open_rows(path, sheet) as (headers, rows, reader):
        result = analyze(rows, headers, mapping, start, split, end, confirmed, required_capability)
    if sha256(path) != source_hash or sha256(mapping_path) != mapping_hash:
        raise ContractError("input_changed_during_analysis")
    code_hashes = {p.name: sha256(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    result["evidence"] = {
        "source_sha256": source_hash, "source_file": Path(path).name, "sheet": sheet,
        "mapping_file_sha256": mapping_hash, "mapping_semantics_sha256": digest(mapping),
        "code_sha256": code_hashes, "python": platform.python_version(),
        "reader": reader,
        "formula": "sum(abs(quantity * unit_price * unit_price_multiplier)) by confirmed event rule",
    }
    return result

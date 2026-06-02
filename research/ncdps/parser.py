"""Parse NCDPS fixed-width bulk-download files.

Each NCDPS table comes as a `.zip` containing a `.des` schema descriptor
and a `.dat` fixed-width data file. The .des format is:

    Name          Description                        Type      Start   Length
    CMDORNUM      OFFENDER NC DOC ID NUMBER          CHAR      1       7
    CMPREFIX      COMMITMENT PREFIX                  CHAR      8       2
    ...

This module provides:
    parse_des(text) -> list[Field]
    iter_records(dat_bytes_or_path, fields) -> iterator of dict rows
    iter_zip_records(zip_path) -> iterator of dict rows (auto-loads .des)
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class Field:
    name: str
    description: str
    type: str    # CHAR / DATE / TIME / NUM
    start: int   # 1-based start column
    length: int  # field width in bytes

    @property
    def slice(self) -> slice:
        # Convert 1-based inclusive start + length to 0-based slice
        return slice(self.start - 1, self.start - 1 + self.length)


# Lines look like:
# "CMCOUNTY      CO OF CONV MOST SERIOUS OFFNSE     CHAR      160     30"
_RECORD_RE = re.compile(
    r"^(?P<name>\S+)\s+"
    r"(?P<description>.+?)\s{2,}"
    r"(?P<type>CHAR|DATE|TIME|NUM)\s+"
    r"(?P<start>\d+)\s+"
    r"(?P<length>\d+)\s*$"
)


def parse_des(text: str) -> list[Field]:
    """Parse the .des descriptor text into a list of Field objects."""
    fields: list[Field] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("Name"):
            continue
        m = _RECORD_RE.match(line.rstrip())
        if not m:
            continue
        fields.append(Field(
            name=m["name"],
            description=m["description"].strip(),
            type=m["type"],
            start=int(m["start"]),
            length=int(m["length"]),
        ))
    if not fields:
        raise ValueError("parse_des: no fields found")
    return fields


def record_size(fields: list[Field]) -> int:
    return max(f.start - 1 + f.length for f in fields)


def _decode(b: bytes) -> str:
    # NCDPS files are mostly ASCII but occasionally contain Latin-1 bytes
    # (extended characters in names). Decode permissively.
    return b.decode("latin-1", errors="replace")


def iter_records_from_dat(dat: bytes, fields: list[Field],
                          line_separator: bytes = b"\n") -> Iterator[dict]:
    """Yield dict records from a `.dat` bytestring. NCDPS .dat files are
    newline-delimited fixed-width text."""
    for raw_line in dat.split(line_separator):
        if not raw_line:
            continue
        line = _decode(raw_line)
        row: dict[str, str] = {}
        for f in fields:
            chunk = line[f.slice].strip() if len(line) >= f.start else ""
            row[f.name] = chunk
        yield row


def iter_zip_records(zip_path: str | Path,
                     limit: int | None = None) -> Iterator[dict]:
    """Open a NCDPS .zip, auto-detect the .des + .dat members, parse the
    schema, and yield dict rows. Set `limit` for a quick sample."""
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path, "r") as zf:
        des_name = next((n for n in zf.namelist()
                         if n.lower().endswith(".des")), None)
        dat_name = next((n for n in zf.namelist()
                         if n.lower().endswith(".dat")), None)
        if not des_name or not dat_name:
            raise ValueError(f"{zip_path}: zip must contain .des + .dat")
        with zf.open(des_name) as f:
            fields = parse_des(_decode(f.read()))
        # Read the .dat in streaming chunks to avoid loading 1.2 GB into RAM.
        with zf.open(dat_name) as f:
            buf = io.BufferedReader(f, buffer_size=1 << 20)
            line_no = 0
            while True:
                line = buf.readline()
                if not line:
                    break
                if line.endswith(b"\n"):
                    line = line[:-1]
                if line.endswith(b"\r"):
                    line = line[:-1]
                if not line:
                    continue
                text = _decode(line)
                row = {}
                for fld in fields:
                    row[fld.name] = (text[fld.slice].strip()
                                     if len(text) >= fld.start else "")
                yield row
                line_no += 1
                if limit and line_no >= limit:
                    return

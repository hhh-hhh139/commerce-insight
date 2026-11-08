"""Download one pinned public dataset. No login, repository, or model access."""

import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path


URL = "https://archive.ics.uci.edu/static/public/352/online%2Bretail.zip"
ZIP_SHA256 = "f5385cbb54bbebf7196389109c6b0621faab0c304e3702548165e71c84aede8b"
XLSX_SHA256 = "43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d"
DEST = Path(__file__).resolve().parents[1] / "data/raw/uci-online-retail"


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    archive_path = DEST / "online-retail.zip"
    if archive_path.exists():
        archive = archive_path.read_bytes()
    else:
        with urllib.request.urlopen(URL, timeout=60) as response:
            archive = response.read(40_000_001)
    if len(archive) > 40_000_000 or hashlib.sha256(archive).hexdigest() != ZIP_SHA256:
        raise ValueError("UCI download changed; review source/version before accepting it")
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        info = zipped.getinfo("Online Retail.xlsx")
        if info.file_size > 40_000_000:
            raise ValueError("Unexpected XLSX size")
        xlsx = zipped.read(info)
    if hashlib.sha256(xlsx).hexdigest() != XLSX_SHA256:
        raise ValueError("XLSX checksum mismatch")
    archive_path.write_bytes(archive)
    (DEST / "Online Retail.xlsx").write_bytes(xlsx)
    print(json.dumps({"directory": str(DEST), "xlsx_sha256": XLSX_SHA256,
                      "attribution": "Chen, D. (2015). Online Retail. UCI. DOI:10.24432/C5BW33",
                      "license": "https://creativecommons.org/licenses/by/4.0/"}))


if __name__ == "__main__":
    main()

"""Copy only the six reviewed synthetic CSV samples into the local UI assets."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

if __name__=="__main__":
    for layout in ("standard-minor","renamed-major"):
        dest=ROOT/"services/business-api/src/main/resources/static/demo/import-samples"/layout
        dest.mkdir(parents=True,exist_ok=True)
        for table in ("orders","items","refunds"):
            source=ROOT/"examples/imports"/layout/(table+".csv")
            (dest/source.name).write_bytes(source.read_bytes())
    print("Packaged six synthetic CSV files; no external data or model calls.")

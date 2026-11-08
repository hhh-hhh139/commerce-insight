"""Pinned macOS ARM64 test proxy, installed only in this project."""
import hashlib
import json
from pathlib import Path
import platform
import tarfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.11.9"
NAME = "caddy_2.11.9_mac_arm64.tar.gz"
URL = "https://github.com/caddyserver/caddy/releases/download/v2.11.9/" + NAME
SHA512 = "166a8fdd183dc597ebe3a12d71e2e8eb313ab673ae8d3e286cb92bb0078d04233c23347543f7d7e8285b44136416e6718b0d106afe973de8ede4d81b6809c6bb"


def main():
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        raise SystemExit("This bootstrap is pinned to macOS ARM64; Linux uses the deployment image.")
    import httpx
    directory = ROOT / ".local/caddy-toolchain"; directory.mkdir(parents=True, exist_ok=True)
    archive = directory / NAME
    if not archive.exists():
        partial = directory / (NAME + ".part")
        with httpx.Client(trust_env=False, follow_redirects=True, timeout=45) as client, client.stream("GET", URL) as response:
            response.raise_for_status()
            with partial.open("wb") as output:
                for block in response.iter_bytes(): output.write(block)
        partial.rename(archive)
    if hashlib.sha512(archive.read_bytes()).hexdigest() != SHA512: raise SystemExit("Caddy archive checksum mismatch")
    binary = directory / "caddy"
    with tarfile.open(archive) as bundle:
        member = bundle.getmember("caddy")
        if not member.isfile(): raise SystemExit("Caddy archive member rejected")
        with bundle.extractfile(member) as source: binary.write_bytes(source.read())
    binary.chmod(0o700)
    (directory / "manifest.json").write_text(json.dumps({"url": URL, "sha512": SHA512,
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}, indent=2) + "\n")
    print("Project-local Caddy verified. No system service or certificate store was changed.")


if __name__ == "__main__": main()

"""Pinned public toolchain bootstrap for the verified macOS ARM64 environment."""
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = [
    {"name": "OpenJDK21U-jdk_aarch64_mac_hotspot_21.0.13_9.tar.gz", "algorithm": "sha256",
     "digest": "bf036b35b071bfb2f54f2c0af8ee8d88afa95ee6eb3dce02c0f94cfc2524d04a",
     "url": "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/OpenJDK21U-jdk_aarch64_mac_hotspot_21.0.13_9.tar.gz"},
    {"name": "apache-maven-3.9.16-bin.tar.gz", "algorithm": "sha512",
     "digest": "831a8591fe20c8243b1dbe7d71e3244f31d1665b0804b2e825e38cbbe5ce0cafb8338851f90780735568773e0a6cd07bbec107cda0b896b008b861075358b6f6",
     "url": "https://archive.apache.org/dist/maven/maven-3/3.9.16/binaries/apache-maven-3.9.16-bin.tar.gz"},
]


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("This bootstrap is pinned to macOS ARM64; other deployment environments are not yet verified.")
    if not hasattr(tarfile, "data_filter"):
        raise SystemExit("Use Python 3.12+ for safe archive extraction.")
    destination = ROOT / ".local/java-toolchain"
    downloads = destination / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    for package in PACKAGES:
        path = downloads / package["name"]
        def verified():
            return path.exists() and hashlib.new(package["algorithm"], path.read_bytes()).hexdigest() == package["digest"]
        if not verified():
            subprocess.run(["/usr/bin/curl", "--fail", "--location", "--connect-timeout", "20", "--max-time", "900",
                            "--retry", "2", "--output", str(path), package["url"]], check=True)
        if not verified():
            raise SystemExit("Checksum mismatch: " + package["name"])
        with tarfile.open(path) as archive:
            archive.extractall(destination, filter="data")
        print("Verified and extracted " + package["name"])
    (destination / "toolchain-lock.json").write_text(json.dumps(PACKAGES, indent=2) + "\n")


if __name__ == "__main__":
    main()

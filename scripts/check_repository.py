"""Check the files eligible for Git publication without printing secret values."""
import ipaddress
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_SUFFIXES = {".key", ".pem", ".crt", ".cer", ".p12", ".pfx", ".csr", ".sqlite3", ".sqlite", ".db", ".csv", ".xlsx", ".xls", ".parquet", ".h5", ".hdf5", ".zip", ".gz", ".tar"}
BLOCKED_PREFIXES = ("data/", "logs/", "reference/", "frontend/dist/", "deploy/local/", "tests/fixtures/private/", "tests/fixtures/lab/")
BLOCKED_FILES = {"config/server.json", "config/server.local.json", "config/server.production.json", "delivery-manifest.json"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    "GitHub credential": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "credential in URL": re.compile(r"https?://[^\s/:'\"]+:[^\s/@'\"]+@"),
    "personal filesystem path": re.compile(r"/(?:Users|home)/[A-Za-z][A-Za-z0-9._-]+/"),
    "stored password hash": re.compile(r"scrypt\$131072\$8\$1\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+"),
}
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def main():
    top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip()
    if Path(top).resolve() != ROOT:
        raise SystemExit("Run this check from the independent AutoEClab repository.")
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
    entries = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=ROOT).decode().split("\0")
    staged = {entry.partition("\t")[2]: entry.partition("\t")[0].split()[0] for entry in entries if entry}
    problems = []
    for name in sorted(set(filter(None, names))):
        candidate = ROOT / name
        if name in BLOCKED_FILES or name.startswith(BLOCKED_PREFIXES) or candidate.suffix.lower() in BLOCKED_SUFFIXES:
            problems.append((name, 0, "private or generated file")); continue
        if candidate.is_symlink() or staged.get(name) in ("120000", "160000"):
            problems.append((name, 0, "symlink outside the source snapshot")); continue
        versions = set()
        if name in staged:
            versions.add(subprocess.check_output(["git", "show", ":" + name], cwd=ROOT))
        if candidate.is_file():
            versions.add(candidate.read_bytes())
        for encoded in versions:
            try:
                content = encoded.decode("utf-8")
            except UnicodeError:
                problems.append((name, 0, "binary file requires explicit review")); continue
            for number, line in enumerate(content.splitlines(), 1):
                for label, pattern in PATTERNS.items():
                    if pattern.search(line): problems.append((name, number, label))
                # This module defines the network ranges rather than using a deployment address.
                if name == "scripts/check_repository.py":
                    continue
                for value in re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", line):
                    try:
                        address = ipaddress.ip_address(value)
                    except ValueError:
                        continue
                    if any(address in network for network in PRIVATE_NETWORKS):
                        problems.append((name, number, "private deployment address"))
    if problems:
        for name, line, label in problems:
            print(f"{name}:{line}: {label}")
        raise SystemExit("Repository publication check failed; no secret contents were printed.")
    print(f"Publication check passed for {len(set(filter(None, names)))} source files; no private runtime files or deployment addresses found.")


if __name__ == "__main__":
    main()

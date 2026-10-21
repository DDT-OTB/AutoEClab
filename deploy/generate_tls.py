"""Generate local-only TLS material; no certificates belong in Git."""
import argparse
import ipaddress
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description="Generate an internal CA and server certificate without overwriting existing files")
    parser.add_argument("--host", required=True, type=ipaddress.ip_address)
    parser.add_argument("--output", type=Path, default=Path("/etc/pump-console/tls"))
    args = parser.parse_args()
    host = str(args.host)
    directory = args.output.resolve()
    os.umask(0o077)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    names = ("ca.key", "ca.crt", "server.key", "server.csr", "server.crt", "server.ext", "ca.srl")
    if any((directory / name).exists() for name in names):
        raise SystemExit("TLS files already exist; refusing to replace them")

    def run(arguments):
        subprocess.run(arguments, check=True, capture_output=True)

    run(["openssl", "req", "-x509", "-newkey", "rsa:3072", "-sha256", "-nodes", "-days", "1825",
         "-subj", "/CN=AutoEClab Internal Root CA", "-keyout", str(directory / "ca.key"), "-out", str(directory / "ca.crt"),
         "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0", "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-addext", "subjectKeyIdentifier=hash"])
    run(["openssl", "req", "-new", "-newkey", "rsa:3072", "-sha256", "-nodes", "-subj", "/CN=" + host,
         "-keyout", str(directory / "server.key"), "-out", str(directory / "server.csr")])
    (directory / "server.ext").write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:" + host + "\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n")
    run(["openssl", "x509", "-req", "-in", str(directory / "server.csr"), "-CA", str(directory / "ca.crt"), "-CAkey", str(directory / "ca.key"),
         "-CAcreateserial", "-out", str(directory / "server.crt"), "-days", "365", "-sha256", "-extfile", str(directory / "server.ext")])
    run(["openssl", "verify", "-CAfile", str(directory / "ca.crt"), "-verify_ip", host, str(directory / "server.crt")])
    for name in ("ca.crt", "server.crt"):
        (directory / name).chmod(0o644)
    print("TLS material created in", directory)
    print("Distribute only the public ca.crt through your administrator's chosen channel.")


if __name__ == "__main__":
    main()

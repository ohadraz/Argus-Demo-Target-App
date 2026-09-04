"""Serving the shop, on plain HTTP and - when asked - on TLS beside it.

One process, two listeners. Everything about this service is stateful in
memory: the seeded scenario, when it was seeded, where the flags were before
anyone touched them. A second process serving the same app would answer from a
world where nothing was ever staged, so the two listeners share a loop rather
than a hostname.

The TLS one exists because a client can insist on it. PagerDuty's own SDK
refuses any base URL that is not `https://`, and the whole point of standing in
for PagerDuty is that the code reading this stand-in is the code that would
read the real thing - so the stand-in answers the scheme the client demands
rather than the client being talked out of it.

The certificate is self-signed and minted at startup, because it is a
credential for `localhost` in a demo and there is nothing about it worth
keeping between runs. Anything reading the TLS port has to be told not to
verify it. In a deployed environment none of this runs: the platform terminates
TLS with a real certificate, `TLS_PORT` is unset, and this file starts one
plain listener exactly as it always did.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import os
import tempfile
from pathlib import Path

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from target_app.app import app

# Where the shop answers. The plain port is what everything has always used;
# the TLS one is started only when it is asked for.
PORT = int(os.environ.get("PORT", "8000"))
TLS_PORT = os.environ.get("TLS_PORT")

# Bound to every interface: the alerts this service raises are posted from
# inside its own container, and a server listening only on loopback is not
# reachable from anywhere the rest of the stack lives.
HOST = "0.0.0.0"

# How long the throwaway certificate is good for. Long enough that a stack held
# open for a demo does not expire mid-sentence, short enough that it is
# obviously not meant to be kept.
_CERTIFICATE_DAYS = 30

_LOCALHOST = "localhost"


def main() -> None:
    """Starts the listeners this environment asked for."""
    asyncio.run(_serve())


async def _serve() -> None:
    servers = [_a_server_on(PORT)]

    if TLS_PORT:
        certificate, key = _a_self_signed_certificate()
        servers.append(_a_server_on(int(TLS_PORT),
                                    certificate=certificate,
                                    key=key))

    await asyncio.gather(*(server.serve() for server in servers))


def _a_server_on(port: int,
                 certificate: Path | None = None,
                 key: Path | None = None) -> uvicorn.Server:
    return uvicorn.Server(
        uvicorn.Config(app,
                       host=HOST,
                       port=port,
                       ssl_certfile=certificate,
                       ssl_keyfile=key)
    )


def _a_self_signed_certificate() -> tuple[Path, Path]:
    """A fresh certificate for `localhost`, written where the process can read it.

    Minted rather than committed: a private key in a repository is a private
    key in a repository, whatever it is for, and this one is worth nothing to
    anybody who has it. Named for both `localhost` and the loopback address,
    because a client may reach this by either.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _LOCALHOST)])
    now = dt.datetime.now(dt.UTC)

    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=_CERTIFICATE_DAYS))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName(_LOCALHOST),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1"))
            ]),
            critical=False
        )
        .sign(key, hashes.SHA256())
    )

    written_to = Path(tempfile.mkdtemp(prefix="target-app-tls-"))
    certificate_file = written_to / "certificate.pem"
    key_file = written_to / "key.pem"

    certificate_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        )
    )

    return certificate_file, key_file


if __name__ == "__main__":
    main()

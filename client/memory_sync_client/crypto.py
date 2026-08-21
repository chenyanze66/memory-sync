"""Ed25519 device keys and the signed-request canonical bytes.

All keys and signatures use standard (non-URL-safe) base64 over the raw
32-byte Ed25519 material. The canonical message signed by the device is
exactly:

    f"{METHOD.upper()}\\n{path_plus_raw_query}\\n{timestamp}\\n{nonce}\\n{sha256(raw_body).hexdigest()}"

where ``raw_body`` is the exact request body bytes (empty for bodies).
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def generate_keypair() -> tuple[str, str]:
    """Return (public_key_b64, private_key_b64) for a fresh device key."""
    private = Ed25519PrivateKey.generate()
    private_b64 = _b64(
        private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    )
    return private_to_public(private_b64), private_b64


def private_to_public(private_key_b64: str) -> str:
    """Derive the public key (standard base64) from a raw private key."""
    private = Ed25519PrivateKey.from_private_bytes(_b64decode(private_key_b64))
    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return _b64(raw)


def sign(private_key_b64: str, message: bytes) -> str:
    """Sign ``message`` with the raw device private key, standard base64."""
    private = Ed25519PrivateKey.from_private_bytes(_b64decode(private_key_b64))
    return _b64(private.sign(message))


def verify(public_key_b64: str, message: bytes, signature_b64: str) -> bool:
    """Return True when ``signature_b64`` is a valid Ed25519 signature."""
    try:
        public = Ed25519PublicKey.from_public_bytes(_b64decode(public_key_b64))
        public.verify(_b64decode(signature_b64), message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def sha256_hex(data: bytes) -> str:
    """Hex sha256 digest, used for content hashes and the canonical message."""
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(
    method: str,
    path_and_query: str,
    timestamp: int,
    nonce: str,
    raw_body: bytes,
) -> bytes:
    """Build the exact canonical bytes a device signs for a request."""
    digest = sha256_hex(raw_body)
    text = (
        f"{method.upper()}\n{path_and_query}\n{int(timestamp)}\n{nonce}\n{digest}"
    )
    return text.encode("utf-8")

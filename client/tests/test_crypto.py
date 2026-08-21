"""Canonical signing bytes, Ed25519 roundtrip, and tamper rejection."""

from memory_sync_client.crypto import (
    canonical_bytes,
    generate_keypair,
    private_to_public,
    sha256_hex,
    sign,
    verify,
)


def test_canonical_bytes_exact_vector():
    raw = b'{"operation_id": "x", "content": "hello"}'
    nonce = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    method = "get"
    path = "/v1/sync/pull?space=user-global&after_seq=3&limit=2"
    expected = (
        "GET\n"
        f"{path}\n"
        "1712345678\n"
        f"{nonce}\n"
        f"{sha256_hex(raw)}"
    ).encode("utf-8")
    assert canonical_bytes(method, path, 1712345678, nonce, raw) == expected


def test_canonical_bytes_normalizes_method_and_empty_body():
    assert canonical_bytes("post", "/v1/sync/push", 1, "n", b"") == (
        "POST\n/v1/sync/push\n1\nn\n" + sha256_hex(b"")
    ).encode("utf-8")


def test_keypair_generation_and_derivation():
    public, private = generate_keypair()
    assert public == private_to_public(private)
    assert public != private


def test_signature_roundtrip():
    public, private = generate_keypair()
    message = b"hello device"
    signature = sign(private, message)
    assert verify(public, message, signature)
    assert not verify(public, b"tampered", signature)
    assert not verify(public, message, sign(private, b"other"))
    assert not verify(public, message, "AAAA")

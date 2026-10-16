#!/usr/bin/env python3
"""Print Web Push VAPID values for manual entry into Render environment variables.

Run only on a trusted computer. Never commit the private output or share it in chat.
Requires the cryptography dependency installed through requirements.txt.
"""
from __future__ import annotations

import base64
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def main() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    private_der = key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    print("VAPID_PUBLIC_KEY=" + b64url(public_raw))
    print("VAPID_PRIVATE_KEY=" + b64url(private_der))
    print("\nGuarda VAPID_PRIVATE_KEY solo como secreto en Render. No lo subas a GitHub.")


if __name__ == "__main__":
    main()

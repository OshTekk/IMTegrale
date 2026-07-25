from __future__ import annotations

import os
import struct

from app.crypto.errors import SecretFrameError

IMT_PASSWORD_FRAME_MAGIC = b"IMTPWD\x00\x00"
IMT_PASSWORD_FRAME_VERSION = 1
IMT_PASSWORD_MAX_CHARACTERS = 512
IMT_PASSWORD_MAX_BYTES = IMT_PASSWORD_MAX_CHARACTERS * 4
IMT_PASSWORD_FRAME_SIZE = 3_072

_FRAME_HEADER = struct.Struct("!8sBBH")
_FRAME_RESERVED = 0


def encode_imt_password_frame(secret: str) -> bytes:
    if not isinstance(secret, str) or not 1 <= len(secret) <= IMT_PASSWORD_MAX_CHARACTERS:
        raise SecretFrameError
    try:
        encoded_secret = secret.encode("utf-8")
    except UnicodeEncodeError:
        raise SecretFrameError from None
    if not encoded_secret or len(encoded_secret) > IMT_PASSWORD_MAX_BYTES:
        raise SecretFrameError

    padding_length = IMT_PASSWORD_FRAME_SIZE - _FRAME_HEADER.size - len(encoded_secret)
    if padding_length < 0:
        raise SecretFrameError
    return (
        _FRAME_HEADER.pack(
            IMT_PASSWORD_FRAME_MAGIC,
            IMT_PASSWORD_FRAME_VERSION,
            _FRAME_RESERVED,
            len(encoded_secret),
        )
        + encoded_secret
        + os.urandom(padding_length)
    )


def decode_imt_password_frame(frame: bytes) -> str:
    if not isinstance(frame, bytes) or len(frame) != IMT_PASSWORD_FRAME_SIZE:
        raise SecretFrameError
    magic, version, reserved, secret_length = _FRAME_HEADER.unpack_from(frame)
    if magic != IMT_PASSWORD_FRAME_MAGIC:
        raise SecretFrameError
    if version != IMT_PASSWORD_FRAME_VERSION:
        raise SecretFrameError
    if reserved != _FRAME_RESERVED:
        raise SecretFrameError
    if not 1 <= secret_length <= IMT_PASSWORD_MAX_BYTES:
        raise SecretFrameError

    secret_bytes = frame[_FRAME_HEADER.size : _FRAME_HEADER.size + secret_length]
    if len(secret_bytes) != secret_length:
        raise SecretFrameError
    try:
        secret = secret_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise SecretFrameError from None
    if not 1 <= len(secret) <= IMT_PASSWORD_MAX_CHARACTERS:
        raise SecretFrameError
    return secret

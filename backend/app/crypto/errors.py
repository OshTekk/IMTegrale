from __future__ import annotations


class _SafeCryptoError(Exception):
    default_message = "Cryptographic operation failed."

    def __init__(self) -> None:
        super().__init__(self.default_message)


class HpkeEnvelopeError(_SafeCryptoError):
    default_message = "HPKE envelope operation failed."


class KeyMaterialError(HpkeEnvelopeError):
    default_message = "Invalid HPKE key material."


class ContextValidationError(HpkeEnvelopeError):
    default_message = "Invalid HPKE context."


class EnvelopeFormatError(HpkeEnvelopeError):
    default_message = "Invalid HPKE envelope."


class UnsupportedEnvelopeError(HpkeEnvelopeError):
    default_message = "Unsupported HPKE envelope."


class EnvelopeKeyUnavailableError(HpkeEnvelopeError):
    default_message = "Required HPKE recipient key is unavailable."


class EnvelopeAuthenticationError(HpkeEnvelopeError):
    default_message = "HPKE envelope authentication failed."


class EnvelopeEncryptionError(HpkeEnvelopeError):
    default_message = "HPKE envelope encryption failed."


class SecretFrameError(_SafeCryptoError):
    default_message = "Invalid secret frame."

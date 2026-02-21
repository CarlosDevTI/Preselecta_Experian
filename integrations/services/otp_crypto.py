import base64
import os
from typing import Tuple

class OTPCryptoError(Exception):
    pass


def _load_key() -> bytes:
    key_b64 = os.environ.get("OTP_AES_KEY_B64", "").strip()
    if not key_b64:
        raise OTPCryptoError("Falta OTP_AES_KEY_B64 para cifrado OTP.")
    try:
        key = base64.b64decode(key_b64)
    except Exception as exc:  # pragma: no cover
        raise OTPCryptoError("OTP_AES_KEY_B64 no es base64 valido.") from exc
    if len(key) != 32:
        raise OTPCryptoError("OTP_AES_KEY_B64 debe decodificar a 32 bytes (AES-256).")
    return key


def encrypt_text(plain_text: str) -> str:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:  # pragma: no cover
        raise OTPCryptoError(
            "El backend AES-256 no esta disponible. Instala 'cryptography' en el entorno."
        ) from exc
    key = _load_key()
    aes = AESGCM(key)
    nonce = os.urandom(12)
    data = (plain_text or "").encode("utf-8")
    cipher = aes.encrypt(nonce, data, None)
    token = nonce + cipher
    return base64.b64encode(token).decode("ascii")


def decrypt_text(token_b64: str) -> str:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:  # pragma: no cover
        raise OTPCryptoError(
            "El backend AES-256 no esta disponible. Instala 'cryptography' en el entorno."
        ) from exc
    key = _load_key()
    try:
        token = base64.b64decode((token_b64 or "").encode("ascii"))
    except Exception as exc:  # pragma: no cover
        raise OTPCryptoError("Ciphertext OTP invalido (base64).") from exc
    if len(token) < 13:
        raise OTPCryptoError("Ciphertext OTP invalido.")
    nonce, cipher = token[:12], token[12:]
    aes = AESGCM(key)
    try:
        plain = aes.decrypt(nonce, cipher, None)
    except Exception as exc:  # pragma: no cover
        raise OTPCryptoError("No fue posible descifrar OTP.") from exc
    return plain.decode("utf-8")


def can_decrypt(token_b64: str) -> Tuple[bool, str]:
    try:
        value = decrypt_text(token_b64)
        return True, value
    except Exception:
        return False, ""

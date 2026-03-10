"""Tests for chat encryption (AES-256-GCM key derivation, encrypt, decrypt)."""

import pytest
from chat import derive_key, encrypt_payload, decrypt_payload


class TestKeyDerivation:
    def test_deterministic(self):
        """Same passphrase always produces same key."""
        k1 = derive_key("secret")
        k2 = derive_key("secret")
        assert k1 == k2

    def test_different_passphrases(self):
        k1 = derive_key("secret")
        k2 = derive_key("other-secret")
        assert k1 != k2

    def test_key_length(self):
        """AES-256 key must be 32 bytes."""
        k = derive_key("test")
        assert len(k) == 32


class TestEncryptDecrypt:
    def test_roundtrip(self):
        key = derive_key("passphrase")
        plaintext = b"hello LoRa"
        ct = encrypt_payload(key, plaintext)
        result = decrypt_payload(key, ct)
        assert result == plaintext

    def test_wrong_key_fails(self):
        key1 = derive_key("correct")
        key2 = derive_key("wrong")
        ct = encrypt_payload(key1, b"secret message")
        result = decrypt_payload(key2, ct)
        assert result is None

    def test_corrupted_ciphertext_fails(self):
        key = derive_key("passphrase")
        ct = encrypt_payload(key, b"data")
        corrupted = ct[:-1] + bytes([(ct[-1] + 1) % 256])
        result = decrypt_payload(key, corrupted)
        assert result is None

    def test_too_short_returns_none(self):
        key = derive_key("passphrase")
        assert decrypt_payload(key, b"") is None
        assert decrypt_payload(key, b"short") is None

    def test_nonce_is_random(self):
        """Each encryption should produce different ciphertext (random nonce)."""
        key = derive_key("passphrase")
        ct1 = encrypt_payload(key, b"same")
        ct2 = encrypt_payload(key, b"same")
        assert ct1 != ct2

    def test_empty_plaintext(self):
        key = derive_key("passphrase")
        ct = encrypt_payload(key, b"")
        result = decrypt_payload(key, ct)
        assert result == b""

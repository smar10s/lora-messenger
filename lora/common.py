"""Shared LoRa PHY primitives used by both modulator and demodulator."""

import numpy as np


def upchirp_os(N, os, symbol=0):
    """Oversampled upchirp (N*os samples)."""
    sps = N * os
    n = np.arange(sps, dtype=np.float64)
    k = n / os                           # fractional chip index
    n_fold = (N - symbol) * os
    phase = np.where(
        n < n_fold,
        2 * np.pi * (k * k / (2 * N) + (symbol / N - 0.5) * k),
        2 * np.pi * (k * k / (2 * N) + (symbol / N - 1.5) * k),
    )
    return np.exp(1j * phase).astype(np.complex64)


def bits_msb(val, n):
    return [(val >> (n - 1 - i)) & 1 for i in range(n)]


def int_msb(bits):
    r = 0
    for b in bits:
        r = (r << 1) + b
    return r


WHITENING = bytes([
    0xFF, 0xFE, 0xFC, 0xF8, 0xF0, 0xE1, 0xC2, 0x85,
    0x0B, 0x17, 0x2F, 0x5E, 0xBC, 0x78, 0xF1, 0xE3,
    0xC6, 0x8D, 0x1A, 0x34, 0x68, 0xD0, 0xA0, 0x40,
    0x80, 0x01, 0x02, 0x04, 0x08, 0x11, 0x23, 0x47,
    0x8E, 0x1C, 0x38, 0x71, 0xE2, 0xC4, 0x89, 0x12,
    0x25, 0x4B, 0x97, 0x2E, 0x5C, 0xB8, 0x70, 0xE0,
    0xC0, 0x81, 0x03, 0x06, 0x0C, 0x19, 0x32, 0x64,
    0xC9, 0x92, 0x24, 0x49, 0x93, 0x26, 0x4D, 0x9B,
])


def crc16(data):
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc

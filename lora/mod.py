#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""LoRa CSS modulator — encode payloads to IQ waveforms.

Generates spec-compliant LoRa CSS packets in NumPy.
Developed independently through hardware experimentation with SX1262
transceivers. Implementation details for whitening, CRC, and header
encoding were informed by gr-lora_sdr (Tapparel et al., EPFL, GPL-3.0):
https://github.com/tapparelj/gr-lora_sdr

Usage:
    from lora.mod import modulate
    iq = modulate(b"Hello")
"""

import numpy as np
from lora.demod import LoRaParams
from lora.common import WHITENING as _WHITENING, crc16 as _crc16, upchirp_os as _upchirp_os, bits_msb as _bits_msb, int_msb as _int_msb


# ---------------------------------------------------------------------------
# FEC encode (inverses of lora_demod decode helpers)
# ---------------------------------------------------------------------------

def _gray_encode(x):
    """Encode for LoRa Gray coding step.

    The demod's _gray_decode does x ^ (x >> 1), which is standard
    binary-to-Gray. The inverse is the iterative Gray-to-binary
    conversion. Our modulator applies this so the demod can undo it.
    """
    b = x
    mask = x >> 1
    while mask:
        b ^= mask
        mask >>= 1
    return b


def _hamming_encode(nibble, cr):
    """Encode 4-bit nibble to (cr+4)-bit Hamming codeword.

    Inverse of _hamming_decode in lora_demod.py.

    The demod reads bits MSB-first and extracts data as:
        data = [bits[3], bits[2], bits[1], bits[0]]
    So bits[0] = LSB of nibble, bits[3] = MSB of nibble.
    Parity bits go in bits[4..].
    """
    # bits[0..3]: data in LSB-first order (reversed from nibble MSB)
    bits = [(nibble >> i) & 1 for i in range(4)]
    # Parity from demod's syndrome equations:
    #   s0 = bits[0] ^ bits[1] ^ bits[2] ^ bits[4]  => p0 = bits[0]^bits[1]^bits[2]
    #   s1 = bits[1] ^ bits[2] ^ bits[3] ^ bits[5]  => p1 = bits[1]^bits[2]^bits[3]
    #   s2 = bits[0] ^ bits[1] ^ bits[3] ^ bits[6]  => p2 = bits[0]^bits[1]^bits[3]
    p0 = bits[0] ^ bits[1] ^ bits[2]
    p1 = bits[1] ^ bits[2] ^ bits[3]
    p2 = bits[0] ^ bits[1] ^ bits[3]
    if cr == 1:
        p = bits[0] ^ bits[1] ^ bits[2] ^ bits[3]
        all_bits = bits + [p]
    elif cr == 2:
        all_bits = bits + [p0, p1]
    elif cr == 3:
        all_bits = bits + [p0, p1, p2]
    elif cr == 4:
        p3 = bits[0] ^ bits[1] ^ bits[2] ^ bits[3] ^ p0 ^ p1 ^ p2
        all_bits = bits + [p0, p1, p2, p3]
    else:
        raise ValueError(f"Unsupported CR: {cr}")
    # Pack MSB-first (bits[0] is MSB of the codeword integer)
    val = 0
    for b in all_bits:
        val = (val << 1) | b
    return val


def _interleave(codewords, sf_app, cr_app):
    """Interleave codewords into symbols.

    Inverse of _deinterleave in lora_demod.py.
    Input: sf_app codewords, each (cr_app+4) bits wide.
    Output: (cr_app+4) symbols, each sf_app bits wide.
    """
    cw_len = cr_app + 4
    # The demod's deinterleave does:
    #   deinter[(i - j - 1) % sf_app][i] = inter[i][j]
    # We have codewords (= deinter rows). We need symbols (= inter rows).
    # So: inter[i][j] = deinter[(i - j - 1) % sf_app][i]
    deinter = [_bits_msb(cw, cw_len) for cw in codewords]
    symbols = []
    for i in range(cw_len):
        bits = []
        for j in range(sf_app):
            bits.append(deinter[(i - j - 1) % sf_app][i])
        symbols.append(_int_msb(bits))
    return symbols


def _whiten(payload_bytes, has_crc):
    """Whiten payload (and CRC) into nibble stream.

    Inverse of _dewhiten in lora_demod.py.

    LoRa CRC is:
    1. CRC-16/CCITT over first (len-2) payload bytes
    2. XOR with payload[-1] | (payload[-2] << 8)
    3. Stored little-endian: [crc_lo, crc_hi]
    """
    data = list(payload_bytes)
    if has_crc:
        if len(payload_bytes) >= 2:
            crc = _crc16(payload_bytes[:-2])
            crc ^= payload_bytes[-1] | (payload_bytes[-2] << 8)
        else:
            crc = _crc16(payload_bytes)
        data.append(crc & 0xFF)          # CRC lo byte first
        data.append((crc >> 8) & 0xFF)   # CRC hi byte second

    nibbles = []
    for i, byte_val in enumerate(data):
        hi = (byte_val >> 4) & 0x0F
        lo = byte_val & 0x0F
        if i < len(payload_bytes) and i < len(_WHITENING):
            lo ^= _WHITENING[i] & 0x0F
            hi ^= (_WHITENING[i] >> 4) & 0x0F
        nibbles.append(lo)
        nibbles.append(hi)
    return nibbles


# ---------------------------------------------------------------------------
# Header & data encoding
# ---------------------------------------------------------------------------

def _encode_header(payload_len, cr, has_crc, p):
    """Encode the 8-symbol header at reduced rate (SF-2).

    Header is always encoded at CR 4/8.
    """
    sf_h = p.sf - 2
    cr_h = 4  # header always CR 4/8

    nibs = [0] * 5
    nibs[0] = (payload_len >> 4) & 0x0F
    nibs[1] = payload_len & 0x0F
    nibs[2] = ((cr & 7) << 1) | int(has_crc)
    # Header checksum (matches gr-lora_sdr header_impl.cc)
    c4 = ((nibs[0]>>3)&1) ^ ((nibs[0]>>2)&1) ^ ((nibs[0]>>1)&1) ^ (nibs[0]&1)
    c3 = ((nibs[0]>>3)&1) ^ ((nibs[1]>>3)&1) ^ ((nibs[1]>>2)&1) ^ ((nibs[1]>>1)&1) ^ (nibs[2]&1)
    c2 = ((nibs[0]>>2)&1) ^ ((nibs[1]>>3)&1) ^ (nibs[1]&1) ^ ((nibs[2]>>3)&1) ^ ((nibs[2]>>1)&1)
    c1 = ((nibs[0]>>1)&1) ^ ((nibs[1]>>2)&1) ^ (nibs[1]&1) ^ ((nibs[2]>>2)&1) ^ ((nibs[2]>>1)&1) ^ (nibs[2]&1)
    c0 = (nibs[0]&1) ^ ((nibs[1]>>1)&1) ^ ((nibs[2]>>3)&1) ^ ((nibs[2]>>2)&1) ^ ((nibs[2]>>1)&1) ^ (nibs[2]&1)
    nibs[3] = c4 & 1
    nibs[4] = ((c3&1)<<3) | ((c2&1)<<2) | ((c1&1)<<1) | (c0&1)

    codewords = [_hamming_encode(n, cr_h) for n in nibs]
    # Pad to sf_h codewords
    while len(codewords) < sf_h:
        codewords.append(_hamming_encode(0, cr_h))

    symbols = _interleave(codewords[:sf_h], sf_h, cr_h)
    symbols = [_gray_encode(s) for s in symbols]
    # Scale to full N bins (header uses SF-2, so multiply by 4)
    symbols = [(s * 4) % p.N for s in symbols]
    return symbols


def _encode_data(payload, p, cr, has_crc):
    """Encode payload data symbols at full SF."""
    nibbles = _whiten(payload, has_crc)

    codewords = [_hamming_encode(n, cr) for n in nibbles]

    # Pad to multiple of sf codewords
    while len(codewords) % p.sf != 0:
        codewords.append(_hamming_encode(0, cr))

    # Interleave in blocks of sf codewords -> (cr+4) symbols each
    symbols = []
    for blk in range(0, len(codewords), p.sf):
        chunk = codewords[blk:blk + p.sf]
        if len(chunk) < p.sf:
            break
        symbols.extend(_interleave(chunk, p.sf, cr))

    symbols = [_gray_encode(s) for s in symbols]
    return symbols


# ---------------------------------------------------------------------------
# Chirp modulation & packet assembly
# ---------------------------------------------------------------------------

def modulate(payload, params=None):
    """Encode payload bytes into a LoRa IQ waveform.

    Args:
        payload: bytes to transmit
        params: LoRaParams (defaults match demod defaults)

    Returns:
        np.complex64 array of IQ samples at params.fs
    """
    if params is None:
        params = LoRaParams()

    N, os, sps = params.N, params.os, params.sps

    # --- Preamble: 8 unmodulated upchirps ---
    preamble = np.concatenate([_upchirp_os(N, os, symbol=0)
                               for _ in range(params.preamble_len)])

    # --- Sync word: 2 upchirps encoding sync_word nibbles ---
    sw_hi = ((params.sync_word >> 4) & 0xF) * 8
    sw_lo = (params.sync_word & 0xF) * 8
    sync = np.concatenate([
        _upchirp_os(N, os, symbol=sw_hi),
        _upchirp_os(N, os, symbol=sw_lo),
    ])

    # --- SFD: 2.25 downchirps ---
    downchirp = np.conj(_upchirp_os(N, os, symbol=0))
    sfd = np.concatenate([downchirp, downchirp, downchirp[:sps // 4]])

    # --- Header + data symbols -> chirps ---
    header_syms = _encode_header(len(payload), params.cr, params.has_crc, params)
    data_syms = _encode_data(payload, params, params.cr, params.has_crc)

    chirps = []
    for sym in header_syms + data_syms:
        chirps.append(_upchirp_os(N, os, symbol=sym))

    # --- Assemble with silence padding ---
    silence = np.zeros(sps * 4, dtype=np.complex64)
    packet = np.concatenate([silence, preamble, sync, sfd] + chirps + [silence])

    return packet


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    payload = sys.argv[1].encode() if len(sys.argv) > 1 else b"Hello"
    params = LoRaParams(sf=7, bw=125e3, cr=1, fs=1e6)

    print(f"Modulating: \"{payload.decode()}\"")
    print(f"Params: SF={params.sf}, BW={params.bw/1e3:.0f}kHz, "
          f"CR=4/{params.cr+4}, Fs={params.fs/1e6:.0f}MHz")

    iq = modulate(payload, params)
    print(f"Generated {len(iq)} samples ({len(iq)/params.fs*1000:.1f} ms)")

    out = "modulated.npy"
    np.save(out, iq)
    print(f"Saved to {out}")

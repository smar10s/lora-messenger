#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""LoRa CSS demodulator — decode IQ captures to packets.

Decodes LoRa CSS (Chirp Spread Spectrum) packets with known parameters.
Developed independently through hardware experimentation with SX1262
transceivers. Implementation details for whitening, CRC, and header
encoding were informed by gr-lora_sdr (Tapparel et al., EPFL, GPL-3.0):
https://github.com/tapparelj/gr-lora_sdr

Usage:
    ./run tools/demod.py [capture.npy]
"""

import numpy as np
from dataclasses import dataclass
from lora.common import WHITENING, crc16, upchirp_os, bits_msb, int_msb

_WHITENING = WHITENING
_crc16 = crc16
_upchirp_os = upchirp_os
_bits_msb = bits_msb
_int_msb = int_msb


@dataclass
class LoRaParams:
    sf: int = 7              # spreading factor (7-12)
    bw: float = 125e3        # bandwidth in Hz
    cr: int = 1              # coding rate (1=4/5, 2=4/6, 3=4/7, 4=4/8)
    fs: float = 1e6          # capture sample rate
    preamble_len: int = 8    # preamble upchirps
    sync_word: int = 0x12    # LoRa sync word
    implicit_header: bool = False
    has_crc: bool = True

    @property
    def N(self):
        return 1 << self.sf

    @property
    def os(self):
        return int(self.fs / self.bw)

    @property
    def sps(self):
        """Samples per symbol at capture rate."""
        return self.N * self.os


# ---------------------------------------------------------------------------
# Chirp generation (matches gr-lora_sdr build_upchirp)
# ---------------------------------------------------------------------------

def _upchirp_cr(N, symbol=0):
    """Chip-rate upchirp (N samples, no oversampling).

    Used as the dechirp reference to avoid ±1 bin errors that occur when
    the oversampled reference's frequency-wrap phase coefficient (C1 vs C2)
    differs across the fold boundary.
    """
    n = np.arange(N, dtype=np.float64)
    n_fold = N - symbol
    phase = np.where(
        n < n_fold,
        2 * np.pi * (n * n / (2 * N) + (symbol / N - 0.5) * n),
        2 * np.pi * (n * n / (2 * N) + (symbol / N - 1.5) * n),
    )
    return np.exp(1j * phase).astype(np.complex64)


# ---------------------------------------------------------------------------
# Dechirp + FFT (fold to chip rate, chip-rate reference)
# ---------------------------------------------------------------------------

def _dechirp(seg, downchirp, N, os):
    """Dechirp one symbol: fold oversampled input to chip rate, multiply
    by chip-rate downchirp, FFT.

    The downchirp parameter is a chip-rate conjugate upchirp (N samples).
    Folding the oversampled input first preserves SNR (sums os samples per
    chip) and avoids the ±1 bin errors caused by mismatched phase
    coefficients across the frequency-wrap boundary in oversampled
    fold+multiply approaches.
    """
    sps = N * os
    folded = seg[:sps].reshape(N, os).sum(axis=1)
    mixed = folded * downchirp
    spec = np.abs(np.fft.fft(mixed))
    sym = int(np.argmax(spec))
    return sym, float(spec[sym])


def _dechirp_os(seg, downchirp_os, N, os):
    """Dechirp using oversampled downchirp — tolerant of sub-sample offsets.

    Used for preamble detection and alignment where ±1 bin error tolerance
    is acceptable.  The 'downchirp_os' is N*os samples (oversampled).
    """
    sps = N * os
    mixed = seg[:sps] * downchirp_os
    folded = mixed.reshape(N, os).sum(axis=1)
    spec = np.abs(np.fft.fft(folded))
    sym = int(np.argmax(spec))
    return sym, float(spec[sym])


# ---------------------------------------------------------------------------
# Preamble detection
# ---------------------------------------------------------------------------

def _find_preambles(iq, p):
    """Coarse preamble search: scan at symbol-slot boundaries."""
    N, os, sps = p.N, p.os, p.sps
    dc = np.conj(_upchirp_os(N, os, symbol=0))  # oversampled for alignment

    n_slots = len(iq) // sps
    syms = np.zeros(n_slots, dtype=int)
    mags = np.zeros(n_slots)
    for i in range(n_slots):
        syms[i], mags[i] = _dechirp_os(iq[i * sps:(i + 1) * sps], dc, N, os)

    # Use 25th percentile as noise estimate (robust even when most slots are signal)
    noise = np.percentile(mags, 25)
    max_mag = np.max(mags) if np.max(mags) > 0 else 1
    # Threshold must stay well below the preamble dechirp magnitude (N*os for unit-amplitude).
    # Use the lower of: noise-based estimate, or half the capture's peak magnitude.
    thresh = min(noise * 5, max_mag * 0.5)
    thresh = max(thresh, max_mag * 0.05)  # but above the noise floor
    candidates = []
    run_start = None
    run_sym = None

    for i in range(n_slots):
        if mags[i] <= thresh:
            # Slot below threshold: end any current run, skip
            if run_start is not None and (i - run_start) >= 6:
                from collections import Counter
                cfo = Counter(syms[run_start:i]).most_common(1)[0][0]
                candidates.append((run_start * sps, int(cfo)))
            run_start = None
            run_sym = None
            continue
        if run_start is None:
            # Start new run at first above-threshold slot
            run_start = i
            run_sym = syms[i]
            continue
        same = abs(syms[i] - run_sym) <= 1 or abs(syms[i] - run_sym) >= N - 1
        if same:
            continue
        # Symbol changed: end run, start new one
        if (i - run_start) >= 6:
            from collections import Counter
            cfo = Counter(syms[run_start:i]).most_common(1)[0][0]
            candidates.append((run_start * sps, int(cfo)))
        run_start = i
        run_sym = syms[i]

    # Check final run
    if run_start is not None and (n_slots - run_start) >= 6:
        from collections import Counter
        cfo = Counter(syms[run_start:n_slots]).most_common(1)[0][0]
        candidates.append((run_start * sps, int(cfo)))

    return candidates


def _align_preamble(iq, coarse, p):
    """Fine-align preamble to single-sample precision.

    Returns (sample_offset, cfo_bin) where cfo_bin is the preamble
    dechirp peak bin (used to build CFO-corrected downchirp).
    """
    N, os, sps = p.N, p.os, p.sps
    dc = np.conj(_upchirp_os(N, os, symbol=0))  # oversampled for alignment

    # Coarse pass: step by os, require all 8 preamble symbols same bin
    lo = max(0, coarse - sps)
    hi = min(len(iq) - 8 * sps, coarse + sps)
    best, best_s, best_cfo = coarse, 0, 0

    for off in range(lo, hi, os):
        s = 0
        first = None
        ok = True
        for k in range(8):
            pos = off + k * sps
            if pos + sps > len(iq):
                ok = False
                break
            sym, mag = _dechirp_os(iq[pos:pos + sps], dc, N, os)
            s += mag
            if first is None:
                first = sym
            elif sym != first:
                ok = False
        if ok and s > best_s:
            best_s, best, best_cfo = s, off, first

    # Fine pass: step by 1 around best
    for off in range(max(0, best - os), min(len(iq) - 8 * sps, best + os)):
        s = 0
        for k in range(8):
            _, mag = _dechirp_os(iq[off + k * sps:off + (k + 1) * sps], dc, N, os)
            s += mag
        if s > best_s:
            best_s, best = s, off

    # Re-determine CFO at best offset (use mode of all 8 preamble bins)
    from collections import Counter
    preamble_syms = []
    for k in range(8):
        sym, _ = _dechirp_os(iq[best + k * sps:best + (k + 1) * sps], dc, N, os)
        preamble_syms.append(sym)
    cfo = Counter(preamble_syms).most_common(1)[0][0]

    # Sub-bin CFO estimation using zero-padded FFT on chip-rate dechirp.
    # This is used ONLY for time-domain frequency correction in demodulate(),
    # not for alignment or data start finding (which use the integer cfo).
    ZP_FACTOR = 16  # 16x zero-padding -> 1/16 bin resolution
    dc_cr_ref = np.conj(_upchirp_cr(N, symbol=0))
    frac_cfos = []
    for k in range(8):
        seg = iq[best + k * sps:best + (k + 1) * sps]
        if len(seg) < sps:
            continue
        folded = seg[:sps].reshape(N, os).sum(axis=1)
        spec = np.abs(np.fft.fft(folded * dc_cr_ref, n=N * ZP_FACTOR))
        peak_idx = int(np.argmax(spec))
        frac_cfos.append(peak_idx / ZP_FACTOR)

    if frac_cfos:
        ref = frac_cfos[0]
        unwrapped = []
        for v in frac_cfos:
            diff = v - ref
            if diff > N / 2:
                v -= N
            elif diff < -N / 2:
                v += N
            unwrapped.append(v)
        cfo_frac = float(np.mean(unwrapped))
    else:
        cfo_frac = float(cfo)

    return best, int(cfo), cfo_frac


# ---------------------------------------------------------------------------
# Data start alignment (after SFD)
# ---------------------------------------------------------------------------

def _find_data_start(iq, preamble, cfo, p):
    """Find data start by maximising dechirp quality with header validation.

    Searches near 12.25 symbols after preamble, picks the offset that
    produces the cleanest dechirp spectra and a valid header.
    """
    N, os, sps = p.N, p.os, p.sps
    dc_cr = np.conj(_upchirp_cr(N, symbol=cfo))
    nominal = preamble + int(12.25 * sps)

    def _score_offset(off, n_syms=18):
        """Score an offset by peak-to-mean ratio of chip-rate dechirp."""
        sc = 0
        for i in range(n_syms):
            pos = off + i * sps
            if pos + sps > len(iq):
                return 0
            folded = iq[pos:pos + sps].reshape(N, os).sum(axis=1)
            spec = np.abs(np.fft.fft(folded * dc_cr))
            peak = float(np.max(spec))
            mean = float(np.mean(spec))
            if mean > 0:
                sc += peak / mean
        return sc

    # Coarse search: step by os, require valid header via chip-rate dechirp
    best_off, best_sc = nominal, 0
    for off in range(nominal - sps // 2, nominal + sps // 2, os):
        bins = []
        for i in range(8):
            pos = off + i * sps
            if pos + sps > len(iq):
                break
            sym, _ = _dechirp(iq[pos:pos + sps], dc_cr, N, os)
            bins.append(sym)
        if len(bins) < 8:
            continue
        hdr = _decode_header(bins, p)
        if not (0 < hdr["payload_len"] <= 255 and 1 <= hdr["cr"] <= 4):
            continue
        sc = _score_offset(off)
        if sc > best_sc:
            best_sc, best_off = sc, off

    # Fine search around best
    for off in range(best_off - os, best_off + os):
        sc = _score_offset(off)
        if sc > best_sc:
            best_sc, best_off = sc, off

    return best_off


# ---------------------------------------------------------------------------
# FEC / coding helpers
# ---------------------------------------------------------------------------

def _gray_decode(x):
    return x ^ (x >> 1)


def _deinterleave(symbols, sf_app, cr_app):
    cw_len = cr_app + 4
    inter = [_bits_msb(s, sf_app) for s in symbols[:cw_len]]
    deinter = [[0] * cw_len for _ in range(sf_app)]
    for i in range(cw_len):
        for j in range(sf_app):
            deinter[(i - j - 1) % sf_app][i] = inter[i][j]
    return [_int_msb(row) for row in deinter]


def _hamming_decode(cw, cr_app):
    cw_len = cr_app + 4
    bits = _bits_msb(cw, cw_len)
    data = [bits[3], bits[2], bits[1], bits[0]]
    if cr_app >= 3:
        s0 = bits[0] ^ bits[1] ^ bits[2] ^ bits[4]
        s1 = bits[1] ^ bits[2] ^ bits[3] ^ bits[5]
        s2 = bits[0] ^ bits[1] ^ bits[3] ^ bits[6]
        syn = s0 + (s1 << 1) + (s2 << 2)
        if cr_app == 4 and sum(bits) % 2 == 0:
            syn = 0
        if syn == 5:   data[3] ^= 1
        elif syn == 7: data[2] ^= 1
        elif syn == 3: data[1] ^= 1
        elif syn == 6: data[0] ^= 1
    return _int_msb(data)


def _dewhiten(nibbles, payload_len):
    out = []
    for i in range(0, len(nibbles) - 1, 2):
        byte_idx = i // 2
        lo, hi = nibbles[i], nibbles[i + 1]
        if byte_idx < payload_len and byte_idx < len(_WHITENING):
            lo ^= _WHITENING[byte_idx] & 0x0F
            hi ^= (_WHITENING[byte_idx] >> 4) & 0x0F
        out.append((hi << 4) | lo)
    return out


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------

def _extract_symbols(iq, start, count, downchirp, p):
    N, os, sps = p.N, p.os, p.sps
    result = []
    for i in range(count):
        pos = start + i * sps
        if pos + sps > len(iq):
            break
        sym, mag = _dechirp(iq[pos:pos + sps], downchirp, N, os)
        result.append((sym, mag))
    return result


# ---------------------------------------------------------------------------
# Decode pipeline
# ---------------------------------------------------------------------------

def _decode_header(bins, p):
    sf_h = p.sf - 2
    N_h = 1 << sf_h
    # Header symbols are at reduced rate: divide by 4 to get SF-2 bit values,
    # then mask to N_h range. Division before masking preserves the full range.
    reduced = [(b // 4) % N_h for b in bins[:8]]
    grayed = [_gray_decode(r) for r in reduced]
    cws = _deinterleave(grayed, sf_h, 4)
    nibs = [_hamming_decode(cw, 4) for cw in cws]
    return {
        "payload_len": (nibs[0] << 4) | nibs[1],
        "cr": (nibs[2] >> 1) & 7,
        "has_crc": bool(nibs[2] & 1),
        "header_nibbles": nibs,
    }


def _decode_data(bins, p, payload_len, cr, has_crc, shift=1):
    shifted = [(b + shift) % p.N for b in bins]
    grayed = [_gray_decode(s) for s in shifted]
    cw_len = cr + 4
    all_cw = []
    for blk in range(0, len(grayed), cw_len):
        chunk = grayed[blk:blk + cw_len]
        if len(chunk) < cw_len:
            break
        all_cw.extend(_deinterleave(chunk, p.sf, cr))
    nibs = [_hamming_decode(cw, cr) for cw in all_cw]
    n_payload_nibs = payload_len * 2
    n_crc_nibs = 4 if has_crc else 0
    total = n_payload_nibs + n_crc_nibs
    if len(nibs) < total:
        return {"error": f"Need {total} nibbles, got {len(nibs)}"}
    data_bytes = _dewhiten(nibs[:total], payload_len)
    result = {"data_bytes": data_bytes, "data_nibbles": nibs[:total]}
    if len(data_bytes) >= payload_len:
        payload = bytes(data_bytes[:payload_len])
        result["payload"] = payload
        if has_crc and len(data_bytes) >= payload_len + 2:
            crc_rx = data_bytes[payload_len] | (data_bytes[payload_len + 1] << 8)
            if payload_len >= 2:
                crc_calc = _crc16(payload[:-2])
                crc_calc ^= payload[-1] | (payload[-2] << 8)
            else:
                crc_calc = _crc16(payload)
            result["crc_rx"] = crc_rx
            result["crc_calc"] = crc_calc
            result["crc_ok"] = crc_rx == crc_calc
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def demodulate(iq, params=None, verbose=True):
    """Demodulate LoRa packet(s) from raw IQ capture.

    Args:
        iq: complex64 numpy array (captured at params.fs)
        params: LoRaParams (defaults: SF7/BW125k/CR4-5)
        verbose: if True, print progress/status messages

    Returns:
        list of dicts, each with 'payload' (bytes), 'crc_ok', etc.
    """
    if params is None:
        params = LoRaParams()

    N, os, sps = params.N, params.os, params.sps
    results = []

    preambles = _find_preambles(iq, params)
    if verbose:
        print(f"Found {len(preambles)} preamble(s)")

    for idx, (coarse, coarse_cfo) in enumerate(preambles):
        if verbose:
            print(f"\n--- Packet {idx} ---")
        preamble_start, cfo, cfo_frac = _align_preamble(iq, coarse, params)
        if verbose:
            print(f"Preamble @ sample {preamble_start} (t={preamble_start/params.fs:.4f}s), CFO={cfo} (sub-bin: {cfo_frac:.2f})")

        data_start = _find_data_start(iq, preamble_start, cfo, params)
        if verbose:
            print(f"Data @ ~sample {data_start} ({(data_start-preamble_start)/sps:.2f} symbols)")

        # Apply time-domain CFO correction using the sub-bin estimate from
        # the preamble. This is more precise than integer-bin correction,
        # reducing the residual that the cfo_residual sweep must cover.
        freq_offset = cfo_frac * params.bw / N
        # Extract enough of the packet: preamble through end of data
        pkt_len = data_start - preamble_start + 200 * sps  # enough for max LoRa payload
        pkt_end = min(preamble_start + pkt_len, len(iq))
        t = np.arange(preamble_start, pkt_end) / params.fs
        iq_pkt = iq[preamble_start:pkt_end].copy()
        iq_pkt *= np.exp(-2j * np.pi * freq_offset * t).astype(np.complex64)
        ds_rel = data_start - preamble_start  # data start relative to preamble

        dc_base = np.conj(_upchirp_cr(N, symbol=0))

        best_result = None
        best_hdr = None
        best_cfo_used = cfo
        best_total_score = 0
        _done = False

        # Sweep residual CFO (±1 bin around time-domain correction)
        for cfo_residual in [0, 1, -1]:
            if _done:
                break
            dc_cfo = np.conj(_upchirp_cr(N, symbol=cfo_residual % N))
            for ds_off in range(-os * 2, os * 2 + 1):
                if _done:
                    break
                ds_try = ds_rel + ds_off

                # Extract header and data symbols using oversampled dechirp
                all_bins = []
                for i in range(200):  # enough for max LoRa payload
                    pos = ds_try + i * sps
                    if pos + sps > len(iq_pkt):
                        break
                    sym, mag = _dechirp(iq_pkt[pos:pos + sps], dc_cfo, N, os)
                    all_bins.append(sym)

                if len(all_bins) < 8:
                    continue

                hdr = _decode_header(all_bins[:8], params)
                pl, cr_h, has_crc = hdr["payload_len"], hdr["cr"], hdr["has_crc"]
                if not (0 < pl <= 255 and 1 <= cr_h <= 4):
                    continue

                cr_d = cr_h if cr_h > 0 else 1
                de = 0
                num = 8 * pl - 4 * params.sf + 28 + 16 * int(has_crc) - 20 * int(params.implicit_header)
                den = 4 * (params.sf - 2 * de)
                n_data_syms = max(int(np.ceil(num / den)) * (cr_d + 4), 0)
                data_bins = all_bins[8:8 + n_data_syms]

                for shift in [0, 1, -1, 2, -2, 3, -3]:
                    res = _decode_data(data_bins, params, pl, cr_d, has_crc, shift)
                    if "payload" not in res:
                        continue

                    score = 0
                    if res.get("crc_ok"):
                        score = 100
                    try:
                        text = res["payload"].decode("ascii")
                        score += sum(1 for c in text if c.isprintable())
                    except Exception:
                        pass

                    if best_result is None or score > best_result.get("_score", 0):
                        res["_score"] = score
                        best_result = res
                        best_hdr = hdr
                        best_cfo_used = cfo_frac

                    if score >= 100:
                        _done = True
                        break

        if best_hdr is None:
            dc_cfo = np.conj(_upchirp_cr(N, symbol=cfo))
            raw = _extract_symbols(iq, data_start, 50, dc_cfo, params)
            header_bins = [s for s, _ in raw[:8]]
            try:
                best_hdr = _decode_header(header_bins, params)
            except (IndexError, ValueError):
                best_hdr = {"payload_len": 0, "cr": 0, "has_crc": False}
        if best_result is None:
            best_result = {"error": "decode failed"}

        pl = best_hdr["payload_len"]
        cr_h = best_hdr["cr"]
        has_crc = best_hdr["has_crc"]
        if verbose:
            print(f"Header: payload_len={pl}, CR=4/{cr_h+4}, CRC={has_crc} (CFO={best_cfo_used})")

        if pl > 255 or cr_h > 4 or cr_h < 1:
            if verbose:
                print(f"  Invalid header, skipping")
            results.append({"error": "bad header", **best_hdr})
            continue

        best_result.pop("_score", None)
        result = {**best_hdr, **best_result, "cfo": best_cfo_used, "preamble": preamble_start}
        results.append(result)

        if "payload" in result:
            try:
                text = result["payload"].decode("ascii", errors="replace")
            except Exception:
                text = result["payload"].hex()
            crc_str = ""
            if "crc_ok" in result:
                crc_str = f" CRC={'OK' if result['crc_ok'] else 'FAIL'}"
            if verbose:
                print(f"Payload: \"{text}\"{crc_str}")
        elif "error" in result:
            if verbose:
                print(f"Error: {result['error']}")

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    capture = sys.argv[1] if len(sys.argv) > 1 else "capture.npy"
    print(f"Loading {capture}...")
    iq = np.load(capture)
    print(f"{len(iq)} samples, {iq.dtype}")

    params = LoRaParams(sf=7, bw=125e3, cr=1, fs=1e6)
    results = demodulate(iq, params)

    print("\n" + "=" * 50)
    decoded = [r for r in results if "payload" in r]
    for r in decoded:
        text = r["payload"].decode("ascii", errors="replace")
        print(f"  \"{text}\"")
    if not decoded:
        print("  No packets decoded.")

# SPDX-License-Identifier: MIT
"""LoRa PHY — pure-Python CSS modulation and demodulation.

Software LoRa physical layer using NumPy. Encode payloads to IQ
waveforms and decode IQ captures back to bytes.

    from lora.mod import modulate
    from lora.demod import demodulate, LoRaParams
"""

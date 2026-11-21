# SPDX-License-Identifier: GPL-3.0-or-later
"""Opus encoding through the system's libopus (ctypes, no Python dependency).

Only plain function calls cross into C; nothing calls back into Python, so
this works under the server unit's ``MemoryDenyWriteExecute=yes``.
"""

import ctypes
import ctypes.util
from functools import cache

import numpy as np
import numpy.typing as npt

_OPUS_APPLICATION_AUDIO = 2049
_OPUS_SET_BITRATE_REQUEST = 4002
_OPUS_SET_COMPLEXITY_REQUEST = 4010
# Encoder complexity, 0-10: 10, the highest quality, at every bitrate (speech
# heard through noise reduction and EQ, and FT8/FT4 reception at 48 kbit/s).
# Measured on the S740: about 5 points of one core more than 5 for speech.
COMPLEXITY = 10


_MAX_PACKET = 4000


class OpusUnavailableError(Exception):
    """libopus is missing or refused the settings."""


@cache
def _library() -> ctypes.CDLL:
    name = ctypes.util.find_library("opus")
    if name is None:
        raise OpusUnavailableError("libopus not found (install libopus0)")
    lib = ctypes.CDLL(name)
    lib.opus_encoder_create.restype = ctypes.c_void_p
    lib.opus_encoder_create.argtypes = [
        ctypes.c_int32,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.opus_encode.restype = ctypes.c_int32
    lib.opus_encode.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int32,
    ]
    lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
    lib.opus_decoder_create.restype = ctypes.c_void_p
    lib.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    lib.opus_decode.restype = ctypes.c_int
    lib.opus_decode.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_int,
        ctypes.c_int,
    ]
    lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
    return lib


class OpusEncoder:
    """A mono Opus encoder at a fixed bitrate."""

    def __init__(self, sample_rate: int, bitrate: int, complexity: int = COMPLEXITY) -> None:
        if not 0 <= complexity <= COMPLEXITY:
            raise ValueError(f"complexity must be 0 to {COMPLEXITY}")
        lib = _library()
        error = ctypes.c_int()
        handle = lib.opus_encoder_create(
            sample_rate, 1, _OPUS_APPLICATION_AUDIO, ctypes.byref(error)
        )
        if not handle or error.value != 0:
            raise OpusUnavailableError(f"opus_encoder_create failed ({error.value})")
        self._handle: int | None = handle
        if (
            lib.opus_encoder_ctl(
                ctypes.c_void_p(handle), _OPUS_SET_BITRATE_REQUEST, ctypes.c_int32(bitrate)
            )
            != 0
        ):
            self.close()
            raise OpusUnavailableError(f"bitrate {bitrate} refused")
        lib.opus_encoder_ctl(
            ctypes.c_void_p(handle),
            _OPUS_SET_COMPLEXITY_REQUEST,
            ctypes.c_int32(complexity),
        )
        self._out = ctypes.create_string_buffer(_MAX_PACKET)

    def encode(self, pcm: npt.NDArray[np.int16]) -> bytes:
        """One packet for a frame of 2.5, 5, 10, 20, 40 or 60 ms of samples."""
        if self._handle is None:
            raise OpusUnavailableError("encoder closed")
        samples = np.ascontiguousarray(pcm, dtype=np.int16)
        size = _library().opus_encode(
            self._handle,
            samples.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
            len(samples),
            self._out,
            _MAX_PACKET,
        )
        if size < 0:
            raise OpusUnavailableError(f"opus_encode failed ({size})")
        return self._out.raw[:size]

    def close(self) -> None:
        if self._handle is not None:
            _library().opus_encoder_destroy(self._handle)
            self._handle = None


class OpusDecoder:
    """A mono Opus decoder."""

    _MAX_FRAME = 5760  # 120 ms at 48 kHz

    def __init__(self, sample_rate: int) -> None:
        lib = _library()
        error = ctypes.c_int()
        handle = lib.opus_decoder_create(sample_rate, 1, ctypes.byref(error))
        if not handle or error.value != 0:
            raise OpusUnavailableError(f"opus_decoder_create failed ({error.value})")
        self._handle: int | None = handle
        self._pcm = np.zeros(self._MAX_FRAME, dtype=np.int16)

    def decode(self, packet: bytes) -> npt.NDArray[np.int16]:
        """The samples of one packet (raises OpusUnavailableError on a bad packet)."""
        if self._handle is None:
            raise OpusUnavailableError("decoder closed")
        count = _library().opus_decode(
            self._handle,
            packet,
            len(packet),
            self._pcm.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
            self._MAX_FRAME,
            0,
        )
        if count < 0:
            raise OpusUnavailableError(f"opus_decode failed ({count})")
        return self._pcm[:count].copy()

    def close(self) -> None:
        if self._handle is not None:
            _library().opus_decoder_destroy(self._handle)
            self._handle = None

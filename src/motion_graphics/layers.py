"""Rendered layers (docs/decisions.md ADR-25): where each piece of a pre-rendered clip goes on the main timeline.

A rendered layer has three segments: IN (its first `in_duration` seconds), OUT (its last `out_duration` seconds) and the LOOP
between them. Placed over an element window [start, end), IN plays from `start`, OUT ends at `end`, and the LOOP fills the time
between: repeated (the last repeat cut short) when `loop` is true, played once and cut when false. Each piece becomes one
ffmpeg-skill/overlay `--place SRC_START-SRC_END@AT`.

Everything is computed in whole milliseconds, so consecutive pieces touch exactly (overlay refuses overlapping pieces) and the
same request always gives the same argv. Pure: no probe, no file access."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .errors import MotionGraphicsError

# a "repeat N times" bound like countdown.count_from (ADR-14): each piece is one trim + overlay in ffmpeg-skill's filter graph
MAX_LAYER_PIECES = 120
_MS = 1000


def _ms(seconds: float) -> int:
    return int(round(seconds * _MS))


def layer_placements(start: float, end: float, layer_duration: float, in_duration: float, out_duration: float, loop: bool,
                     where: str = "element") -> Tuple[List[Tuple[int, int, int]], Dict[str, Any]]:
    """[(src_start_ms, src_end_ms, at_ms)] and a summary {in, loop: {source, repeats, last_cut}, out}.

    `layer_duration` is floored to the millisecond so no piece asks for a picture past the clip's end."""
    d = int(layer_duration * _MS)  # floor
    s, e = _ms(start), _ms(end)
    i, o = _ms(in_duration), _ms(out_duration)
    w = e - s
    details = {"element": where, "layer_duration": d / _MS, "window": w / _MS, "in_duration": i / _MS, "out_duration": o / _MS}
    if d <= 0:
        raise MotionGraphicsError("INVALID_INPUT", f"{where}: the layer has no duration", dict(details, reason="layer_without_duration"))
    if i + o > d:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where}: in_duration + out_duration ({(i + o) / _MS:g}s) is longer than the layer ({d / _MS:g}s)",
                                  dict(details, reason="segments_longer_than_layer"))
    if i + o > w:
        raise MotionGraphicsError("INVALID_TIME_RANGE", f"{where}: in_duration + out_duration ({(i + o) / _MS:g}s) do not fit the element's window ({w / _MS:g}s)",
                                  dict(details, reason="segments_longer_than_window"))
    pieces: List[Tuple[int, int, int]] = []
    if i:
        pieces.append((0, i, s))
    fill = w - i - o
    m = d - i - o
    repeats, last_cut = 0, 0
    if fill > 0:
        if m <= 0:
            raise MotionGraphicsError("INVALID_REQUEST", f"{where}: the layer has no loop segment between its in and out ({d / _MS:g}s = in + out) "
                                      f"to fill {fill / _MS:g}s of the window", dict(details, reason="no_loop_segment"))
        if not loop and fill > m:
            raise MotionGraphicsError("INVALID_TIME_RANGE", f"{where}: with loop false the middle plays once ({m / _MS:g}s), shorter than the "
                                      f"{fill / _MS:g}s it must fill", dict(details, reason="layer_shorter_than_window"))
        at = s + i
        left = fill
        while left > 0:
            piece = min(m, left)
            pieces.append((i, i + piece, at))
            if piece == m:
                repeats += 1
            else:
                last_cut = piece
            at += piece
            left -= piece
            if len(pieces) > MAX_LAYER_PIECES:
                raise MotionGraphicsError("INVALID_REQUEST", f"{where}: filling {fill / _MS:g}s with a {m / _MS:g}s loop needs more than {MAX_LAYER_PIECES} "
                                          f"pieces", dict(details, reason="too_many_pieces", max_pieces=MAX_LAYER_PIECES))
    if o:
        pieces.append((d - o, d, e - o))
    summary = {"in": [0.0, i / _MS] if i else None, "out": [(d - o) / _MS, d / _MS] if o else None,
               "loop": {"source": [i / _MS, (d - o) / _MS], "repeats": repeats, "last_cut": last_cut / _MS} if fill > 0 else None,
               "pieces": len(pieces), "layer_duration": d / _MS}
    return pieces, summary


def place_args(pieces: List[Tuple[int, int, int]]) -> List[str]:
    """`--place A-B@T` for each piece, seconds with millisecond precision."""
    out: List[str] = []
    for a, b, at in pieces:
        out += ["--place", f"{a / _MS:.3f}-{b / _MS:.3f}@{at / _MS:.3f}"]
    return out


ALPHA_PIXEL_FORMATS = ("yuva", "rgba", "bgra", "argb", "abgr", "gbrap", "ya8", "ya16", "rgba64", "bgra64")


def has_alpha(pix_fmt: Any) -> bool:
    """Whether a decoded pixel format carries an alpha channel. A palette format (pal8) is not counted: whether its palette
    holds alpha cannot be told from the name. A VP9 alpha WebM probes as yuv420p: its alpha is in side data that the decoder
    ffmpeg-skill/overlay uses drops, so it is refused too."""
    return isinstance(pix_fmt, str) and pix_fmt.startswith(ALPHA_PIXEL_FORMATS)

"""Text fit for lower thirds and titles (docs/decisions.md ADR-27): shrink to a stated minimum, then break at the language's own
break opportunities, so a name or an affiliation stays inside its box. The text is never cut.

Width is estimated from a per-character table of advance widths in em, rounded *up* against common sans-serif and CJK faces:
a rendered line can come out narrower than the estimate, not wider. Breaking follows the Unicode line-breaking classes this
needs (UAX #14, simplified and stated below); a regional profile adds stricter rules on top. Pure: no font file, no process.

Break opportunities (default):
  - after a space (the space is not drawn at the end of a line), and after a hyphen or a slash;
  - before and after an ideograph, kana or Hangul syllable (ID), except as below;
  - never before closing punctuation, a full stop or comma, or an iteration mark (CL / CP / IS / NS: 、。）」』】〕〉》 . , ! ? ...);
  - never after opening punctuation (OP: （「『【〔〈《 ( [ { ...);
  - never inside a run of letters or digits of an alphabetic script (Latin, Cyrillic, Greek, Arabic, Hebrew ...).
Profile `ja-kinsoku` (opt-in): small kana and the prolonged sound mark (ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶー) and the middle dot
are non-starters too, so no line starts with one (UAX #14 treats them so only under "strict" breaking).
A run with no opportunity that is wider than the box even at the smallest size is broken between characters as a last resort
(reported as `forced_break`); a combining mark stays with its base."""
from __future__ import annotations

import unicodedata
from typing import Any, Dict, List, Optional, Tuple

PROFILES = ("ja-kinsoku",)

OPEN_PUNCT = set("([{（［｛「『【〔〈《〘〖‘“«")
CLOSE_PUNCT = set(")]}）］｝」』】〕〉》〙〗’”»、。，．,.!?！？:;：；…‥")
ITERATION = set("々〻ゝゞヽヾ")
KINSOKU_EXTRA = set("ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇺㇻㇼㇽㇾㇿーｰ・･")
BREAK_AFTER = set("-‐/–—")

STEP = 0.05


def _is_wide(cp: int) -> bool:
    return (0x1100 <= cp <= 0x115F or 0x2E80 <= cp <= 0x303E or 0x3041 <= cp <= 0x33FF or 0x3400 <= cp <= 0x4DBF or 0x4E00 <= cp <= 0x9FFF
            or 0xA960 <= cp <= 0xA97F or 0xAC00 <= cp <= 0xD7A3 or 0xF900 <= cp <= 0xFAFF or 0xFE30 <= cp <= 0xFE4F or 0xFF00 <= cp <= 0xFF60
            or 0xFFE0 <= cp <= 0xFFE6 or 0x20000 <= cp <= 0x3FFFD)


def char_em(ch: str) -> float:
    """Advance width of one character in em, rounded up."""
    cp = ord(ch)
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0.0
    if _is_wide(cp):
        return 1.0
    if ch == " ":
        return 0.35
    if ch in "MWmw@%":
        return 1.0
    if 0x0590 <= cp <= 0x08FF or 0xFB1D <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFF:  # Hebrew, Arabic and their presentation forms
        return 0.8
    cat = unicodedata.category(ch)
    if cat.startswith("L") and ch.isupper() or cat == "Nd":
        return 0.8
    if cat.startswith("L"):
        return 0.65 if cp < 0x2000 else 1.0   # an unfamiliar script is charged a full em
    if cat.startswith("P") or cat.startswith("S"):
        return 0.6
    return 1.0


def text_em(text: str) -> float:
    return sum(char_em(c) for c in text)


def _klass(ch: str, profile: Optional[str]) -> str:
    if ch == " ":
        return "SP"
    if ch in OPEN_PUNCT:
        return "OP"
    if ch in CLOSE_PUNCT or ch in ITERATION:
        return "CL"
    if profile == "ja-kinsoku" and ch in KINSOKU_EXTRA:
        return "CL"
    if ch in BREAK_AFTER:
        return "BA"
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me"):
        return "CM"
    if _is_wide(ord(ch)):
        return "ID"
    return "AL"


def break_allowed(prev: str, nxt: str, profile: Optional[str]) -> bool:
    """May a line end between `prev` and `nxt`?"""
    a, b = _klass(prev, profile), _klass(nxt, profile)
    if b in ("CL", "CM", "SP"):
        return False
    if a == "OP":
        return False
    if a in ("SP", "BA"):
        return True
    if a == "ID" or b == "ID":
        return True
    if a == "CL" and b in ("ID", "OP"):
        return True
    return False


def direction(text: str) -> str:
    """The paragraph direction from the first strong character (rtl for Arabic / Hebrew and the like)."""
    for ch in text:
        bidi = unicodedata.bidirectional(ch)
        if bidi in ("R", "AL"):
            return "rtl"
        if bidi == "L":
            return "ltr"
    return "ltr"


def _atoms(text: str, profile: Optional[str]) -> List[str]:
    out: List[str] = []
    cur = ""
    for i, ch in enumerate(text):
        if cur and break_allowed(text[i - 1], ch, profile):
            out.append(cur)
            cur = ""
        cur += ch
    if cur:
        out.append(cur)
    return out


def _split_chars(atom: str, max_em: float) -> List[str]:
    """Last resort: an atom wider than the line, broken between characters (a combining mark stays with its base)."""
    parts: List[str] = []
    cur = ""
    for ch in atom:
        if cur and text_em(cur + ch) > max_em and not (unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf")):
            parts.append(cur)
            cur = ""
        cur += ch
    if cur:
        parts.append(cur)
    return parts


def wrap(text: str, max_em: float, profile: Optional[str] = None, allow_forced: bool = True) -> Tuple[List[str], bool]:
    """Greedy fill at break opportunities. Returns (lines, forced): `forced` when a run had to be broken between characters.
    Joining the lines (with the spaces at the breaks) gives the text back unchanged: nothing is dropped."""
    lines: List[str] = []
    forced = False
    cur = ""
    for atom in _atoms(text, profile):
        if not cur or text_em((cur + atom).rstrip(" ")) <= max_em:
            if not cur and text_em(atom.rstrip(" ")) > max_em:
                if not allow_forced:
                    return [text], True
                pieces = _split_chars(atom, max_em)
                forced = forced or len(pieces) > 1
                lines.extend(pieces[:-1])
                cur = pieces[-1]
            else:
                cur += atom
            continue
        lines.append(cur)
        if text_em(atom.rstrip(" ")) > max_em:
            if not allow_forced:
                return [text], True
            pieces = _split_chars(atom, max_em)
            forced = forced or len(pieces) > 1
            lines.extend(pieces[:-1])
            cur = pieces[-1]
        else:
            cur = atom
    if cur:
        lines.append(cur)
    return [ln.rstrip(" ") for ln in lines], forced


# What each template draws, as multiples of min(width, height) x scale (ffmpeg-skill/graphics's layout, the same in every checkout
# this fit is offered on; tests/test_text_fit.py measures it): the font size of each text and the inner padding on each side.
TEMPLATE_LAYOUT: Dict[str, Dict[str, Any]] = {
    "lower_third": {"texts": {"name": 0.055, "title": 0.038}, "pad": 0.035},
    "title": {"texts": {"title": 0.11, "subtitle": 0.045}, "pad": 0.0},
}


def fit(template: str, texts: Dict[str, str], frame: Tuple[int, int], width: int, max_lines: int, min_scale: float,
        profile: Optional[str]) -> Dict[str, Any]:
    """The largest scale (1.0 down to min_scale, in steps of 0.05) at which every text fits `width` px, first on one line each,
    then on up to `max_lines` lines each. If none does, the smallest scale with as many lines as it takes (`overflow`)."""
    layout = TEMPLATE_LAYOUT[template]
    base = min(frame)
    scales = []
    s = 1.0
    while s >= min_scale - 1e-9:
        scales.append(round(s, 2))
        s -= STEP
    if scales[-1] > min_scale:
        scales.append(round(min_scale, 2))

    def attempt(scale: float, limit: Optional[int], allow_forced: bool) -> Optional[Dict[str, Any]]:
        pad = int(base * scale * layout["pad"])
        out: Dict[str, Any] = {}
        forced_any = False
        for key, text in texts.items():
            size = int(base * scale * layout["texts"][key])
            room = width - 2 * pad - max(2, size // 8)   # the outline a renderer may add, on top of the advances
            if size <= 0 or room <= 0:
                return None
            lines, forced = wrap(text, room / size, profile, allow_forced)
            if limit is not None and (len(lines) > limit or forced):
                return None
            forced_any = forced_any or forced
            out[key] = {"lines": lines, "font_size": size, "direction": direction(text),
                        "widest_px": max(int(text_em(ln) * size + 0.999) for ln in lines)}
        return {"scale": scale, "texts": out, "pad": pad, "forced_break": forced_any}

    for limit in range(1, max_lines + 1):
        for scale in scales:
            got = attempt(scale, limit, False)
            if got is not None:
                got.update(fits=True, overflow=None)
                return got
    got = attempt(scales[-1], None, True)
    assert got is not None
    too_many = any(len(t["lines"]) > max_lines for t in got["texts"].values())
    got.update(fits=not too_many, overflow="more lines than max_lines" if too_many else None)
    return got


def contract_section(fields: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """The `text_fit` block of the contract."""
    return {
        "applies_to": sorted(TEMPLATE_LAYOUT),
        "request_field": "elements[].fit",
        "fields": fields,
        "profiles": {"ja-kinsoku": "Japanese line-start rules: small kana, the prolonged sound mark and the middle dot do not start a line "
                                   "(on top of the default rules, which never start a line with closing punctuation or end one with opening punctuation)"},
        "method": ["sizes tried: 1.0 down to min_scale in steps of 0.05 (ffmpeg-skill/graphics --scale); first one line per text, then up to max_lines",
                   "width: a per-character advance table in em, rounded up (a drawn line can be narrower than the estimate, not wider)",
                   "breaks: after spaces, hyphens and slashes; before and after ideographs, kana and Hangul; never before closing punctuation "
                   "or after opening punctuation; never inside a run of an alphabetic script (UAX #14, simplified)",
                   "a run with no break that is wider than the box at the smallest size is broken between characters (forced_break), never cut",
                   "direction: from the first strong character (rtl for Arabic, Hebrew); the lines are given in logical order and laid out by ffmpeg-skill"],
        "layout": {t: {"font_size_of_short_side": dict(v["texts"]), "inner_padding_of_short_side": v["pad"]} for t, v in TEMPLATE_LAYOUT.items()},
        "result": "operations[].parameters.fit (and plan timeline[].fit_result): scale, texts {lines, font_size, direction, widest_px}, pad, fits, "
                  "overflow, forced_break, box_width, profile, lang; a warning when it does not fit max_lines or was broken between characters",
        "requires": "ffmpeg-skill/graphics --scale and --wrap (and --lang when fit.lang is given); otherwise refused before rendering (TOOL_ERROR)",
        "csv": "motion-graphics lower-thirds <file.csv> --video V --output O --fit-width N prints a request with one fitted lower third per row "
               "(columns: name, title, start, end, id, lang, profile)",
        "not_provided": ["phrase-level breaks from a dictionary or model (e.g. for Japanese, Thai): only the rules above",
                         "right-to-left reordering by this skill: ffmpeg-skill's text renderer lays out each line by its direction"],
    }

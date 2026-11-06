"""Reserved screen regions: YouTube end-screen elements and card teasers (docs/decisions.md ADR-28).

YouTube draws its end-screen elements (in the last 5-20 s) and its card teasers (a few seconds from each card's time) over the
video, at positions the editor sets in YouTube Studio. They are an input here: the request gives the regions, or names the stated
default layout for the frame's aspect (which the editor then reproduces in Studio). Every element's box is compared with every
region over the time both are on screen, and each overlap is reported -- or, with on_overlap "refuse", the request is refused
before anything renders. This skill moves nothing: where a graphic goes is the caller's decision.

An element's box is exact where the request fixes it (a shape; an image, video or layer of probed size at a named or explicit
position) and estimated from the template's layout and the text-fit width table otherwise (`precision`). Pure: the caller
supplies probed sizes."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .errors import MotionGraphicsError
from .textfit import TEMPLATE_LAYOUT, text_em, wrap

Box = Tuple[int, int, int, int]   # x, y, width, height in px

END_SCREEN_MIN, END_SCREEN_MAX = 5.0, 20.0
CARD_TEASER_DEFAULT = 5.0
DEFAULT_TEMPLATE_MARGIN = 48       # ffmpeg-skill/graphics's own default safe margin (brand safe_margin), px

# Stated default end-screen layouts, as fractions of the frame width (x, w) and height (y); a video element keeps 16:9 and the
# subscribe element is square, both sized from the width. One per aspect class; the result names the one used.
DEFAULT_END_SCREENS: Dict[str, List[Dict[str, Any]]] = {
    "16:9": [{"id": "video_1", "x": 0.08, "y": 0.30, "w": 0.30, "shape": "16:9"},
             {"id": "video_2", "x": 0.62, "y": 0.30, "w": 0.30, "shape": "16:9"},
             {"id": "subscribe", "x": 0.44, "y": 0.66, "w": 0.12, "shape": "1:1"}],
    "9:16": [{"id": "video_1", "x": 0.10, "y": 0.34, "w": 0.80, "shape": "16:9"},
             {"id": "subscribe", "x": 0.40, "y": 0.62, "w": 0.20, "shape": "1:1"}],
    "1:1": [{"id": "video_1", "x": 0.15, "y": 0.22, "w": 0.70, "shape": "16:9"},
            {"id": "subscribe", "x": 0.42, "y": 0.70, "w": 0.16, "shape": "1:1"}],
}
# the card teaser's default area: the top-right corner, 30 % of the width by 10 % of the height, 2 % in from the edges
DEFAULT_CARD_AREA = {"x": 0.68, "y": 0.02, "w": 0.30, "h": 0.10}


def aspect_class(width: int, height: int) -> str:
    r = width / float(height)
    return "16:9" if r >= 1.2 else ("9:16" if r <= 0.83 else "1:1")


def _rect(value: Any, where: str) -> Box:
    if not isinstance(value, dict) or sorted(value) != ["height", "width", "x", "y"]:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must be {{x, y, width, height}} in px", {"field": where})
    for k in ("x", "y", "width", "height"):
        v = value[k]
        if isinstance(v, bool) or not isinstance(v, int) or not (0 <= v <= 16384) or (k in ("width", "height") and v < 1):
            raise MotionGraphicsError("INVALID_REQUEST", f"{where}.{k} must be an integer px value (width/height >= 1)", {"field": f"{where}.{k}"})
    return value["x"], value["y"], value["width"], value["height"]


def _number(value: Any, where: str, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or not (lo <= float(value) <= hi):
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must be a number within [{lo:g}, {hi:g}]", {"field": where})
    return float(value)


def parse_reserved_regions(value: Any, where: str = "document.options.reserved_regions") -> Dict[str, Any]:
    """Structural check (no frame size yet): {end_screen?: {duration?, elements? | layout?}, cards?: [{time, duration?, region?}],
    on_overlap?: report|refuse}."""
    if not isinstance(value, dict):
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must be an object", {"field": where})
    unknown = sorted(k for k in value if k not in ("end_screen", "cards", "on_overlap"))
    if unknown:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where}: unknown field(s) {unknown}", {"field": where, "unknown": unknown})
    out: Dict[str, Any] = {"on_overlap": value.get("on_overlap", "report"), "end_screen": None, "cards": []}
    if out["on_overlap"] not in ("report", "refuse"):
        raise MotionGraphicsError("INVALID_REQUEST", f"{where}.on_overlap must be report or refuse", {"field": f"{where}.on_overlap"})
    es = value.get("end_screen")
    if es is not None:
        w = f"{where}.end_screen"
        if not isinstance(es, dict) or sorted(k for k in es if k not in ("duration", "elements", "layout")):
            raise MotionGraphicsError("INVALID_REQUEST", f"{w} takes duration, elements or layout", {"field": w})
        if "elements" in es and "layout" in es:
            raise MotionGraphicsError("INVALID_REQUEST", f"{w}: give elements (their positions) or layout, not both", {"field": w})
        if es.get("layout", "default") != "default":
            raise MotionGraphicsError("INVALID_REQUEST", f"{w}.layout must be default", {"field": f"{w}.layout"})
        elements = None
        if "elements" in es:
            if not isinstance(es["elements"], list) or not (1 <= len(es["elements"]) <= 4):
                raise MotionGraphicsError("INVALID_REQUEST", f"{w}.elements must list 1 to 4 end-screen elements (YouTube's limit)", {"field": f"{w}.elements"})
            elements = []
            for i, e in enumerate(es["elements"]):
                ew = f"{w}.elements[{i}]"
                if not isinstance(e, dict):
                    raise MotionGraphicsError("INVALID_REQUEST", f"{ew} must be an object", {"field": ew})
                rid = e.get("id", f"element_{i + 1}")
                if not isinstance(rid, str) or not (1 <= len(rid) <= 64):
                    raise MotionGraphicsError("INVALID_REQUEST", f"{ew}.id must be a short string", {"field": f"{ew}.id"})
                elements.append({"id": rid, "box": _rect({k: v for k, v in e.items() if k != "id"}, ew)})
        out["end_screen"] = {"duration": _number(es.get("duration", END_SCREEN_MAX), f"{w}.duration", END_SCREEN_MIN, END_SCREEN_MAX),
                             "elements": elements}
    cards = value.get("cards", [])
    if not isinstance(cards, list) or len(cards) > 5:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where}.cards must be a list of at most 5 cards (YouTube's limit)", {"field": f"{where}.cards"})
    for i, c in enumerate(cards):
        cw = f"{where}.cards[{i}]"
        if not isinstance(c, dict) or "time" not in c or sorted(k for k in c if k not in ("time", "duration", "region", "id")):
            raise MotionGraphicsError("INVALID_REQUEST", f"{cw} must be {{time, duration?, region?, id?}}", {"field": cw})
        out["cards"].append({"id": c.get("id", f"card_{i + 1}") if isinstance(c.get("id", ""), str) else f"card_{i + 1}",
                             "time": _number(c["time"], f"{cw}.time", 0.0, 24 * 3600.0),
                             "duration": _number(c.get("duration", CARD_TEASER_DEFAULT), f"{cw}.duration", 0.5, 30.0),
                             "box": _rect(c["region"], f"{cw}.region") if "region" in c else None})
    if out["end_screen"] is None and not out["cards"]:
        raise MotionGraphicsError("INVALID_REQUEST", f"{where} must give end_screen or cards", {"field": where})
    return out


def resolve_regions(spec: Dict[str, Any], width: int, height: int, duration: float) -> List[Dict[str, Any]]:
    """Every reserved region with its box and time window, for this frame and duration."""
    regions: List[Dict[str, Any]] = []
    es = spec.get("end_screen")
    if es is not None:
        if duration < es["duration"]:
            raise MotionGraphicsError("INVALID_TIME_RANGE", f"the end screen lasts {es['duration']:g}s, longer than the video ({duration:.3f}s)",
                                      {"field": "document.options.reserved_regions.end_screen.duration"})
        start = max(0.0, duration - es["duration"])
        if es["elements"] is not None:
            source = "input"
            items = es["elements"]
        else:
            source = f"default layout {aspect_class(width, height)}"
            items = []
            for d in DEFAULT_END_SCREENS[aspect_class(width, height)]:
                w = int(d["w"] * width)
                h = int(w * 9 / 16) if d["shape"] == "16:9" else w
                items.append({"id": d["id"], "box": (int(d["x"] * width), int(d["y"] * height), w, h)})
        for it in items:
            regions.append({"kind": "end_screen", "id": it["id"], "box": tuple(it["box"]), "start": start, "end": duration, "source": source})
    for c in spec.get("cards", []):
        box = c["box"]
        source = "input"
        if box is None:
            a = DEFAULT_CARD_AREA
            box = (int(a["x"] * width), int(a["y"] * height), int(a["w"] * width), int(a["h"] * height))
            source = "default area top-right"
        regions.append({"kind": "card_teaser", "id": c["id"], "box": tuple(box), "start": c["time"], "end": min(duration, c["time"] + c["duration"]), "source": source})
    return regions


# ---- element boxes
def _place(position: str, w: int, h: int, W: int, H: int, margin: int) -> Tuple[int, int]:
    named = {"top-left": (margin, margin), "top": ((W - w) // 2, margin), "top-right": (W - w - margin, margin),
             "left": (margin, (H - h) // 2), "center": ((W - w) // 2, (H - h) // 2), "right": (W - w - margin, (H - h) // 2),
             "bottom-left": (margin, H - h - margin), "bottom": ((W - w) // 2, H - h - margin), "bottom-right": (W - w - margin, H - h - margin)}
    if position in named:
        return named[position]
    xs, ys = position.split(",")
    x, y = int(xs), int(ys)
    return (W - w + x if x < 0 else x), (H - h + y if y < 0 else y)


def _block(text: str, size: int, max_px: int) -> Tuple[int, int]:
    lines, _ = wrap(text, max(1.0, max_px / float(size)))
    return max(int(text_em(ln) * size + 0.999) for ln in lines), size + (len(lines) - 1) * int(size * 1.2)


def element_boxes(el: Any, W: int, H: int, asset_size: Optional[Tuple[int, int]] = None,
                  fit: Optional[Dict[str, Any]] = None) -> Tuple[List[Box], str]:
    """The boxes an element draws in at rest, and "exact" or "estimated"."""
    p = el.parameters
    t = el.type
    m = DEFAULT_TEMPLATE_MARGIN
    scale = fit["scale"] if fit else 1.0
    base = min(W, H) * scale
    if t == "shape":
        x, y = _place(p["position"], p["width"], p["height"], W, H, p.get("margin", 0))
        return [(x, y, p["width"], p["height"])], ("exact" if "margin" in p else "estimated")
    if t in ("image_overlay", "video_overlay", "rendered_layer"):
        if asset_size is None:
            return [(0, 0, W, H)], "estimated"
        w0, h0 = asset_size
        if p.get("scale_width"):
            w = p["scale_width"]
        elif p.get("scale_percent") is not None:
            w = int(W * p["scale_percent"] / 100.0)
        else:
            w = w0
        h = int(round(h0 * w / float(w0))) if w0 else h0
        x, y = _place(p["position"], w, h, W, H, p.get("margin", 0))
        return [(x, y, w, h)], ("exact" if "margin" in p else "estimated")
    if t == "text_overlay":
        fs = p["font_size"]
        tw, th = _block(p["text"], fs, W - 2 * p.get("margin", 24))
        pad = 2 * p["border_width"] + (int(fs * 0.4) if p.get("box") else 0)
        w, h = tw + pad, th + pad
        x, y = _place(p["position"], w, h, W, H, p.get("margin", 24))
        return [(x, y, w, h)], "estimated"
    if t == "lower_third":
        lay = TEMPLATE_LAYOUT["lower_third"]
        h1, h2, pad, tx = int(base * lay["texts"]["name"]), int(base * lay["texts"]["title"]), int(base * 0.02), int(base * lay["pad"])
        room = W - 2 * m
        if fit:
            name_w, title_w = fit["texts"]["name"]["widest_px"], fit["texts"].get("title", {}).get("widest_px", 0)
            name_h = h1 + (len(fit["texts"]["name"]["lines"]) - 1) * int(h1 * 1.2)
            title_h = (h2 + (len(fit["texts"]["title"]["lines"]) - 1) * int(h2 * 1.2)) if "title" in fit["texts"] else 0
        else:
            name_w, name_h = _block(p["name"], h1, room - 2 * tx)
            title_w, title_h = _block(p["title"], h2, room - 2 * tx) if p.get("title") else (0, 0)
        bar_w = min(room, max(int(base * 0.62), max(name_w, title_w) + 2 * tx))
        bar_h = name_h + (title_h + pad if title_h else 0) + 2 * pad
        return [(m, H - m - bar_h, bar_w, bar_h)], "estimated"
    if t == "title":
        lay = TEMPLATE_LAYOUT["title"]
        h1, h2 = int(base * lay["texts"]["title"]), int(base * lay["texts"]["subtitle"])
        if fit:
            tw = max(v["widest_px"] for v in fit["texts"].values())
            th = h1 + (len(fit["texts"]["title"]["lines"]) - 1) * int(h1 * 1.2)
            sh = (h2 + (len(fit["texts"]["subtitle"]["lines"]) - 1) * int(h2 * 1.2)) if "subtitle" in fit["texts"] else 0
        else:
            tw, th = _block(p["title"], h1, int(W * 0.9))
            sw, sh = _block(p["subtitle"], h2, int(W * 0.9)) if p.get("subtitle") else (0, 0)
            tw = max(tw, sw)
        total = th + (int(base * 0.03) + sh if sh else int(base * 0.012))
        return [((W - tw) // 2, (H - th) // 2, tw, total)], "estimated"
    if t in ("bug", "chapter", "sticker"):
        fs = int(base * {"bug": 0.032, "chapter": 0.04, "sticker": 0.05}[t])
        padx, pady = (int(fs * 0.7), int(fs * 0.45)) if t == "sticker" else (int(fs * 0.6), int(fs * 0.35))
        tw, th = _block(p["text"] if t == "sticker" else p["title"], fs, W - 2 * m - 2 * padx)
        w, h = tw + 2 * padx, th + 2 * pady
        return [_place(p["position"], w, h, W, H, m) + (w, h)], "estimated"
    if t == "progress":
        h = max(3, int(base * 0.008))
        return [(0, H - h, W, h)], "estimated"
    if t == "countdown":
        fs = int(base * 0.32)
        w = int(fs * 0.8 * len(str(p["count_from"])))
        return [((W - w) // 2, (H - fs) // 2, w, fs)], "estimated"
    if t == "hook":
        h1 = int(base * 0.085)
        _, bh = _block(p["title"], h1, int(W * 0.9))
        band = min(H, max(int(base * 0.30), bh + int(h1 * 0.7)))
        return [(0, (H - band) // 2, W, band), (0, 0, W, max(3, int(base * 0.01)))], "estimated"
    if t == "meme":
        fs = int(base * 0.09)
        boxes = []
        for key, top in (("top", True), ("bottom", False)):
            if p.get(key):
                tw, th = _block(p[key].upper(), fs, int(W * 0.9))
                boxes.append(((W - tw) // 2, m if top else H - m - th, tw, th))
        return boxes, "estimated"
    return [(0, 0, W, H)], "estimated"   # pragma: no cover - every type is handled above


def _intersection(a: Box, b: Box) -> Optional[Box]:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else None


def overlaps(elements: List[Dict[str, Any]], regions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[{element_id, type, region {kind, id, box, source}, time [a, b], area {x, y, width, height}, precision}] for every element box
    that meets a region while both are on screen."""
    out: List[Dict[str, Any]] = []
    for e in elements:
        for r in regions:
            a, b = max(e["start"], r["start"]), min(e["end"], r["end"])
            if b <= a:
                continue
            for box in e["boxes"]:
                hit = _intersection(box, r["box"])
                if hit:
                    out.append({"element_id": e["id"], "type": e["type"], "region": {"kind": r["kind"], "id": r["id"], "box": list(r["box"]), "source": r["source"]},
                                "time": [round(a, 3), round(b, 3)], "element_box": list(box), "area": list(hit), "precision": e["precision"]})
                    break
    return out


def contract_section() -> Dict[str, Any]:
    """The `reserved_regions` block of the contract."""
    return {
        "request_field": "options.reserved_regions",
        "shape": {"end_screen?": {"duration?": f"{END_SCREEN_MIN:g}..{END_SCREEN_MAX:g} s, default {END_SCREEN_MAX:g} (the last seconds of the video)",
                                  "elements?": "[{id?, x, y, width, height}] px, 1 to 4: the positions the editor sets in YouTube Studio",
                                  "layout?": "default: the stated layout below for the frame's aspect (used when elements is not given)"},
                  "cards?": [{"time": "s", "duration?": f"teaser length, default {CARD_TEASER_DEFAULT:g} s", "region?": "{x, y, width, height} px; default the top-right area",
                              "id?": "str"}],
                  "on_overlap?": "report (default: a warning per overlap) | refuse (INVALID_REQUEST, details.reason reserved_region_overlap, before rendering)"},
        "default_end_screens": {k: v for k, v in DEFAULT_END_SCREENS.items()},
        "default_end_screen_units": "x and w are fractions of the frame width, y of its height; a 16:9 element's height is w x 9/16, a 1:1 element's is w; "
                                    "aspect class: width/height >= 1.2 is 16:9, <= 0.83 is 9:16, otherwise 1:1",
        "default_card_area": dict(DEFAULT_CARD_AREA),
        "element_boxes": "exact for a shape, and for an image / video / rendered layer of probed size with an explicit margin; estimated from the template "
                         "layout and the text_fit width table otherwise (the result says which)",
        "result": "response.reserved_regions {on_overlap, regions [{kind, id, box, start, end, source}], overlaps [{element_id, type, region, time, element_box, area, "
                  "precision}], element_boxes}; one warning per overlap",
        "note": "this skill moves nothing: it reports (or refuses); where a graphic goes is the caller's decision",
    }

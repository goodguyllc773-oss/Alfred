"""Vertical layouts as FFmpeg filtergraphs.

Each builder takes the *display* size of the source (after rotation), the output
size and the profile's ``Layout``, and returns a filtergraph that reads ``[0:v]``
and writes ``[vbase]`` plus hints for caption placement:

* ``crop``            9:16 window from the source, centred on the detected face.
* ``fit_blur``        whole frame scaled to fit, over a blurred, darkened copy.
* ``split_stack``     two source regions (e.g. two podcast hosts) stacked.
* ``content_facecam`` webcam region in one panel, gameplay/source in the other;
                      ``content_mode=fit`` keeps the *entire* gameplay frame visible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..analysis.framing import FaceSummary
from ..profiles.schema import Layout, Rect


@dataclass
class LayoutPlan:
    filtergraph: str
    seam_y: float | None = None          # where captions sit for position="seam" (0..1)
    face_box_out: tuple[float, float, float, float] | None = None
    details: dict = field(default_factory=dict)


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def _cover_crop(rx: float, ry: float, rw: float, rh: float, aspect: float,
                cx: float | None = None, cy: float | None = None) -> tuple[int, int, int, int]:
    """Largest ``aspect`` (w/h) rectangle inside region (rx, ry, rw, rh), centred on
    (cx, cy) if given (absolute px), else on the region centre."""
    if rw / rh > aspect:
        ch, cw = rh, rh * aspect
    else:
        cw, ch = rw, rw / aspect
    max_w, max_h = max(2, int(rw) // 2 * 2), max(2, int(rh) // 2 * 2)
    cw_i, ch_i = min(_even(cw), max_w), min(_even(ch), max_h)
    cx = rx + rw / 2 if cx is None else cx
    cy = ry + rh / 2 if cy is None else cy
    x = int(min(max(cx - cw_i / 2, rx), rx + rw - cw_i))
    y = int(min(max(cy - ch_i / 2, ry), ry + rh - ch_i))
    return cw_i, ch_i, max(0, x), max(0, y)


def _px(rect: Rect, W: int, H: int) -> tuple[float, float, float, float]:
    return rect.x * W, rect.y * H, max(2.0, rect.w * W), max(2.0, rect.h * H)


def _fit_panel(src: str, out: str, rx, ry, rw, rh, pw: int, ph: int) -> tuple[str, int]:
    """Region scaled to fit inside pw×ph over a blurred cover of itself. Returns
    (filter chain, height of the sharp foreground)."""
    small_w, small_h = _even(pw / 8), _even(ph / 8)
    crop = f"crop={int(rw)}:{int(rh)}:{int(rx)}:{int(ry)}"
    fg_h = min(ph, _even(pw * rh / rw))
    fg_w = min(pw, _even(fg_h * rw / rh))
    chain = (
        f"[{src}]{crop},split=2[{out}_a][{out}_b];"
        f"[{out}_a]scale={pw}:{ph}:force_original_aspect_ratio=increase,crop={pw}:{ph},"
        f"scale={small_w}:{small_h},boxblur=6:2,scale={pw}:{ph},eq=brightness=-0.12:saturation=0.8[{out}_bg];"
        f"[{out}_b]scale={fg_w}:{fg_h}:flags=lanczos[{out}_fg];"
        f"[{out}_bg][{out}_fg]overlay=(W-w)/2:(H-h)/2[{out}]"
    )
    return chain, fg_h


def plan_layout(layout: Layout, W: int, H: int, out_w: int, out_h: int,
                face: FaceSummary | None = None) -> LayoutPlan:
    kind = layout.kind
    if kind == "crop":
        aspect = out_w / out_h
        cx = (face.center_x if face else layout.crop_center_x) * W
        # Put the face ~40% from the top of the frame rather than dead centre.
        cy = (face.center_y * H + 0.1 * min(H, W / aspect)) if face else H / 2
        cw, ch, x, y = _cover_crop(0, 0, W, H, aspect, cx, cy)
        if layout.zoom > 1:
            cw, ch = _even(cw / layout.zoom), _even(ch / layout.zoom)
            x = int(min(max(cx - cw / 2, 0), W - cw))
            y = int(min(max(cy - ch / 2, 0), H - ch))
        fg = f"[0:v]crop={cw}:{ch}:{x}:{y},scale={out_w}:{out_h}:flags=lanczos,setsar=1[vbase]"
        face_out = None
        if face and face.box:
            fx, fy, fw, fh = face.box
            face_out = ((fx * W - x) / cw, (fy * H - y) / ch, fw * W / cw, fh * H / ch)
        return LayoutPlan(fg, None, face_out, {"crop": [cw, ch, x, y], "face_tracked": face is not None})

    if kind == "fit_blur":
        chain, fg_h = _fit_panel("0:v", "vfit", 0, 0, W, H, out_w, out_h)
        return LayoutPlan(chain + ";[vfit]setsar=1[vbase]", None, None, {"foreground_h": fg_h})

    if kind == "split_stack":
        h1 = _even(out_h / 2)
        h2 = out_h - h1
        parts = []
        for name, rect, ph in (("top", layout.top_rect, h1), ("bot", layout.bottom_rect, h2)):
            cw, ch, x, y = _cover_crop(*_px(rect, W, H), out_w / ph)
            parts.append((name, f"crop={cw}:{ch}:{x}:{y},scale={out_w}:{ph}:flags=lanczos"))
        fg = (f"[0:v]split=2[s_top][s_bot];[s_top]{parts[0][1]}[p_top];[s_bot]{parts[1][1]}[p_bot];"
              f"[p_top][p_bot]vstack=inputs=2,setsar=1[vbase]")
        return LayoutPlan(fg, h1 / out_h, None, {"panels": [h1, h2]})

    if kind == "content_facecam":
        fc_h = _even(out_h * layout.facecam_share)
        ct_h = out_h - fc_h
        fcw, fch, fx, fy = _cover_crop(*_px(layout.facecam_rect, W, H), out_w / fc_h)
        cam = f"[s_cam]crop={fcw}:{fch}:{fx}:{fy},scale={out_w}:{fc_h}:flags=lanczos[p_cam]"
        rx, ry, rw, rh = _px(layout.content_rect, W, H)
        if layout.content_mode == "crop":
            cw, ch, x, y = _cover_crop(rx, ry, rw, rh, out_w / ct_h)
            content = f"[s_con]crop={cw}:{ch}:{x}:{y},scale={out_w}:{ct_h}:flags=lanczos[p_con]"
            band = 0
        else:
            content, fg_h = _fit_panel("s_con", "p_con", rx, ry, rw, rh, out_w, ct_h)
            band = (ct_h - fg_h) / 2
        order = "[p_cam][p_con]" if layout.facecam_position == "top" else "[p_con][p_cam]"
        fg = f"[0:v]split=2[s_cam][s_con];{cam};{content};{order}vstack=inputs=2,setsar=1[vbase]"
        # Captions go in the blurred band between webcam and gameplay when there's
        # room, so they cover neither the face nor the game.
        from ..captions.ass import LINE_HEIGHT  # local import avoids a cycle
        need = 2 * 84 * (out_h / 1920) * LINE_HEIGHT
        if layout.facecam_position == "top":
            seam = (fc_h + band / 2) if band >= need else fc_h
        else:
            seam = (ct_h - band / 2) if band >= need else ct_h
        return LayoutPlan(fg, seam / out_h, None,
                          {"panels": [fc_h, ct_h], "facecam_crop": [fcw, fch, fx, fy], "band_px": band})

    raise ValueError(f"unknown layout kind {kind!r}")

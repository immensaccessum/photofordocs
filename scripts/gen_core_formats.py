import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from photofordocs.formats import _ini_text, parse_photolab_formats

ROOT = Path(r"C:/ProgramData/ColorRiver/Photolab/formats")
OUT = Path(__file__).resolve().parents[1] / "src" / "photofordocs" / "formats"

CORE = [
    ("passport-rf-2013", "jp_41"),
    ("photo-3x4-color", "jp_201"),
    ("photo-9x12-color", "jp_53"),
    ("photo-4x6-no-corner", "jp_14"),
    ("photo-3x4-bw", "jp_20"),
    ("photo-3x4-corner-right", "jp_02"),
    ("photo-45x60-no-corner", "jp_21"),
    ("military-ticket-enlisted", "jp_44"),
    ("photo-9x12", "jp_34"),
    ("photo-3x4-corner-left", "jp_03"),
]

for ident, ext in CORE:
    fmt = parse_photolab_formats(_ini_text(ROOT / f"format.{ext}"))[0]
    payload = {
        "id": ident,
        "name": fmt.name,
        "width_mm": fmt.width_mm,
        "height_mm": fmt.height_mm,
        "head_height_mm": fmt.head_height_mm,
        "chin_bottom_mm": fmt.chin_bottom_mm,
        "background": fmt.background,
        "note": "",
        "tier": "core",
        "legacy_ext": [ext],
        "have_corner": fmt.have_corner,
        "corner_kind": fmt.corner_kind,
        "corner_placement": fmt.corner_placement,
        "corner_rx_mm": fmt.corner_rx_mm,
        "corner_ry_mm": fmt.corner_ry_mm,
        "show_in_menu": True,
        "extension": ext,
        "colored": fmt.colored,
        "default_count": fmt.default_count,
        "place_horizontal": fmt.place_horizontal,
        "paper_kind": fmt.paper_kind,
        "two_points": fmt.two_points,
        "overhead_mm": fmt.overhead_mm,
        "overhead2_mm": fmt.overhead2_mm,
        "face_height_mm": fmt.face_height_mm,
        "retouch": fmt.retouch,
    }
    (OUT / f"{ident}.yaml").write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print("wrote", ident)
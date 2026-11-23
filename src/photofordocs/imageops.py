"""Кадр, замена фона и коррекция. Всё считается на этой машине."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps

from photofordocs.formats import Format


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    width: float
    height: float

    def shifted(self, dx: float, dy: float) -> Rect:
        return Rect(self.left + dx, self.top + dy, self.width, self.height)

    def scaled_about_center(self, factor: float) -> Rect:
        cx = self.left + self.width / 2
        cy = self.top + self.height / 2
        width = self.width / factor
        height = self.height / factor
        return Rect(cx - width / 2, cy - height / 2, width, height)


@dataclass(frozen=True)
class FaceBox:
    """Лицо в долях снимка (0…1). При детекции YuNet заполняются глаза и рот."""

    x: float
    y: float
    w: float
    h: float
    eyes_x: float | None = None
    eyes_y: float | None = None
    mouth_y: float | None = None
    crown_y: float | None = None


_CHIN_BELOW_MOUTH = 0.52
_CROWN_ABOVE_EYES = 0.48
_HAIR_ABOVE_BBOX = 0.24
_CROWN_MAX_ABOVE_BBOX = 0.32


def head_pose_pixels(
    face: FaceBox,
    width: int,
    height: int,
    fmt: Format | None = None,
) -> tuple[float, float, float, float]:
    """Макушка, глаза, подбородок и центр по X в пикселях исходника."""
    _ = fmt
    center_x = (face.x + face.w / 2) * width
    box_h = max(face.h * height, 1.0)
    bbox_top = face.y * height
    bbox_bottom = (face.y + face.h) * height
    if face.eyes_y is not None and face.mouth_y is not None:
        eye_y = float(face.eyes_y) * height
        if face.eyes_x is not None:
            center_x = float(face.eyes_x) * width
        mouth_y = float(face.mouth_y) * height
        eye_to_mouth = max(mouth_y - eye_y, 1.0)
        chin_mouth = mouth_y + _CHIN_BELOW_MOUTH * eye_to_mouth
        jaw_in_box = max(bbox_bottom - mouth_y, 1.0) / box_h
        if jaw_in_box < 0.22:
            chin_y = 0.45 * chin_mouth + 0.55 * bbox_bottom
        else:
            chin_bbox = bbox_bottom - 0.04 * box_h
            chin_y = 0.35 * chin_mouth + 0.65 * chin_bbox
        chin_y = min(chin_y, bbox_bottom + 0.06 * box_h)
        eye_to_chin = max(chin_y - eye_y, 1.0)
        crown_bbox = bbox_top - _HAIR_ABOVE_BBOX * box_h
        crown_eyes = eye_y - _CROWN_ABOVE_EYES * eye_to_chin
        crown_y = min(crown_bbox, crown_eyes)
        if (
            box_h > 280
            and crown_bbox < bbox_top - 0.235 * box_h
            and crown_eyes > crown_bbox + 0.12 * box_h
        ):
            crown_y = max(crown_bbox, 0.25 * crown_bbox + 0.75 * crown_eyes)
        crown_highest = bbox_top - _CROWN_MAX_ABOVE_BBOX * box_h
        crown_hairline = bbox_top - 0.07 * box_h
        crown_y = max(crown_highest, min(crown_y, crown_hairline))
        crown_y = max(0.0, crown_y)
        chin_y = min(float(height - 1), max(chin_y, mouth_y + 6.0))
        if face.crown_y is not None:
            hair = float(face.crown_y) * height
            # Верх волос, а не линия, где волосы встречаются со лбом.
            if 0.0 <= hair < eye_y - 4.0:
                crown_y = min(crown_y, hair)
        return crown_y, eye_y, chin_y, center_x
    chin_y = (face.y + face.h * 0.98) * height
    crown_y = (face.y - face.h * 0.42) * height
    eye_y = (face.y + face.h * 0.40) * height
    return crown_y, eye_y, chin_y, center_x


def guide_ratios_from_face(
    crop: Rect,
    face: FaceBox,
    image_size: tuple[int, int],
    fmt: Format | None = None,
) -> tuple[float, float, float] | None:
    """Положение линий внутри рамки по YuNet — для перекрестий на экране."""
    if face.eyes_y is None or face.mouth_y is None:
        return None
    width, height = image_size
    crown_y, eye_y, chin_y, _ = head_pose_pixels(face, width, height, fmt)
    span = max(crop.height, 1.0)
    crown_r = (crown_y - crop.top) / span
    eye_r = (eye_y - crop.top) / span
    chin_r = (chin_y - crop.top) / span
    crown_r = min(0.92, max(0.0, crown_r))
    chin_r = min(1.0, max(0.08, chin_r))
    eye_r = min(chin_r - 0.03, max(crown_r + 0.03, eye_r))
    return crown_r, eye_r, chin_r


def open_image(path: str) -> Image.Image:
    with Image.open(path) as raw:
        return ImageOps.exif_transpose(raw).convert("RGB")


def downscale(image: Image.Image, max_side: int) -> Image.Image:
    if max(image.size) <= max_side:
        return image
    copy = image.copy()
    copy.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return copy


_FACE_MODEL = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"
_PERSON_MODEL = Path(__file__).resolve().parent / "models" / "modnet_photographic_portrait_matting.onnx"
_person_net: cv2.dnn.Net | None = None


def _hair_crown_y(
    image: Image.Image,
    face_x: float,
    face_y: float,
    face_w: float,
    face_h: float,
    eyes_y: float,
) -> float | None:
    """Доля высоты снимка до верха волос над лицом. None, если силуэт не читается."""
    matte = person_matte(image)
    if matte is None:
        return None
    height, width = matte.shape
    center_x = face_x + face_w / 2.0
    half = max(face_w * 0.45, 4.0)
    x0 = max(0, int(round(center_x - half)))
    x1 = min(width, int(round(center_x + half)))
    y1 = min(height, int(round(eyes_y)))
    if x1 - x0 < 4 or y1 < 4:
        return None
    region = matte[:y1, x0:x1]
    span = x1 - x0
    rows = np.where((region > 0.22).sum(axis=1) >= max(3, span // 8))[0]
    if len(rows) == 0:
        return None
    top = int(rows[0])
    box_top = int(round(face_y))
    if top < box_top:
        bridge = region[top:box_top]
        if bridge.size and float((bridge > 0.22).mean()) < 0.08:
            return None
    # Чуть выше найденного края, чтобы лапка не врезалась в верхние волосы.
    top -= int(round(face_h * 0.04))
    return max(0.0, top) / height


def detect_face(image: Image.Image) -> FaceBox | None:
    """Самое крупное лицо. YuNet даёт рамку и пять опорных точек."""
    array = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    height, width = array.shape[:2]
    detector = cv2.FaceDetectorYN.create(str(_FACE_MODEL), "", (width, height), score_threshold=0.6)
    _retval, faces = detector.detect(array)
    if faces is None or len(faces) == 0:
        return None
    row = max(faces, key=lambda item: item[2] * item[3])
    x, y, face_w, face_h = row[:4]
    re_x, re_y, le_x, le_y = float(row[4]), float(row[5]), float(row[6]), float(row[7])
    rm_y, lm_y = float(row[11]), float(row[13])
    eyes_x = (re_x + le_x) / 2.0 / width
    eyes_y = (re_y + le_y) / 2.0 / height
    mouth_y = (rm_y + lm_y) / 2.0 / height
    return FaceBox(
        float(x) / width,
        float(y) / height,
        float(face_w) / width,
        float(face_h) / height,
        eyes_x=eyes_x,
        eyes_y=eyes_y,
        mouth_y=mouth_y,
        crown_y=_hair_crown_y(image, x, y, face_w, face_h, (re_y + le_y) / 2.0),
    )


# Колесо мыши остаётся в этих пределах. Перекрестия задают размер точнее,
# поэтому place_crop не обрезает зум до тройки: иначе рамка перестаёт
# уменьшаться и уезжает.
ZOOM_MIN = 0.05
ZOOM_MAX = 40.0


def detected_eye_t(
    face: FaceBox | None,
    image_size: tuple[int, int],
    fmt: Format,
) -> float:
    """Доля глаз на отрезке макушка–подбородок по YuNet (как при открытии снимка в PhotoLab)."""
    if face is None or face.eyes_y is None:
        return format_eye_t(fmt)
    width, height = image_size
    crown_y, eye_y, chin_y, _ = head_pose_pixels(face, width, height, fmt)
    span = chin_y - crown_y
    if span < 1.0:
        return format_eye_t(fmt)
    return min(0.9, max(0.1, (eye_y - crown_y) / span))


def initial_frame_fit(
    image_size: tuple[int, int],
    face: FaceBox | None,
    fmt: Format,
    eye_t: float | None = None,
) -> tuple[float, float, float]:
    """Сдвиг и при необходимости zoom, чтобы кадр влез в превью.

    Трёхточечный масштаб нельзя менять через деление crop_h на zoom — иначе
    макушка и подбородок перестают сидеть на направляющих. Сначала панорама, zoom только
    если рамка шире/выше картинки.
    """
    if face is None or fmt.two_points or fmt.face_height_mm <= 0:
        return 1.0, 0.0, 0.0
    if eye_t is None:
        eye_t = detected_eye_t(face, image_size, fmt)
    width, height = image_size
    crop = place_crop(image_size, face, fmt, 1.0, 0.0, 0.0, eye_t=eye_t)
    shift_x, shift_y = 0.0, 0.0
    if crop.width:
        if crop.left < 0:
            shift_x = -crop.left / crop.width
        elif crop.left + crop.width > width:
            shift_x = (width - crop.left - crop.width) / crop.width
    if crop.height:
        if crop.top < 0:
            shift_y = -crop.top / crop.height
        elif crop.top + crop.height > height:
            shift_y = (height - crop.top - crop.height) / crop.height
    zoom = 1.0
    fitted = place_crop(image_size, face, fmt, 1.0, shift_x, shift_y, eye_t=eye_t)
    if fitted.height > height or fitted.width > width:
        zoom = max(fitted.height / height, fitted.width / width, 1.0)
        zoom = min(max(zoom, ZOOM_MIN), ZOOM_MAX)
    return zoom, shift_x, shift_y


def _crop_from_head_span(
    crown_y: float,
    chin_y: float,
    center_x: float,
    fmt: Format,
    eye_t: float | None = None,
) -> Rect:
    """Трёхточечный кадр, как pose_from_ends при открытии снимка."""
    if eye_t is None:
        eye_t = format_eye_t(fmt)
    left, top, crop_h, _angle = pose_from_ends(
        fmt,
        (center_x, crown_y),
        (center_x, chin_y),
        False,
        eye_t,
    )
    return Rect(left, top, crop_h * fmt.aspect, crop_h)


def place_crop(
    image_size: tuple[int, int],
    face: FaceBox | None,
    fmt: Format,
    zoom: float = 1.0,
    shift_x: float = 0.0,
    shift_y: float = 0.0,
    eye_t: float | None = None,
) -> Rect:
    """Окно кадра в пикселях исходника.

    zoom > 1 приближает. shift_x/shift_y — доли ширины и высоты окна:
    положительный сдвиг уводит окно вправо и вниз.
    """
    width, height = image_size
    zoom = max(ZOOM_MIN, min(zoom, ZOOM_MAX))
    if face is None:
        crop_h = min(height, width / fmt.aspect) / zoom
        crop_w = crop_h * fmt.aspect
        left = (width - crop_w) / 2
        top = (height - crop_h) / 2
    else:
        crown_y, _eye_y, chin_y, center_x = head_pose_pixels(face, width, height, fmt)
        if not fmt.two_points and fmt.face_height_mm > 0:
            if eye_t is None:
                eye_t = format_eye_t(fmt)
            left, top, crop_h, _angle = pose_from_ends(
                fmt,
                (center_x, crown_y),
                (center_x, chin_y),
                False,
                eye_t,
            )
            crop_w = crop_h * fmt.aspect
            if zoom != 1.0:
                cx = left + crop_w / 2
                cy = top + crop_h / 2
                crop_h /= zoom
                crop_w /= zoom
                left = cx - crop_w / 2
                top = cy - crop_h / 2
        else:
            head_px = max(chin_y - crown_y, 1.0)
            ratio = fmt.frame_head_ratio if fmt.have_corner else fmt.head_ratio
            crop_h = head_px / ratio / zoom
            crop_w = crop_h * fmt.aspect
            chin_r = fmt.crop_chin_ratio if fmt.have_corner else fmt.chin_ratio
            top = chin_y - (1.0 - chin_r) * crop_h
            left = center_x - crop_w / 2
    left += shift_x * crop_w
    top += shift_y * crop_h
    return Rect(left, top, crop_w, crop_h)


def format_eye_t(fmt: Format) -> float:
    """Доля глаз на отрезке макушка–подбородок, при которой линии совпадают с полями формата."""
    head = fmt.frame_head_height_mm
    if head <= 0.1:
        return 0.55
    face = fmt.face_height_mm if fmt.face_height_mm > 0 else head * 0.45
    face = min(max(face, head * 0.1), head * 0.9)
    return 1.0 - face / head


def guide_ratios(fmt: Format, eye_t: float = 0.55) -> tuple[float, float, float]:
    """Доли макушки, глаз и подбородка внутри рамки.

    Трёхточечный кадр PhotoLab: масштаб — расстояние глаза–подбородок,
    делённое на лицевую высоту. Высота рамки равна высоте формата в этом
    масштабе, макушка стоит на верхнем поле. Подбородок попадает на нижнее
    поле только когда глаза стоят на линии формата. Сдвиг глаз меняет масштаб
    и высоту рамки, сами точки макушки и подбородка остаются на месте.
    Двухточечный кадр по-прежнему держит оба конца на полях формата.
    """
    eye_t = min(0.9, max(0.1, eye_t))
    face_mm = _face_mm(fmt)
    overhead = _overhead_mm(fmt)
    if not fmt.two_points and fmt.face_height_mm > 0:
        height = max(fmt.height_mm, 0.1)
        crown = overhead / height
        head_span = face_mm / ((1.0 - eye_t) * height)
        return crown, crown + eye_t * head_span, crown + head_span
    under = fmt.crop_under_mm
    scale_per_head = (1.0 - eye_t) / face_mm
    span = 1.0 + (overhead + under) * scale_per_head
    crown = overhead * scale_per_head / span
    chin = (overhead * scale_per_head + 1.0) / span
    eyes = (overhead * scale_per_head + eye_t) / span
    return crown, eyes, chin


def _face_mm(fmt: Format) -> float:
    face_mm = fmt.face_height_mm if fmt.face_height_mm > 0 else max(fmt.height_mm * fmt.eye_to_chin_ratio, 0.1)
    return max(face_mm, 0.1)


def _overhead_mm(fmt: Format) -> float:
    if not fmt.two_points:
        return fmt.overhead2_mm if fmt.overhead2_mm > 0 else fmt.height_mm * fmt.crown_ratio
    return fmt.height_mm * fmt.crown_ratio


def frame_points(
    crop: Rect,
    fmt: Format,
    angle: float = 0.0,
    eye_t: float = 0.55,
    guide: tuple[float, float, float] | None = None,
) -> dict[str, tuple[float, float]]:
    """Макушка, глаза и подбородок в пикселях исходника. Угол — наклон рамки по часовой.

    eye_t — доля глаз на отрезке от макушки к подбородку.
    guide — если передан, линии берутся с детектора, а не из шаблона формата.
    """
    cx = crop.left + crop.width / 2
    cy = crop.top + crop.height / 2
    rad = math.radians(angle)
    s, co = math.sin(rad), math.cos(rad)

    def at(local_y: float) -> tuple[float, float]:
        return (cx - local_y * s, cy + local_y * co)

    height = crop.height
    if guide is not None:
        crown_r, eye_r, chin_r = guide
    else:
        crown_r, eye_r, chin_r = guide_ratios(fmt, eye_t)
    return {
        "crown": at((crown_r - 0.5) * height),
        "eyes": at((eye_r - 0.5) * height),
        "chin": at((chin_r - 0.5) * height),
    }


def move_middle(
    crown: tuple[float, float],
    chin: tuple[float, float],
    proposal: tuple[float, float],
    allow_rotate: bool,
) -> tuple[tuple[float, float], tuple[float, float], float]:
    """Средняя точка остаётся на отрезке макушка–подбородок, не ближе 10% к концам.

    Без поворота прямая вертикальна и едет за горизонтальным сдвигом точки.
    С поворотом концы стоят, точка проецируется на прямую между ними.
    """
    ax, ay = crown
    bx, by = chin
    px, py = proposal
    if not allow_rotate:
        ax = bx = px
        span = by - ay
        fraction = 0.55 if abs(span) < 1e-6 else (py - ay) / span
    else:
        vx, vy = bx - ax, by - ay
        span2 = vx * vx + vy * vy
        fraction = 0.55 if span2 < 1e-6 else ((px - ax) * vx + (py - ay) * vy) / span2
    fraction = min(0.9, max(0.1, fraction))
    return (ax, ay), (bx, by), fraction


def ends_eye_t(fmt: Format, eye_line: float) -> float:
    """Средняя лапка остаётся в трёхточечном формате. Двухточечный берёт линию глаз из каталога."""
    if fmt.two_points or fmt.face_height_mm <= 0:
        return format_eye_t(fmt)
    return eye_line


def head_share(
    crop: Rect,
    fmt: Format,
    angle: float = 0.0,
    eye_t: float = 0.55,
    guide: tuple[float, float, float] | None = None,
) -> tuple[float, float]:
    """Процент высоты кадра между верхней и нижней лапкой и та же длина в миллиметрах формата."""
    if crop.height < 1:
        return 0.0, 0.0
    points = frame_points(crop, fmt, angle, eye_t, guide)
    dx = points["chin"][0] - points["crown"][0]
    dy = points["chin"][1] - points["crown"][1]
    percent = math.hypot(dx, dy) / crop.height * 100.0
    return percent, percent / 100.0 * fmt.height_mm


def pose_for_ends(
    image_size: tuple[int, int],
    face: FaceBox | None,
    fmt: Format,
    crown: tuple[float, float],
    chin: tuple[float, float],
    allow_rotate: bool,
    eye_line: float,
) -> tuple[float, float, float, float, float]:
    """Посадка рамки вокруг лапок: zoom, сдвиг, угол и линия глаз.

    Снимок и каталог форматов не меняются.
    """
    eye_t = ends_eye_t(fmt, eye_line)
    left, top, crop_h, angle = pose_from_ends(fmt, crown, chin, allow_rotate, eye_t)
    zoom, shift_x, shift_y = zoom_shift_for_box(image_size, face, fmt, left, top, crop_h, eye_t)
    shown = angle if allow_rotate else 0.0
    shift_x, shift_y = contain_guides(
        image_size, face, fmt, zoom, shift_x, shift_y, shown, eye_t
    )
    return zoom, shift_x, shift_y, shown, eye_t


def pose_from_ends(
    fmt: Format,
    crown: tuple[float, float],
    chin: tuple[float, float],
    allow_rotate: bool,
    eye_t: float = 0.55,
) -> tuple[float, float, float, float]:
    """Прямоугольник рамки: левый край, верх, высота, угол.

    В трёхточечном кадре масштаб — это глаза–подбородок на лицевую высоту,
    а высота рамки — высота формата в этом масштабе. Верх стоит на верхнем
    поле над макушкой. Масштаб растёт, когда глаза уходят от подбородка.
    """
    if not allow_rotate:
        angle = 0.0
    else:
        angle = math.degrees(math.atan2(-(chin[0] - crown[0]), chin[1] - crown[1]))
    dist = max(math.hypot(chin[0] - crown[0], chin[1] - crown[1]), 8.0)
    eye_t = min(0.9, max(0.1, eye_t))
    face_mm = _face_mm(fmt)
    scale = dist * (1.0 - eye_t) / face_mm
    if not fmt.two_points and fmt.face_height_mm > 0:
        crop_h = max(fmt.height_mm, 0.1) * scale
    else:
        crop_h = dist + (_overhead_mm(fmt) + fmt.crop_under_mm) * scale
    crop_w = crop_h * fmt.aspect
    rad = math.radians(angle)
    unit = (-math.sin(rad), math.cos(rad))
    crown_r, _eyes, _chin = guide_ratios(fmt, eye_t)
    local_crown = (crown_r - 0.5) * crop_h
    cx = crown[0] - local_crown * unit[0]
    cy = crown[1] - local_crown * unit[1]
    return cx - crop_w / 2, cy - crop_h / 2, crop_h, angle


def zoom_shift_for_box(
    image_size: tuple[int, int],
    face: FaceBox | None,
    fmt: Format,
    left: float,
    top: float,
    crop_h: float,
    eye_t: float | None = None,
) -> tuple[float, float, float]:
    """Подбирает zoom и сдвиг так, чтобы окно place_crop совпало с заданным прямоугольником."""
    base = place_crop(image_size, face, fmt, 1.0, 0.0, 0.0, eye_t=eye_t)
    zoom = max(ZOOM_MIN, min(base.height / max(crop_h, 1.0), ZOOM_MAX))
    placed = place_crop(image_size, face, fmt, zoom, 0.0, 0.0, eye_t=eye_t)
    shift_x = (left - placed.left) / placed.width if placed.width else 0.0
    shift_y = (top - placed.top) / placed.height if placed.height else 0.0
    return zoom, shift_x, shift_y


def contain_guides(
    image_size: tuple[int, int],
    face: FaceBox | None,
    fmt: Format,
    zoom: float,
    shift_x: float,
    shift_y: float,
    angle: float = 0.0,
    eye_t: float = 0.55,
    guide: tuple[float, float, float] | None = None,
) -> tuple[float, float]:
    """Сдвигает рамку так, чтобы макушка, глаза и подбородок остались на снимке."""
    width, height = image_size
    crop = place_crop(image_size, face, fmt, zoom, shift_x, shift_y)
    active = guide
    if active is None and face is not None:
        active = guide_ratios_from_face(crop, face, image_size, fmt)
    points = list(frame_points(crop, fmt, angle, eye_t, active).values())
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    dx = _nudge_span(min(xs), max(xs), width - 1)
    dy = _nudge_span(min(ys), max(ys), height - 1)
    if crop.width:
        shift_x += dx / crop.width
    if crop.height:
        shift_y += dy / crop.height
    return shift_x, shift_y


def _nudge_span(low: float, high: float, limit: float) -> float:
    if high - low >= limit:
        return (limit - (low + high)) / 2
    delta = 0.0
    if low + delta < 0:
        delta = -low
    if high + delta > limit:
        delta = limit - high
    return delta


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return (255, 255, 255)
    return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _head_ellipse(face: FaceBox, width: int, height: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """Только кожа лица. Белый фон рядом с волосами сюда не входит и заливается."""
    center_x = (face.x + face.w / 2.0) * width
    center_y = (face.y + face.h / 2.0) * height
    if face.eyes_x is not None:
        center_x = float(face.eyes_x) * width
    if face.eyes_y is not None:
        center_y = float(face.eyes_y) * height + face.h * height * 0.12
    rx = max(2, int(round(face.w * width * 0.42)))
    ry = max(2, int(round(face.h * height * 0.46)))
    return (int(round(center_x)), int(round(center_y))), (rx, ry)


def _in_body(face: FaceBox, x: int, y: int, width: int, height: int) -> bool:
    """Плечи и рубашка часто доходят до края кадра. Оттуда заливку не начинаем."""
    center_x = (face.x + face.w / 2.0) * width
    if face.eyes_x is not None:
        center_x = float(face.eyes_x) * width
    shoulders = face.y * height + face.h * height * 0.85
    if y < shoulders:
        return False
    half = face.w * width * (1.15 + 0.9 * min(1.0, (y - shoulders) / max(height - shoulders, 1.0)))
    return abs(x - center_x) <= half


def _matting_size(width: int, height: int, ref: int = 512) -> tuple[int, int]:
    """Стороны для MODNet: кратны 32, короткая сторона около 512, если кадр далеко от этого размера."""
    if max(height, width) < ref or min(height, width) > ref:
        if width >= height:
            new_h = ref
            new_w = int(width / height * ref)
        else:
            new_w = ref
            new_h = int(height / width * ref)
    else:
        new_w, new_h = width, height
    return max(32, new_w - new_w % 32), max(32, new_h - new_h % 32)


def _load_person_net() -> cv2.dnn.Net | None:
    global _person_net
    if _person_net is not None:
        return _person_net
    if not _PERSON_MODEL.is_file():
        return None
    _person_net = cv2.dnn.readNet(str(_PERSON_MODEL))
    return _person_net


def person_matte(image: Image.Image) -> np.ndarray | None:
    """Где человек: 1 — фигура, 0 — фон. None, если модель не ответила."""
    net = _load_person_net()
    if net is None:
        return None
    source = np.ascontiguousarray(np.asarray(image.convert("RGB")))
    height, width = source.shape[:2]
    if height < 2 or width < 2:
        return None
    net_w, net_h = _matting_size(width, height)
    resized = cv2.resize(source, (net_w, net_h), interpolation=cv2.INTER_AREA).astype(np.float32)
    blob = np.transpose((resized - 127.5) / 127.5, (2, 0, 1))[None, ...]
    try:
        net.setInput(blob)
        output = net.forward()
    except cv2.error:
        return None
    if output.size == 0:
        return None
    matte = cv2.resize(output[0, 0], (width, height), interpolation=cv2.INTER_LINEAR)
    return np.clip(matte, 0.0, 1.0).astype(np.float32)


def background_alpha(
    image: Image.Image,
    tolerance: float,
    brush: int = 0,
    face: FaceBox | None = None,
    method: str = "edge",
) -> np.ndarray | None:
    """Маска фона: 1 — фон, 0 — человек. Считается до цветокоррекции.

    edge — от краёв по похожему цвету. figure — по силуэту человека.
    Если силуэт не нашёлся, остаётся режим от краёв.
    """
    if tolerance <= 0:
        return None
    if method == "figure":
        found = _figure_background_alpha(image, tolerance, brush, face)
        if found is not None:
            return found
    return _edge_background_alpha(image, tolerance, brush, face)


def matte_to_alpha(
    matte: np.ndarray,
    tolerance: float,
    brush: int = 0,
    face: FaceBox | None = None,
) -> np.ndarray | None:
    """Порог силуэта: 1 — фон, 0 — человек. Сеть здесь не вызывается.

    Ползунок двигает только стену, лицо не бледнеет.
    """
    covered = float((matte > 0.5).mean())
    if not (0.02 < covered < 0.98):
        return None
    cut = 0.18 + float(tolerance) / 100.0 * 0.42
    lock = 0.80
    alpha = np.zeros(matte.shape, dtype=np.float32)
    alpha[matte <= cut] = 1.0
    band = (matte > cut) & (matte < lock)
    alpha[band] = ((lock - matte[band]) / (lock - cut)) ** 2
    skin = None
    if face is not None:
        height, width = alpha.shape
        skin = np.zeros((height, width), np.uint8)
        center, axes = _head_ellipse(face, width, height)
        cv2.ellipse(skin, center, axes, 0, 0, 360, 1, thickness=-1)
    alpha[matte >= lock] = 0.0
    if skin is not None:
        alpha[skin > 0] = 0.0
    if brush > 0:
        feather = max(1, min(24, int(brush)))
        kernel = feather * 2 + 1
        alpha = cv2.GaussianBlur(alpha, (kernel, kernel), 0)
        alpha[matte >= lock] = 0.0
        if skin is not None:
            alpha[skin > 0] = 0.0
    alpha[alpha >= 0.92] = 1.0
    alpha[alpha <= 0.04] = 0.0
    return np.ascontiguousarray(alpha)


def _figure_background_alpha(
    image: Image.Image,
    tolerance: float,
    brush: int = 0,
    face: FaceBox | None = None,
) -> np.ndarray | None:
    """Фон по силуэту. Матч считает сеть, порог от неё не зависит."""
    matte = person_matte(image)
    if matte is None:
        return None
    return matte_to_alpha(matte, tolerance, brush, face)


def _edge_background_alpha(
    image: Image.Image,
    tolerance: float,
    brush: int = 0,
    face: FaceBox | None = None,
) -> np.ndarray | None:
    """Запасной фон: от краёв по похожему цвету, не заходя на узкий овал кожи."""
    source = np.ascontiguousarray(np.asarray(image.convert("RGB")))
    height, width = source.shape[:2]
    if height < 2 or width < 2 or tolerance <= 0:
        return None
    lab = cv2.cvtColor(source, cv2.COLOR_RGB2LAB)
    wall = np.zeros((height + 2, width + 2), np.uint8)
    if face is not None:
        center, axes = _head_ellipse(face, width, height)
        cv2.ellipse(wall, (center[0] + 1, center[1] + 1), axes, 0, 0, 360, 1, thickness=-1)
    # Чувствительность 0…100: насколько соседний пиксель может отличаться, чтобы остаться фоном.
    diff = max(1, int(round(float(tolerance) * 0.45)))
    flags = 8 | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
    step = max(4, min(height, width) // 48)
    seeds: list[tuple[int, int]] = []
    for x in range(0, width, step):
        seeds.append((x, 0))
        seeds.append((x, height - 1))
    for y in range(step, height - 1, step):
        seeds.append((0, y))
        seeds.append((width - 1, y))
    span = (diff, diff, diff)
    for x, y in seeds:
        if wall[y + 1, x + 1] != 0:
            continue
        if face is not None and _in_body(face, x, y, width, height):
            continue
        cv2.floodFill(lab, wall, (x, y), (0, 0, 0), span, span, flags)
    filled = np.where(wall[1:-1, 1:-1] == 255, np.uint8(255), np.uint8(0))
    if int(filled.max()) == 0:
        return None
    speck = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    filled = cv2.morphologyEx(filled, cv2.MORPH_CLOSE, speck)
    feather = max(1, min(24, int(brush))) if brush > 0 else max(1, round(min(height, width) / 350))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (feather * 2 + 1, feather * 2 + 1))
    expanded = cv2.dilate(filled, kernel)
    if face is not None:
        center, axes = _head_ellipse(face, width, height)
        cv2.ellipse(expanded, center, axes, 0, 0, 360, 0, thickness=-1)
    person = np.where(expanded == 0, np.uint8(255), np.uint8(0))
    distance = cv2.distanceTransform(person, cv2.DIST_L2, 3)
    alpha = np.clip(1.0 - distance / max(feather, 1), 0.0, 1.0)
    alpha[expanded > 0] = 1.0
    return alpha.astype(np.float32)


def hold_background_array(toned: np.ndarray, original: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Коррекция остаётся на человеке. Пиксели фона берутся из кадра до коррекции."""
    cover = alpha.astype(np.float32)
    if cover.ndim == 2:
        cover = cover[..., None]
    kept = toned.astype(np.float32) * (1.0 - cover) + original.astype(np.float32) * cover
    return np.clip(kept, 0.0, 255.0).astype(np.uint8)


def paint_background_array(image: np.ndarray, alpha: np.ndarray, target: tuple[int, int, int]) -> np.ndarray:
    """Кладёт ровный цвет заливки поверх уже скорректированного кадра."""
    cover = alpha.astype(np.float32)
    if cover.ndim == 2:
        cover = cover[..., None]
    base = image.astype(np.float32)
    painted = base * (1.0 - cover) + np.array(target, dtype=np.float32) * cover
    return np.clip(painted, 0.0, 255.0).astype(np.uint8)


def replace_background(
    image: Image.Image,
    target: tuple[int, int, int],
    tolerance: float,
    brush: int = 0,
    face: FaceBox | None = None,
    method: str = "edge",
) -> Image.Image:
    """Красит найденный фон. По умолчанию фон ищется от краёв."""
    alpha = background_alpha(image, tolerance, brush, face, method)
    if alpha is None:
        return image
    painted = paint_background_array(np.asarray(image.convert("RGB")), alpha, target)
    return Image.fromarray(painted, "RGB")


def _tone_curve_lut(brightness: float, contrast: float, gamma: float, black_point: float, white_point: float) -> np.ndarray:
    """Та же кривая, что раньше считалась на каждом пикселе, но на 256 значениях."""
    ramp = np.arange(256, dtype=np.float32)
    black = min(float(black_point), float(white_point) - 1.0)
    white = max(float(white_point), black + 1.0)
    ramp = (ramp - black) / (white - black) * 255.0
    gain = (float(contrast) + 100.0) / 100.0
    ramp = (ramp - 128.0) * gain + 128.0 + float(brightness) / 100.0 * 255.0
    power = 2.0 ** (-float(gamma) / 100.0)
    ramp = np.power(np.clip(ramp, 0.0, 255.0) / 255.0, power) * 255.0
    return np.clip(ramp, 0.0, 255.0).astype(np.uint8)


def apply_tone_array(
    image: np.ndarray,
    brightness: float,
    contrast: float,
    grayscale: bool,
    gamma: float = 0.0,
    saturation: float = 0.0,
    sharpness: float = 0.0,
    black_point: float = 0.0,
    white_point: float = 255.0,
    balance: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None = None,
) -> np.ndarray:
    """Быстрый вариант apply_tone: кривая таблицей, без прохода по полному float-кадру."""
    lut = _tone_curve_lut(brightness, contrast, gamma, black_point, white_point)
    out = cv2.LUT(np.ascontiguousarray(image), lut)
    balance_on = balance is not None and any(abs(float(value)) > 1e-6 for row in balance for value in row)
    sat_on = abs(float(saturation)) > 1e-6
    if balance_on or sat_on:
        work = out.astype(np.float32)
        if balance_on:
            luma = np.clip(work.mean(axis=2) / 255.0, 0.0, 1.0)
            shadow_w = np.clip(1.0 - luma * 2.0, 0.0, 1.0)
            high_w = np.clip(luma * 2.0 - 1.0, 0.0, 1.0)
            mid_w = np.clip(1.0 - shadow_w - high_w, 0.0, 1.0)
            shadows, mids, highs = balance
            for index, parts in enumerate(zip(shadows, mids, highs, strict=True)):
                work[..., index] += -(shadow_w * parts[0] + mid_w * parts[1] + high_w * parts[2])
        if sat_on:
            gray = work.mean(axis=2, keepdims=True)
            sat = (float(saturation) + 100.0) / 100.0
            work = gray + (work - gray) * sat
        out = np.clip(work, 0.0, 255.0).astype(np.uint8)
    if sharpness:
        blurred = cv2.GaussianBlur(out, (0, 0), 1.2)
        amount = float(sharpness) / 100.0
        out = cv2.addWeighted(out, 1.0 + amount, blurred, -amount, 0.0)
    if grayscale:
        gray = cv2.cvtColor(out, cv2.COLOR_RGB2GRAY)
        out = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
    return out


def apply_tone(
    image: Image.Image,
    brightness: float,
    contrast: float,
    grayscale: bool,
    gamma: float = 0.0,
    saturation: float = 0.0,
    sharpness: float = 0.0,
    black_point: float = 0.0,
    white_point: float = 255.0,
    balance: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None = None,
) -> Image.Image:
    """Ползунки яркости и цвета в диапазоне −100…100. Ноль оставляет снимок как есть.

    balance — три тройки (голубой, пурпурный, жёлтый) для теней, полутонов и бликов.
    """
    array = apply_tone_array(
        np.asarray(image.convert("RGB")),
        brightness,
        contrast,
        grayscale,
        gamma,
        saturation,
        sharpness,
        black_point,
        white_point,
        balance,
    )
    return Image.fromarray(array, "RGB")


def histogram(image: Image.Image, channel: str = "luma", clip_peaks: bool = False) -> list[int]:
    array = np.asarray(image.convert("RGB"))
    if channel == "red":
        data = array[:, :, 0]
    elif channel == "green":
        data = array[:, :, 1]
    elif channel == "blue":
        data = array[:, :, 2]
    else:
        data = array.mean(axis=2)
    counts, _edges = np.histogram(data, bins=256, range=(0, 255))
    values = [int(item) for item in counts]
    if clip_peaks and values:
        cap = max(1, int(sorted(values)[int(len(values) * 0.98)]))
        values = [min(item, cap) for item in values]
    return values


def corner_mode_from_format(fmt: Format) -> str:
    """Короткий режим уголка, который показывает панель кадра.

    Без уголка, круглый или прямой, снизу справа или слева.
    Если отмечены оба нижних угла, берётся правый: в списке панели один угол.
    """
    if not fmt.have_corner or not fmt.corner_placement:
        return "none"
    right = bool(fmt.corner_placement & 8)
    left = bool(fmt.corner_placement & 2)
    if not right and not left:
        return "none"
    side = "right" if right else "left"
    shape = "straight" if fmt.corner_kind == 0 else "round"
    return f"{shape}-{side}"


def resolve_corner(fmt: Format, mode: str) -> tuple[int, int, float, float]:
    """Положение, вид и радиусы уголка в миллиметрах.

    Биты положения, как флажки формата: 1 — верхний левый, 2 — нижний левый,
    4 — верхний правый, 8 — нижний правый. «Слева» и «справа» в названиях
    форматов — это нижние углы. Вид 0 — прямой (четверть ромба, треугольник),
    2 — круглый (четверть круга из самого угла). Если у формата уголка нет,
    ручной выбор берёт стандартные 18×18 мм.
    """
    standard_x = fmt.corner_rx_mm if fmt.corner_rx_mm > 0 else 18.0
    standard_y = fmt.corner_ry_mm if fmt.corner_ry_mm > 0 else 18.0
    manual = {
        "straight-left": (2, 0),
        "straight-right": (8, 0),
        "round-left": (2, 2),
        "round-right": (8, 2),
    }
    if mode in manual:
        placement, kind = manual[mode]
        return placement, kind, standard_x, standard_y
    if mode in {"", "format"} and fmt.have_corner and fmt.corner_placement:
        return fmt.corner_placement, fmt.corner_kind, standard_x, standard_y
    return 0, 0, standard_x, standard_y


def apply_corner(
    image: Image.Image,
    placement: int,
    kind: int,
    rx_px: int,
    ry_px: int,
    fill: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    if placement == 0 or rx_px <= 0 or ry_px <= 0:
        return image
    result = image.convert("RGB")
    width, height = result.size
    rx = min(rx_px, width)
    ry = min(ry_px, height)
    mask = Image.new("L", result.size, 255)
    draw = ImageDraw.Draw(mask)
    # 1 верх-лево, 2 низ-лево, 4 верх-право, 8 низ-право.
    corners = {
        "tl": (0, 0),
        "bl": (0, height),
        "tr": (width, 0),
        "br": (width, height),
    }
    spots = []
    if placement & 1:
        spots.append("tl")
    if placement & 2:
        spots.append("bl")
    if placement & 4:
        spots.append("tr")
    if placement & 8:
        spots.append("br")
    for spot in spots:
        cx, cy = corners[spot]
        sx, sy = _corner_inward(spot)
        if kind == 0:
            # Ромб с центром в углу: в кадре остаётся треугольник.
            draw.polygon([(cx, cy), (cx + sx * rx, cy), (cx, cy + sy * ry)], fill=0)
        elif kind in {1, 4}:
            # Центр круга внутри кадра: дуга срезает внешний колпачок угла.
            box = (min(cx, cx + sx * rx), min(cy, cy + sy * ry), max(cx, cx + sx * rx), max(cy, cy + sy * ry))
            draw.rectangle(box, fill=0)
            ix, iy = cx + sx * rx, cy + sy * ry
            draw.ellipse((ix - rx, iy - ry, ix + rx, iy + ry), fill=255)
        else:
            # Круг из самого угла: в кадре остаётся четверть круга.
            draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=0)
    plate = Image.new("RGB", result.size, fill)
    return Image.composite(result, plate, mask)


def _corner_inward(spot: str) -> tuple[int, int]:
    """Куда от угла идти внутрь кадра: вправо/влево и вниз/вверх."""
    return {"tl": (1, 1), "tr": (-1, 1), "bl": (1, -1), "br": (-1, -1)}[spot]


def render_crop(
    image: Image.Image,
    crop: Rect,
    out_size: tuple[int, int],
    fill: tuple[int, int, int],
    angle: float = 0.0,
    resample: Image.Resampling = Image.Resampling.BICUBIC,
) -> Image.Image:
    out_w, out_h = out_size
    # Обратное отображение: пиксель результата -> пиксель исходника.
    # Угол по часовой стрелке крутит окно вокруг его центра.
    if abs(angle) < 1e-6:
        matrix = (
            crop.width / out_w,
            0.0,
            crop.left,
            0.0,
            crop.height / out_h,
            crop.top,
        )
    else:
        rad = math.radians(angle)
        s, co = math.sin(rad), math.cos(rad)
        cx = crop.left + crop.width / 2
        cy = crop.top + crop.height / 2
        matrix = (
            crop.width / out_w * co,
            -crop.height / out_h * s,
            cx - crop.width / 2 * co + crop.height / 2 * s,
            crop.width / out_w * s,
            crop.height / out_h * co,
            cy - crop.width / 2 * s - crop.height / 2 * co,
        )
    return image.transform(out_size, Image.Transform.AFFINE, matrix, resample=resample, fillcolor=fill)


def _crop_alpha(
    full: np.ndarray | None,
    crop: Rect,
    size: tuple[int, int],
    angle: float,
    resample: Image.Resampling,
) -> np.ndarray | None:
    """Обрезает маску фона тем же окном, что и кадр. Снаружи кадра — полная заливка."""
    if full is None:
        return None
    mask = Image.fromarray(np.clip(full * 255.0, 0, 255).astype(np.uint8), "L")
    cropped = render_crop(mask, crop, size, 255, angle, resample)
    return np.asarray(cropped).astype(np.float32) / 255.0


def photo_pixels(fmt: Format, dpi: int) -> tuple[int, int]:
    return (
        max(1, round(fmt.width_mm / 25.4 * dpi)),
        max(1, round(fmt.height_mm / 25.4 * dpi)),
    )


def render_photo(
    image: Image.Image,
    crop: Rect,
    fmt: Format,
    dpi: int,
    *,
    replace: bool,
    tolerance: float,
    brightness: float,
    contrast: float,
    grayscale: bool,
    background: str | None = None,
    gamma: float = 0.0,
    saturation: float = 0.0,
    sharpness: float = 0.0,
    black_point: float = 0.0,
    white_point: float = 255.0,
    balance: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]] | None = None,
    corner: str = "none",
    angle: float = 0.0,
    brush: int = 0,
    resample: Image.Resampling = Image.Resampling.BICUBIC,
    face: FaceBox | None = None,
    prepared_alpha: np.ndarray | None = None,
    already_cropped: bool = False,
    method: str = "edge",
    paint: bool = False,
    source_alpha: np.ndarray | None = None,
) -> Image.Image:
    """Собирает готовый кадр: обрезка, коррекция, фон и уголок.

    already_cropped — картинка уже является кадром (превью). Тогда маска берётся
    готовой, без повторного поиска фона. source_alpha — такая же маска на весь
    исходник: её обрезают вместе с кадром и фон заново не ищут. Фон не красится,
    пока paint выключен: коррекция не применяется к нему, цвет стены остаётся со снимка.
    """
    fill = hex_to_rgb(background or fmt.background)
    if already_cropped:
        picture = image.convert("RGB")
        size = picture.size
        alpha = prepared_alpha
    else:
        size = photo_pixels(fmt, dpi)
        picture = render_crop(image, crop, size, fill, angle, resample)
        # Маска по исходному снимку, заливка — после коррекции, чтобы выбранный
        # цвет не ехал за яркостью, контрастом и гаммой.
        alpha = _crop_alpha(source_alpha, crop, size, angle, resample)
        if alpha is None and replace and tolerance > 0:
            full = background_alpha(image, tolerance, brush, face, method)
            alpha = _crop_alpha(full, crop, size, angle, resample)
    toned = apply_tone(
        picture,
        brightness,
        contrast,
        grayscale,
        gamma,
        saturation,
        sharpness,
        black_point,
        white_point,
        balance,
    )
    if alpha is not None:
        corrected = np.asarray(toned.convert("RGB"))
        if paint:
            toned = Image.fromarray(paint_background_array(corrected, alpha, fill), "RGB")
        else:
            toned = Image.fromarray(hold_background_array(corrected, np.asarray(picture.convert("RGB")), alpha), "RGB")
    placement, kind, rx_mm, ry_mm = resolve_corner(fmt, corner)
    rx_px = max(1, round(rx_mm / fmt.width_mm * size[0])) if fmt.width_mm else 1
    ry_px = max(1, round(ry_mm / fmt.height_mm * size[1])) if fmt.height_mm else 1
    return apply_corner(toned, placement, kind, rx_px, ry_px, fill)


def write_jpeg(image: Image.Image, path: Path, dpi: int, quality: int, max_kb: int = 0) -> None:
    """JPEG с меткой DPI. Если задан потолок в КБ, качество снижается, пока файл не влезет."""
    picture = image.convert("RGB")
    dpi = max(1, int(dpi))
    quality = max(10, min(100, int(quality)))
    path = Path(path)

    def encode(level: int) -> bytes:
        buffer = io.BytesIO()
        picture.save(buffer, format="JPEG", quality=level, dpi=(dpi, dpi), optimize=True)
        return buffer.getvalue()

    data = encode(quality)
    limit = max(0, int(max_kb)) * 1024
    if limit and len(data) > limit:
        low, high = 5, quality - 1
        best = encode(5)
        while low <= high:
            mid = (low + high) // 2
            trial = encode(mid)
            if len(trial) <= limit:
                best = trial
                low = mid + 1
            else:
                high = mid - 1
        data = best
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_pdf(images: list[Image.Image], path: Path, dpi: int, quality: int) -> None:
    """PDF со страницей в миллиметрах картинки: 100 % в читалке совпадает с бумагой."""
    if not images:
        raise ValueError("empty pdf")
    dpi = max(1, int(dpi))
    quality = max(10, min(100, int(quality)))
    pages = [image.convert("RGB") for image in images]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(
        path,
        "PDF",
        save_all=True,
        append_images=pages[1:],
        resolution=float(dpi),
        quality=quality,
    )

"""Images for photogrammetry: reading (Unicode paths), EXIF (focal length, GPS), video frame extraction."""
from __future__ import annotations

import os
import struct

import numpy as np

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")
VIDEO_EXT = (".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mts", ".wmv")


def cv2_module():
    try:
        import cv2
    except Exception as e:  # noqa: BLE001
        raise ModuleNotFoundError("No module named 'cv2' (OpenCV) — install opencv-python-headless",
                                  name="cv2") from e
    return cv2


def imread(path, max_dim=0, gray=False):
    """Read an image (also from paths with non-ASCII characters on Windows); optional downscale."""
    cv2 = cv2_module()
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE if gray else cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Cannot read image {os.path.basename(path)}")
    if max_dim and max(img.shape[:2]) > max_dim:
        s = max_dim / max(img.shape[:2])
        img = cv2.resize(img, (int(round(img.shape[1] * s)), int(round(img.shape[0] * s))),
                         interpolation=cv2.INTER_AREA)
    return img


def imwrite(path, img, quality=95):
    cv2 = cv2_module()
    ext = os.path.splitext(path)[1] or ".jpg"
    ok, buf = cv2.imencode(ext, img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise IOError(f"Cannot write {path}")
    buf.tofile(path)


def list_images(folder_or_files):
    if isinstance(folder_or_files, (list, tuple)):
        files = [f for f in folder_or_files if f.lower().endswith(IMAGE_EXT)]
    else:
        files = [os.path.join(folder_or_files, f) for f in os.listdir(folder_or_files)
                 if f.lower().endswith(IMAGE_EXT)]
    return sorted(files, key=lambda p: os.path.basename(p).lower())


def sharpness(gray):
    cv2 = cv2_module()
    return float(cv2.Laplacian(gray, cv2.CV_32F).var())


# ---------------------------------------------------------------- EXIF (JPEG / TIFF, standard library only)
_TYPES = {1: (1, "B"), 2: (1, "s"), 3: (2, "H"), 4: (4, "I"), 5: (8, "R"), 7: (1, "B"), 9: (4, "i"),
          10: (8, "r"), 11: (4, "f"), 12: (8, "d")}


def _ifd(buf, off, endian, base):
    out = {}
    if off + 2 > len(buf):
        return out, 0
    n = struct.unpack(endian + "H", buf[off:off + 2])[0]
    for k in range(n):
        e = off + 2 + 12 * k
        if e + 12 > len(buf):
            break
        tag, typ, cnt = struct.unpack(endian + "HHI", buf[e:e + 8])
        if typ not in _TYPES:
            continue
        size, code = _TYPES[typ]
        total = size * cnt
        voff = e + 8 if total <= 4 else base + struct.unpack(endian + "I", buf[e + 8:e + 12])[0]
        raw = buf[voff:voff + total]
        if len(raw) < total:
            continue
        try:
            if code == "s":
                val = raw.split(b"\0")[0].decode("latin-1", "replace").strip()
            elif code in ("R", "r"):
                f = "I" if code == "R" else "i"
                nums = struct.unpack(endian + f * (2 * cnt), raw)
                val = [nums[2 * i] / nums[2 * i + 1] if nums[2 * i + 1] else 0.0 for i in range(cnt)]
                val = val[0] if cnt == 1 else val
            else:
                val = struct.unpack(endian + code * cnt, raw)
                val = val[0] if cnt == 1 else list(val)
        except struct.error:
            continue
        out[tag] = val
    nxt = off + 2 + 12 * n
    nxt_off = struct.unpack(endian + "I", buf[nxt:nxt + 4])[0] if nxt + 4 <= len(buf) else 0
    return out, nxt_off


def _tiff_block(buf):
    endian = "<" if buf[:2] == b"II" else ">"
    first = struct.unpack(endian + "I", buf[4:8])[0]
    ifd0, _ = _ifd(buf, first, endian, 0)
    exif, gps = {}, {}
    if 0x8769 in ifd0:
        exif, _ = _ifd(buf, int(ifd0[0x8769]), endian, 0)
    if 0x8825 in ifd0:
        gps, _ = _ifd(buf, int(ifd0[0x8825]), endian, 0)
    return ifd0, exif, gps


def read_exif(path):
    """{make, model, focal_mm, focal35, width, height, orientation, lat, lon, alt, time} — missing keys absent."""
    try:
        with open(path, "rb") as f:
            head = f.read(256 * 1024)
    except OSError:
        return {}
    tiff = None
    if head[:2] == b"\xff\xd8":
        i = 2
        while i + 4 <= len(head):
            if head[i] != 0xFF:
                break
            marker = head[i + 1]
            ln = struct.unpack(">H", head[i + 2:i + 4])[0]
            if marker == 0xE1 and head[i + 4:i + 10] == b"Exif\0\0":
                tiff = head[i + 10:i + 2 + ln]
                break
            if marker in (0xDA, 0xD9):
                break
            i += 2 + ln
    elif head[:4] in (b"II*\0", b"MM\0*"):
        tiff = head
    if not tiff or len(tiff) < 8:
        return {}
    try:
        ifd0, exif, gps = _tiff_block(tiff)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    if 0x010F in ifd0:
        out["make"] = str(ifd0[0x010F])
    if 0x0110 in ifd0:
        out["model"] = str(ifd0[0x0110])
    if 0x0112 in ifd0:
        out["orientation"] = int(ifd0[0x0112])
    if 0x920A in exif:
        out["focal_mm"] = float(exif[0x920A])
    if 0xA405 in exif and exif[0xA405]:
        out["focal35"] = float(exif[0xA405])
    if 0xA002 in exif and 0xA003 in exif:
        out["width"], out["height"] = int(exif[0xA002]), int(exif[0xA003])
    if 0x9003 in exif:
        out["time"] = str(exif[0x9003])

    def dms(v):
        v = v if isinstance(v, list) else [v]
        v = list(v) + [0, 0]
        return float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600

    try:
        if 2 in gps and 4 in gps:
            lat, lon = dms(gps[2]), dms(gps[4])
            if str(gps.get(1, "N")).upper().startswith("S"):
                lat = -lat
            if str(gps.get(3, "E")).upper().startswith("W"):
                lon = -lon
            if abs(lat) > 1e-9 or abs(lon) > 1e-9:
                out["lat"], out["lon"] = lat, lon
                if 6 in gps:
                    alt = float(gps[6])
                    if gps.get(5) in (1, b"\x01", [1]):
                        alt = -alt
                    out["alt"] = alt
    except (TypeError, ValueError, IndexError):
        pass
    return out


def focal_pixels(exif, width, height, default_factor=1.2):
    """Initial focal length in pixels: 35 mm equivalent, else a typical field of view."""
    longest = max(width, height)
    f35 = exif.get("focal35")
    if f35 and f35 > 5:
        return f35 / 36.0 * longest, "EXIF 35 mm"
    return default_factor * longest, "default (≈ 45° field of view)"


# ---------------------------------------------------------------- video
def video_info(path):
    cv2 = cv2_module()
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video {os.path.basename(path)}")
    info = {"frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), "fps": float(cap.get(cv2.CAP_PROP_FPS) or 0),
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}
    cap.release()
    info["duration"] = info["frames"] / info["fps"] if info["fps"] > 0 else 0.0
    return info


def extract_frames(path, out_dir, every_s=1.0, max_frames=300, start_s=0.0, end_s=0.0, sharpest=True,
                   prefix="frame", split="none", progress=None, cancel=None):
    """Write frames every `every_s` seconds as JPEG. With sharpest, the sharpest frame of each interval
    (variance of the Laplacian) is kept, so motion blur is avoided. split='lr' writes the left and right
    halves of a side-by-side stereo video as two files. Returns the written paths (lists of (L, R) for 'lr')."""
    cv2 = cv2_module()
    os.makedirs(out_dir, exist_ok=True)
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"Cannot open video {os.path.basename(path)}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    step = max(int(round(every_s * fps)), 1)
    first = int(start_s * fps)
    last = int(end_s * fps) if end_s > 0 else (total if total > 0 else 10 ** 9)
    if first > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, first)
    out = []
    i = first
    best, best_s = None, -1.0
    window_end = first + step
    while i < last and len(out) < max_frames:
        if cancel is not None and cancel():
            break
        ok = cap.grab()
        if not ok:
            break
        take = sharpest or i == window_end - step
        if take:
            ok, frame = cap.retrieve()
            if ok:
                if sharpest:
                    small = cv2.resize(frame, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
                    s = sharpness(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
                    if s > best_s:
                        best, best_s = frame, s
                else:
                    best = frame
        i += 1
        if i >= window_end:
            if best is not None:
                out.append(_write_frame(best, out_dir, prefix, len(out), split))
            best, best_s = None, -1.0
            window_end += step
            if progress is not None and total:
                progress(min((i - first) / max(min(last, total) - first, 1), 1.0))
    if best is not None and len(out) < max_frames:
        out.append(_write_frame(best, out_dir, prefix, len(out), split))
    cap.release()
    if not out:
        raise ValueError("No frames could be read from the video")
    return out


def _write_frame(frame, out_dir, prefix, k, split):
    if split == "lr":
        w = frame.shape[1] // 2
        pl = os.path.join(out_dir, f"{prefix}_{k:05d}_L.jpg")
        pr = os.path.join(out_dir, f"{prefix}_{k:05d}_R.jpg")
        imwrite(pl, frame[:, :w])
        imwrite(pr, frame[:, w:2 * w])
        return pl, pr
    p = os.path.join(out_dir, f"{prefix}_{k:05d}.jpg")
    imwrite(p, frame)
    return p


def split_side_by_side(files, out_dir):
    """Side-by-side stereo photos → (left, right) file pairs."""
    os.makedirs(out_dir, exist_ok=True)
    pairs = []
    for k, f in enumerate(files):
        img = imread(f)
        w = img.shape[1] // 2
        b = os.path.splitext(os.path.basename(f))[0]
        pl, pr = os.path.join(out_dir, f"{b}_L.jpg"), os.path.join(out_dir, f"{b}_R.jpg")
        imwrite(pl, img[:, :w])
        imwrite(pr, img[:, w:2 * w])
        pairs.append((pl, pr))
    return pairs

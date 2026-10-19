"""Synthetic photo set of a textured terrain (known geometry) — used by the self-test and for checking a build.

A height field is ray-cast from a grid of nadir / oblique cameras; the true terrain, camera centres and
GPS-like positions are returned so reconstructions can be checked against the truth."""
from __future__ import annotations

import os

import numpy as np


def _texture(size, seed):
    import cv2
    rng = np.random.default_rng(seed)
    tex = np.zeros((size, size, 3), np.float32)
    for s in (4, 8, 16, 32, 64, 128, 256, 512):
        if s > size:
            break
        layer = rng.random((s, s, 3)).astype(np.float32)
        tex += cv2.resize(layer, (size, size), interpolation=cv2.INTER_CUBIC) * (1.0 / np.sqrt(s) + 0.15)
    tex -= tex.min()
    tex /= tex.max()
    return (tex * 235 + 10).astype(np.uint8)


def height(x, y):
    return 3.0 * np.sin(x / 9.0) * np.cos(y / 11.0) + 0.04 * x + 1.5 * np.exp(-((x - 30) ** 2 + (y - 25) ** 2) / 60)


def make_scene(out_dir, n_x=4, n_y=3, size=(800, 600), f=700.0, altitude=45.0, extent=(60.0, 50.0), seed=3,
               origin=(250000.0, 2650000.0, 0.0), tilt_deg=8.0):
    """Write images; return dict(paths, centres_world, f, K, truth_fn, origin, extent)."""
    import cv2
    os.makedirs(out_dir, exist_ok=True)
    W, H = extent
    tex_px = 2048
    tex = _texture(tex_px, seed)
    w, h = size
    cx, cy = w / 2.0, h / 2.0
    xs = np.linspace(W * 0.2, W * 0.8, n_x)
    ys = np.linspace(H * 0.25, H * 0.75, n_y)
    paths, centres, rots = [], [], []
    rng = np.random.default_rng(seed)
    u, v = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    k = 0
    for j, y0 in enumerate(ys):
        row = xs if j % 2 == 0 else xs[::-1]
        for x0 in row:
            C = np.array([x0, y0, altitude + rng.normal(0, 1.0)])
            # camera looks down (nadir) with a small random tilt; image x → +X, image y → −Y
            yaw = np.radians(rng.normal(0, 5))
            tilt = np.radians(rng.normal(0, tilt_deg))
            Rz = np.array([[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]])
            Rx = np.array([[1, 0, 0], [0, np.cos(tilt), -np.sin(tilt)], [0, np.sin(tilt), np.cos(tilt)]])
            base = np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1.0]])        # world→camera for nadir
            R = Rx @ base @ Rz.T
            d_cam = np.stack([(u - cx) / f, (v - cy) / f, np.ones_like(u)], axis=-1)
            d = d_cam @ R                                                   # camera → world directions
            tz = (np.mean(height(np.array([W / 2]), np.array([H / 2]))) - C[2]) / d[..., 2]
            for _ in range(25):                                             # fixed point on the height field
                X = C[0] + tz * d[..., 0]
                Y = C[1] + tz * d[..., 1]
                tz = (height(X, Y) - C[2]) / d[..., 2]
            X = C[0] + tz * d[..., 0]
            Y = C[1] + tz * d[..., 1]
            mx = (X / W * (tex_px - 1)).astype(np.float32)
            my = (Y / H * (tex_px - 1)).astype(np.float32)
            img = cv2.remap(tex, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            # mild shading by slope so the texture is not the only cue
            p = os.path.join(out_dir, f"img_{k:03d}.jpg")
            cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tofile(p)
            paths.append(p)
            centres.append(C)
            rots.append(R)
            k += 1
    centres = np.array(centres) + np.asarray(origin)
    return {"paths": paths, "centres": centres, "rotations": np.array(rots), "f": f,
            "truth": lambda x, y: height(x - origin[0], y - origin[1]) + origin[2],
            "origin": np.asarray(origin, float), "extent": extent}


def make_stereo_scene(out_dir, n=3, size=(800, 600), f=700.0, baseline=0.6, distance=12.0, seed=5):
    """A textured wall / slope seen by a horizontal stereo rig moving sideways. Returns dict with the
    left / right paths, calibration and the truth (distance to the surface along Y)."""
    import cv2
    os.makedirs(out_dir, exist_ok=True)
    tex_px = 2048
    tex = _texture(tex_px, seed)
    w, h = size
    cx, cy = w / 2.0, h / 2.0
    u, v = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)

    def surf(x, z):                                       # wall at Y = distance + relief(x, z)
        return distance + 0.8 * np.sin(x / 2.0) * np.cos(z / 1.7)

    L, R_ = [], []
    for k in range(n):
        for side, lst in ((0.0, L), (baseline, R_)):
            C = np.array([k * 2.5 + side, 0.0, 1.5])
            # camera looks along +Y, image x → +X, image y → −Z
            Rm = np.array([[1, 0, 0], [0, 0, -1.0], [0, 1.0, 0]])
            d = np.stack([(u - cx) / f, (v - cy) / f, np.ones_like(u)], axis=-1) @ Rm
            t = (distance - C[1]) / d[..., 1]
            for _ in range(20):
                X = C[0] + t * d[..., 0]
                Z = C[2] + t * d[..., 2]
                t = (surf(X, Z) - C[1]) / d[..., 1]
            X = C[0] + t * d[..., 0]
            Z = C[2] + t * d[..., 2]
            mx = ((X + 5) / 20 * (tex_px - 1)).astype(np.float32)
            my = ((Z + 5) / 15 * (tex_px - 1)).astype(np.float32)
            img = cv2.remap(tex, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            p = os.path.join(out_dir, f"{'L' if side == 0 else 'R'}_{k:03d}.jpg")
            cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tofile(p)
            lst.append(p)
    return {"left": L, "right": R_, "f": f, "cx": cx, "cy": cy, "baseline": baseline, "surface": surf}

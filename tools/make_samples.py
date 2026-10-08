"""Generate test point clouds: XYZ with header, CSV with RGB, and LAZ.

Usage: python tools/make_samples.py [output_dir]
"""
import os
import sys

import laspy
import numpy as np

out = sys.argv[1] if len(sys.argv) > 1 else "samples"
os.makedirs(out, exist_ok=True)
rng = np.random.default_rng(0)

# Synthetic seabed: TWD97 coordinates, gentle slope + sand waves, ~ -20 m depth
n = 400_000
x = 160_000 + rng.uniform(0, 800, n)
y = 2_620_000 + rng.uniform(0, 300, n)
z = -20 - 0.004 * (x - 160_000) + 0.6 * np.sin((x - 160_000) / 15) + rng.normal(0, 0.03, n)
inten = (np.clip(z + 25, 0, 10) * 300).astype(int)

with open(os.path.join(out, "seabed_header.xyz"), "w", encoding="utf-8") as f:
    f.write("# Synthetic seabed TWD97\n# Survey date 2026-10-01\nEasting Northing Depth Intensity\n")
    np.savetxt(f, np.column_stack([x, y, z, inten]), fmt="%.3f %.3f %.3f %d")

r = (np.clip((z + 24) / 6, 0, 1) * 255).astype(int)
with open(os.path.join(out, "seabed_rgb.csv"), "w", encoding="utf-8") as f:
    np.savetxt(f, np.column_stack([x, y, z, r, 128 + 0 * r, 255 - r]), fmt="%.3f,%.3f,%.3f,%d,%d,%d")

hdr = laspy.LasHeader(point_format=3, version="1.2")
hdr.scales = [0.001, 0.001, 0.001]
hdr.offsets = [160_000, 2_620_000, 0]
las = laspy.LasData(hdr)
las.x, las.y, las.z = x, y, z
las.intensity = inten
las.classification = np.where(z < -22, 2, 1).astype(np.uint8)
las.red, las.green, las.blue = r * 257, np.full(n, 128 * 257), (255 - r) * 257
las.write(os.path.join(out, "seabed.laz"))
print("Samples written to", os.path.abspath(out))

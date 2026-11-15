"""Lanelet2 map export (OSM XML, as read by lanelet2.io with a UTM / local projector and by Autoware).

Nodes carry WGS 84 lat / lon and the project coordinates as local_x / local_y / ele. Line features become ways
tagged type / subtype (line_thin, curbstone, stop_line …), areas become closed ways with area=yes, point
features standalone nodes, and lanelets relations with their left / right boundary ways."""
from __future__ import annotations

from xml.sax.saxutils import quoteattr

import numpy as np

from core.hdmap import FEATURE_TYPES


def _tags(d):
    return "".join(f'    <tag k={quoteattr(str(k))} v={quoteattr(str(v))}/>\n' for k, v in d.items()
                   if v not in (None, ""))


def write_lanelet2(path, layer, crs, location="urban", default_speed=None):
    """layer: HD map VectorLayer; crs: pyproj CRS of the coordinates. Returns counts."""
    from pyproj import CRS, Transformer
    if crs is None:
        raise ValueError("The project has no coordinate system — Lanelet2 needs latitude / longitude")
    tr = Transformer.from_crs(CRS.from_user_input(crs), CRS.from_epsg(4326), always_xy=True)
    nodes = []                     # (id, x, y, z, tags)
    ways = []                      # (id, [node ids], tags)
    rels = []                      # (id, members, tags)
    fid_way = {}
    nid = [0]

    def node(p, tags=None):
        nid[0] += 1
        z = float(p[2]) if len(p) > 2 and np.isfinite(p[2]) else 0.0
        nodes.append((nid[0], float(p[0]), float(p[1]), z, tags or {}))
        return nid[0]

    wid = [1_000_000]
    for f in layer.features:
        a = f["attrs"]
        t = a.get("TYPE")
        if t not in FEATURE_TYPES or t == "lanelet":
            continue
        ltype = FEATURE_TYPES[t][3]
        p = np.asarray(f["parts"][0], float)
        tags = {"type": ltype, "subtype": a.get("SUBTYPE") or None}
        if a.get("COLOR") and t.startswith("lane_line"):
            tags["color"] = a["COLOR"]
        if a.get("WIDTH") not in (None, ""):
            tags["width"] = f"{float(a['WIDTH']):g}"
        if a.get("HEIGHT") not in (None, ""):
            tags["height"] = f"{float(a['HEIGHT']):g}"
        if f["type"] == "Point":
            node(p[0], {**tags, "pcs_fid": a.get("FID")})
            continue
        closed = f["type"] == "Polygon"
        pts = p[:-1] if closed and np.allclose(p[0], p[-1]) else p
        ids = [node(q) for q in pts]
        if closed:
            ids.append(ids[0])
            tags["area"] = "yes"
        wid[0] += 1
        ways.append((wid[0], ids, {**tags, "pcs_fid": a.get("FID")}))
        fid_way[int(a.get("FID") or 0)] = wid[0]
    rid = 2_000_000
    missing = 0
    for f in layer.features:
        a = f["attrs"]
        if a.get("TYPE") != "lanelet":
            continue
        lw, rw = fid_way.get(int(a.get("LEFT_FID") or -1)), fid_way.get(int(a.get("RIGHT_FID") or -1))
        if lw is None or rw is None:
            missing += 1
            continue
        rid += 1
        sp = a.get("SPEED") if a.get("SPEED") not in (None, "") else default_speed
        rels.append((rid, [("way", "left", lw), ("way", "right", rw)],
                     {"type": "lanelet", "subtype": a.get("SUBTYPE") or "road", "location": location,
                      "one_way": "yes", "speed_limit": None if sp in (None, "") else f"{float(sp):g}",
                      "participant:vehicle": "yes" if (a.get("SUBTYPE") or "road") == "road" else None,
                      "lane_id": a.get("LANE_ID") or None, "pcs_fid": a.get("FID")}))
    xs = np.array([n[1] for n in nodes]) if nodes else np.empty(0)
    ys = np.array([n[2] for n in nodes]) if nodes else np.empty(0)
    lon, lat = tr.transform(xs, ys) if len(xs) else (np.empty(0), np.empty(0))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="PointCloud Studio">\n')
        for (i, x, y, z, tags), lo, la in zip(nodes, lon, lat):
            t = {"ele": f"{z:.3f}", "local_x": f"{x:.3f}", "local_y": f"{y:.3f}", **tags}
            fh.write(f'  <node id="{i}" visible="true" version="1" lat="{la:.10f}" lon="{lo:.10f}">\n{_tags(t)}'
                     "  </node>\n")
        for i, ids, tags in ways:
            fh.write(f'  <way id="{i}" visible="true" version="1">\n')
            fh.write("".join(f'    <nd ref="{r}"/>\n' for r in ids))
            fh.write(_tags(tags) + "  </way>\n")
        for i, mem, tags in rels:
            fh.write(f'  <relation id="{i}" visible="true" version="1">\n')
            fh.write("".join(f'    <member type="{t}" ref="{r}" role="{role}"/>\n' for t, role, r in mem))
            fh.write(_tags(tags) + "  </relation>\n")
        fh.write("</osm>\n")
    return {"nodes": len(nodes), "ways": len(ways), "lanelets": len(rels), "lanelets_skipped": missing,
            "origin_lat": float(np.mean(lat)) if len(lat) else None,
            "origin_lon": float(np.mean(lon)) if len(lon) else None}

"""Triangle meshes from any point cloud (survey, LiDAR, MBES or photogrammetry).

Methods
  delaunay25d  2.5D Delaunay of the XY plane (terrain, seabed) — one Z per position
  alpha3d      3D alpha shape (SciPy Delaunay tetrahedra) — closed surfaces of objects, stockpiles, structures
  poisson      screened Poisson reconstruction (Open3D, optional) — smooth watertight surfaces
  ballpivot    ball pivoting (Open3D, optional) — keeps the measured points as vertices

Post-processing: small-component removal, Taubin smoothing, vertex-clustering decimation, colours from the
nearest source point. Exports: OBJ (vertex colours), PLY (binary), STL (binary), DXF (3DFACE).
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field

import numpy as np

METHODS = {"delaunay25d": "2.5D Delaunay (terrain / seabed)",
           "alpha3d": "3D alpha shape (objects, structures)",
           "poisson": "Poisson surface (Open3D)",
           "ballpivot": "Ball pivoting (Open3D)"}
EXPORT_FORMATS = {".obj": "Wavefront OBJ (*.obj)", ".ply": "PLY binary (*.ply)", ".stl": "STL binary (*.stl)",
                  ".dxf": "DXF 3DFACE (*.dxf)"}


def _report(progress, f):
    if progress is not None:
        progress(float(min(max(f, 0.0), 1.0)))


def open3d_available():
    """Installed? Checked WITHOUT importing: Open3D is large and brings its own native GUI libraries, so it
    is only imported when a Poisson / ball-pivoting mesh is actually built (a broken install then gives a
    normal error message instead of affecting the dialog or the app)."""
    import importlib.util
    try:
        return importlib.util.find_spec("open3d") is not None
    except (ImportError, ValueError):
        return False


# ---------------------------------------------------------------- model
@dataclass
class Mesh:
    vertices: np.ndarray                    # (N, 3) float64 world coordinates
    faces: np.ndarray                       # (M, 3) int32
    colors: np.ndarray | None = None        # (N, 3) uint8 RGB or None
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        self.vertices = np.ascontiguousarray(self.vertices, dtype=np.float64)
        self.faces = np.ascontiguousarray(self.faces, dtype=np.int32).reshape(-1, 3)
        if self.colors is not None:
            self.colors = np.ascontiguousarray(self.colors, dtype=np.uint8).reshape(-1, 3)
            if len(self.colors) != len(self.vertices):
                self.colors = None
        self._cache = {}

    # Meshes are not modified in place (every operation returns a new Mesh), so the derived values are
    # cached: the Layers panel asks for them on every refresh (each point edit).
    def _cached(self, key, fn):
        c = self.__dict__.setdefault("_cache", {})
        if key not in c:
            c[key] = fn()
        return c[key]

    def __len__(self):
        return len(self.faces)

    def bbox(self):
        lo, hi = self._cached("bbox", lambda: (self.vertices.min(axis=0), self.vertices.max(axis=0)))
        return lo.copy(), hi.copy()

    def face_areas(self):
        v = self.vertices
        f = self.faces
        return 0.5 * np.linalg.norm(np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]]), axis=1)

    @property
    def area(self):
        return self._cached("area", lambda: float(self.face_areas().sum()))

    def boundary_edges(self):
        e = np.sort(np.vstack([self.faces[:, [0, 1]], self.faces[:, [1, 2]], self.faces[:, [2, 0]]]), axis=1)
        u, c = np.unique(e, axis=0, return_counts=True)
        return u[c == 1]

    @property
    def is_closed(self):
        return self._cached("closed", lambda: len(self.faces) > 0 and len(self.boundary_edges()) == 0)

    @property
    def volume(self):
        """Enclosed volume (closed meshes only), else NaN."""
        def calc():
            if not self.is_closed:
                return float("nan")
            v = self.vertices - self.vertices.mean(axis=0)
            f = self.faces
            return float(abs(np.einsum("ij,ij->i", v[f[:, 0]], np.cross(v[f[:, 1]], v[f[:, 2]])).sum()) / 6.0)
        return self._cached("volume", calc)



    def stats(self):
        lo, hi = self.bbox()
        out = {"vertices": len(self.vertices), "triangles": len(self.faces), "area_m2": self.area,
               "closed": self.is_closed, "bbox_min": lo.tolist(), "bbox_max": hi.tolist()}
        if out["closed"]:
            out["volume_m3"] = self.volume
        return out

    def size_text(self):
        t = f"{len(self.faces):,} tri · {self.area:,.1f} m²"
        if self.is_closed:
            t += f" · closed {self.volume:,.1f} m³"
        return t


def merge(meshes, meta=None):
    meshes = [m for m in meshes if m is not None and len(m.faces)]
    if not meshes:
        raise ValueError("Nothing to merge")
    V, F, C, off = [], [], [], 0
    has_c = all(m.colors is not None for m in meshes)
    for m in meshes:
        V.append(m.vertices)
        F.append(m.faces + off)
        off += len(m.vertices)
        if has_c:
            C.append(m.colors)
    return Mesh(np.vstack(V), np.vstack(F), np.vstack(C) if has_c else None, dict(meta or {}))


# ---------------------------------------------------------------- helpers
def colors_of(pc):
    """(N, 3) uint8 RGB of a PointCloud (16-bit LAS colours scaled down) or None."""
    a = pc.attrs
    if not all(k in a for k in ("R", "G", "B")):
        return None
    c = np.column_stack([np.asarray(a[k], float) for k in ("R", "G", "B")])
    if np.nanmax(c) > 255:
        c = c / 257.0
    return np.clip(np.nan_to_num(c), 0, 255).astype(np.uint8)


def voxel_thin(xyz, idx, cell):
    """One point per voxel (closest to the voxel centroid). Returns indices."""
    idx = np.asarray(idx)
    if cell <= 0 or len(idx) == 0:
        return idx
    p = xyz[idx]
    k = np.floor((p - p.min(axis=0)) / cell).astype(np.int64)
    dims = k.max(axis=0) + 1
    key = (k[:, 0] * dims[1] + k[:, 1]) * dims[2] + k[:, 2]
    order = np.argsort(key, kind="stable")
    ks = key[order]
    start = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    cnt = np.diff(np.r_[start, len(ks)])
    grp = np.repeat(np.arange(len(start)), cnt)
    cen = np.zeros((len(start), 3))
    for j in range(3):
        cen[:, j] = np.bincount(grp, weights=p[order, j]) / cnt
    d = ((p[order] - cen[grp]) ** 2).sum(axis=1)
    o2 = np.lexsort((d, grp))
    first = np.flatnonzero(np.r_[True, grp[o2][1:] != grp[o2][:-1]])
    return np.sort(idx[order[o2[first]]])


def mean_spacing(xyz, idx, sample=20000):
    from scipy.spatial import cKDTree
    idx = np.asarray(idx)
    if len(idx) < 3:
        return 1.0
    # the tree is built on at most 1 M points (huge clouds would take minutes / GBs); the spacing of a
    # random subset grows as (n / m)^(1/2) for a surface-like cloud, so it is scaled back
    n = len(idx)
    m = min(n, 1_000_000)
    rng = np.random.default_rng(0)
    sub = idx if m == n else idx[rng.choice(n, m, replace=False)]
    p = xyz[sub]
    s = p if len(p) <= sample else p[rng.choice(len(p), sample, replace=False)]
    d, _ = cKDTree(p).query(s, k=2)
    v = d[:, 1]
    v = v[v > 0]
    sp = float(np.median(v)) if len(v) else 1.0
    return sp * np.sqrt(m / n)


def transfer_colors(mesh, xyz, rgb):
    if rgb is None or len(xyz) == 0:
        return mesh
    from scipy.spatial import cKDTree
    _, j = cKDTree(xyz).query(mesh.vertices, k=1)
    mesh.colors = rgb[j].astype(np.uint8)
    return mesh


def estimate_normals(p, k=20, orient="up", viewpoints=None):
    """PCA normals (k nearest neighbours). orient: 'up' (+Z), 'out' (away from centroid) or 'view'."""
    from scipy.spatial import cKDTree
    k = int(min(max(k, 4), len(p)))
    tree = cKDTree(p)
    n = np.empty((len(p), 3))
    w0 = np.empty(len(p))
    wsum = np.empty(len(p))
    for a in range(0, len(p), 200_000):                   # chunks: (n, k, 3) arrays stay small
        b = min(a + 200_000, len(p))
        _, nn = tree.query(p[a:b], k=k)
        q = p[nn] - p[nn].mean(axis=1, keepdims=True)
        w, v = np.linalg.eigh(np.einsum("nki,nkj->nij", q, q))
        n[a:b] = v[:, :, 0]
        w0[a:b], wsum[a:b] = w[:, 0], w.sum(axis=1)
    if orient == "view" and viewpoints is not None:
        d = viewpoints - p
    elif orient == "out":
        d = p - p.mean(axis=0)
    else:
        d = np.tile([0.0, 0.0, 1.0], (len(p), 1))
    n[(n * d).sum(axis=1) < 0] *= -1
    curv = w0 / np.maximum(wsum, 1e-30)
    return n, curv


# ---------------------------------------------------------------- methods
def delaunay25d(xyz, idx, max_edge=0.0, thin_cell=0.0, rgb=None, progress=None):
    from core.surface import build_tin, thin_grid
    idx = np.asarray(idx)
    if thin_cell > 0:
        idx = thin_grid(xyz, idx, thin_cell, "median")
    tin = build_tin(xyz, idx, max_edge, 0.0, progress=progress)
    return Mesh(tin.vertices, tin.triangles, None if rgb is None else rgb[idx],
                {"method": "delaunay25d", "max_edge": max_edge, "thin_cell": thin_cell})


def _circumradius_tets(P, T):
    a, b, c, d = (P[T[:, i]] for i in range(4))
    A = np.stack([b - a, c - a, d - a], axis=1)                    # (n, 3, 3)
    rhs = 0.5 * np.stack([(b - a) ** 2, (c - a) ** 2, (d - a) ** 2], axis=1).sum(axis=2)
    det = np.linalg.det(A)
    ok = np.abs(det) > 1e-18
    r = np.full(len(T), np.inf)
    if ok.any():
        x = np.linalg.solve(A[ok], rhs[ok][..., None])[..., 0]
        r[ok] = np.linalg.norm(x, axis=1)
    return r


def _alpha_surface(P, T, nb, R, alpha, fill):
    keep = R <= alpha
    if not keep.any():
        return None, None
    if fill:
        # exterior = tetrahedra above alpha that are connected to the outside; everything else is solid,
        # so a scanned shell gives one outer surface (cavities filled) instead of an inner and an outer one
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
        nt = len(T)
        free = ~keep
        rows, cols = [], []
        for k in range(4):
            j = nb[:, k]
            m1 = free & (j >= 0)
            m1[m1] &= free[j[m1]]
            rows.append(np.flatnonzero(m1))
            cols.append(j[m1])
            m2 = free & (j < 0)
            rows.append(np.flatnonzero(m2))
            cols.append(np.full(int(m2.sum()), nt))
        r, c = np.concatenate(rows), np.concatenate(cols)
        G = coo_matrix((np.ones(len(r)), (r, c)), shape=(nt + 1, nt + 1))
        _, lab = connected_components(G, directed=False)
        solid = ~(free & (lab[:nt] == lab[nt]))
    else:
        solid = keep
    faces_of = ((1, 2, 3), (0, 3, 2), (0, 1, 3), (0, 2, 1))          # face k is opposite vertex k
    F, O = [], []
    for k in range(4):
        j = nb[:, k]
        b = solid & ((j < 0) | ~solid[np.maximum(j, 0)])
        F.append(T[b][:, list(faces_of[k])])
        O.append(T[b, k])
    F, O = np.vstack(F), np.concatenate(O)
    if len(F) == 0:
        return None, None
    n = np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]])
    flip = (n * (P[O] - P[F[:, 0]])).sum(axis=1) > 0                  # normal must point away from the tet
    F[flip] = F[flip][:, [0, 2, 1]]
    return F, solid


def alpha3d(xyz, idx, alpha=0.0, cell=0.0, max_points=400_000, fill=True, rgb=None, progress=None):
    """Boundary of the union of Delaunay tetrahedra with circumradius ≤ alpha.

    fill: cavities not connected to the outside are solid → one closed outer surface.
    alpha ≤ 0: automatic — starts from the sampling gaps and grows until the surface encloses a solid
    (a scanned shell must not come out as a double layer)."""
    from scipy.spatial import Delaunay
    idx = np.asarray(idx)
    sp = mean_spacing(xyz, idx)
    if cell <= 0 and len(idx) > max_points:
        cell = sp * (len(idx) / max_points) ** (1 / 2.5)
    if cell > 0:
        idx = voxel_thin(xyz, idx, cell)
        sp = max(sp, cell)
    while len(idx) > max_points * 1.3:
        cell = max(cell, sp) * 1.3
        idx = voxel_thin(xyz, idx, cell)
    if len(idx) < 4:
        raise ValueError("At least 4 points are needed")
    auto = alpha <= 0
    if auto:                                                        # start: bridge the sampling gaps
        from scipy.spatial import cKDTree
        Q = xyz[idx]
        s_ = Q if len(Q) <= 20000 else Q[np.random.default_rng(0).choice(len(Q), 20000, replace=False)]
        d8, _ = cKDTree(Q).query(s_, k=min(9, len(Q)))
        alpha = 4.0 * float(np.percentile(d8[:, -1], 99))
    _report(progress, 0.1)
    P = xyz[idx]
    c0 = P.mean(axis=0)
    P0 = P - c0
    dl = Delaunay(P0, qhull_options="Qbb Qc Qz Q12")
    _report(progress, 0.6)
    T, nb = dl.simplices, dl.neighbors
    R = _circumradius_tets(P0, T)
    F = None
    for _ in range(4 if auto else 1):                  # auto: at most ×4 growth (open surfaces stay thin)
        F, solid = _alpha_surface(P0, T, nb, R, alpha, fill)
        if F is None:
            if not auto:
                raise ValueError("No tetrahedra below the alpha radius — increase alpha")
            alpha *= 1.6
            continue
        if not (auto and fill):
            break
        m_ = Mesh(P0, F)
        vol = m_.volume if m_.is_closed else 0.0
        if vol >= 0.25 * alpha * m_.area:                           # encloses a solid, not a thin double layer
            break
        alpha *= 1.6
    if F is None:
        raise ValueError("No surface found — set an alpha radius")
    used = np.unique(F)
    remap = -np.ones(len(P), np.int64)
    remap[used] = np.arange(len(used))
    _report(progress, 1.0)
    return Mesh(P[used], remap[F], None if rgb is None else rgb[idx][used],
                {"method": "alpha3d", "alpha": float(alpha), "cell": float(cell), "fill": bool(fill)})


def _o3d_cloud(xyz, idx, cell, normals_orient, max_points=3_000_000):
    import open3d as o3d
    idx = np.asarray(idx)
    if cell > 0:
        idx = voxel_thin(xyz, idx, cell)
    if len(idx) > max_points:                         # keep memory / time bounded on huge clouds
        sp = mean_spacing(xyz, idx)
        idx = voxel_thin(xyz, idx, sp * np.sqrt(len(idx) / max_points))
    c0 = xyz[idx].mean(axis=0)
    P = xyz[idx] - c0
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(P)
    n, _ = estimate_normals(P, 20, "out" if normals_orient == "out" else "up")
    pcd.normals = o3d.utility.Vector3dVector(n)
    if normals_orient == "consistent" and len(P) < 300_000:
        pcd.orient_normals_consistent_tangent_plane(15)
    return pcd, idx, c0


def poisson(xyz, idx, depth=9, trim=0.05, cell=0.0, normals="up", rgb=None, progress=None):
    import open3d as o3d
    pcd, idx, c0 = _o3d_cloud(xyz, idx, cell, normals)
    _report(progress, 0.2)
    m, dens = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=int(depth))
    dens = np.asarray(dens)
    _report(progress, 0.8)
    if trim > 0 and len(dens):
        m.remove_vertices_by_mask(dens < np.quantile(dens, trim))
    lo, hi = xyz[idx].min(axis=0) - c0, xyz[idx].max(axis=0) - c0
    pad = 0.02 * (hi - lo) + 1e-6
    m = m.crop(o3d.geometry.AxisAlignedBoundingBox(lo - pad, hi + pad))
    out = Mesh(np.asarray(m.vertices) + c0, np.asarray(m.triangles),
               meta={"method": "poisson", "depth": int(depth), "trim": trim, "cell": cell})
    out = clean(out)
    if rgb is not None:
        transfer_colors(out, xyz[idx], rgb[idx])
    _report(progress, 1.0)
    return out


def ballpivot(xyz, idx, radii=None, cell=0.0, rgb=None, progress=None):
    import open3d as o3d
    pcd, idx, c0 = _o3d_cloud(xyz, idx, cell, "up")
    sp = mean_spacing(xyz, idx)
    radii = radii or [1.5 * sp, 3 * sp, 6 * sp]
    _report(progress, 0.2)
    m = o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(pcd, o3d.utility.DoubleVector(radii))
    out = Mesh(np.asarray(m.vertices) + c0, np.asarray(m.triangles), meta={"method": "ballpivot",
                                                                            "radii": [float(r) for r in radii]})
    if rgb is not None:
        transfer_colors(out, xyz[idx], rgb[idx])
    _report(progress, 1.0)
    return out


def build_mesh(xyz, idx, method="delaunay25d", rgb=None, smooth=0, min_component=0.0, decimate=0.0,
               progress=None, **kw):
    """Run one method then the post-processing. kw goes to the method."""
    fn = {"delaunay25d": delaunay25d, "alpha3d": alpha3d, "poisson": poisson, "ballpivot": ballpivot}[method]
    if method in ("poisson", "ballpivot"):
        if not open3d_available():
            raise ModuleNotFoundError("No module named 'open3d'", name="open3d")
        try:
            import open3d  # noqa: F401
        except Exception as e:  # noqa: BLE001 — installed but not loadable (missing sub-dependency, wrong arch)
            raise RuntimeError(f"Open3D is installed but cannot be loaded ({type(e).__name__}: {e}) — use the "
                               "2.5D Delaunay or 3D alpha shape method, or reinstall Open3D") from e
    m = fn(xyz, idx, rgb=rgb, progress=progress, **kw)
    m = clean(m)
    if min_component > 0:
        m = remove_small_components(m, min_component)
    if smooth > 0:
        m = taubin_smooth(m, int(smooth))
    if decimate > 0:
        m = decimate_clustering(m, decimate)
        if rgb is not None:
            transfer_colors(m, xyz[np.asarray(idx)], rgb[np.asarray(idx)])
    m.meta.update(source_points=int(len(idx)), smooth=int(smooth), decimate=float(decimate))
    if len(m.faces) == 0:
        raise ValueError("The mesh is empty — try other settings")
    return m


# ---------------------------------------------------------------- post-processing
def clean(m):
    """Drop degenerate / duplicate faces and unused vertices."""
    F = m.faces
    if len(F) == 0:
        return m
    ok = (F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])
    F = F[ok]
    _, first = np.unique(np.sort(F, axis=1), axis=0, return_index=True)
    F = F[np.sort(first)]
    used = np.unique(F)
    remap = -np.ones(len(m.vertices), np.int64)
    remap[used] = np.arange(len(used))
    return Mesh(m.vertices[used], remap[F], None if m.colors is None else m.colors[used], m.meta)


def _adjacency(m):
    from scipy.sparse import coo_matrix
    F = m.faces
    i = np.concatenate([F[:, 0], F[:, 1], F[:, 2], F[:, 1], F[:, 2], F[:, 0]])
    j = np.concatenate([F[:, 1], F[:, 2], F[:, 0], F[:, 0], F[:, 1], F[:, 2]])
    n = len(m.vertices)
    A = coo_matrix((np.ones(len(i)), (i, j)), shape=(n, n)).tocsr()
    A.data[:] = 1.0
    return A


def remove_small_components(m, min_area):
    from scipy.sparse.csgraph import connected_components
    if len(m.faces) == 0:
        return m
    _, lab = connected_components(_adjacency(m), directed=False)
    fl = lab[m.faces[:, 0]]
    area = np.bincount(fl, weights=m.face_areas(), minlength=lab.max() + 1)
    keep = area[fl] >= min_area
    return clean(Mesh(m.vertices, m.faces[keep], m.colors, m.meta))


def taubin_smooth(m, iterations=5, lam=0.5, mu=-0.53):
    A = _adjacency(m)
    deg = np.asarray(A.sum(axis=1)).ravel()
    deg[deg == 0] = 1
    fixed = np.zeros(len(m.vertices), bool)
    be = m.boundary_edges()
    if len(be):
        fixed[be.ravel()] = True                     # keep the outline in place
    V = m.vertices.copy()
    for _ in range(int(iterations)):
        for f in (lam, mu):
            L = (A @ V) / deg[:, None] - V
            L[fixed] = 0
            V = V + f * L
    return Mesh(V, m.faces, m.colors, m.meta)


def decimate_clustering(m, cell):
    """Merge the vertices in each cell³ voxel (mean position); faces that collapse are dropped."""
    if cell <= 0:
        return m
    V = m.vertices
    k = np.floor((V - V.min(axis=0)) / cell).astype(np.int64)
    dims = k.max(axis=0) + 1
    key = (k[:, 0] * dims[1] + k[:, 1]) * dims[2] + k[:, 2]
    u, inv = np.unique(key, return_inverse=True)
    inv = inv.ravel()
    cnt = np.bincount(inv)
    NV = np.column_stack([np.bincount(inv, weights=V[:, j]) / cnt for j in range(3)])
    NC = None
    if m.colors is not None:
        NC = np.column_stack([np.bincount(inv, weights=m.colors[:, j].astype(float)) / cnt for j in range(3)])
    return clean(Mesh(NV, inv[m.faces], NC, m.meta))


def vertex_normals(V, F):
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    n = np.zeros((len(V), 3))
    for k in range(3):
        for j in range(3):
            n[:, j] += np.bincount(F[:, k], weights=fn[:, j], minlength=len(V))
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


# ---------------------------------------------------------------- primitives (used by the modelling tools)
def cylinder_mesh(center, axis, radius, h0, h1, segments=32, caps=True, color=None):
    a = np.asarray(axis, float)
    a /= np.linalg.norm(a)
    u = np.cross(a, [1.0, 0, 0] if abs(a[0]) < 0.9 else [0, 1.0, 0])
    u /= np.linalg.norm(u)
    w = np.cross(a, u)
    t = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    ring = radius * (np.cos(t)[:, None] * u + np.sin(t)[:, None] * w)
    c = np.asarray(center, float)
    bot, top = c + a * h0 + ring, c + a * h1 + ring
    V = [bot, top]
    i = np.arange(segments)
    j = (i + 1) % segments
    F = [np.column_stack([i, j, segments + j]), np.column_stack([i, segments + j, segments + i])]
    if caps:
        V += [(c + a * h0)[None], (c + a * h1)[None]]
        cb, ct = 2 * segments, 2 * segments + 1
        F += [np.column_stack([np.full(segments, cb), j, i]),
              np.column_stack([np.full(segments, ct), segments + i, segments + j])]
    V = np.vstack(V)
    col = None if color is None else np.tile(np.asarray(color, np.uint8), (len(V), 1))
    return Mesh(V, np.vstack(F), col)


def ear_clip(poly):
    """Triangulate a simple polygon (N, 2), counter-clockwise or not. Returns (M, 3) indices."""
    P = np.asarray(poly, float)
    n = len(P)
    if n < 3:
        return np.empty((0, 3), int)
    area = 0.5 * np.sum(P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1])
    idx = list(range(n)) if area > 0 else list(range(n))[::-1]
    out = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    guard = 0
    while len(idx) > 3 and guard < 10 * n * n:
        guard += 1
        m = len(idx)
        cut = False
        for k in range(m):
            i0, i1, i2 = idx[(k - 1) % m], idx[k], idx[(k + 1) % m]
            a, b, c = P[i0], P[i1], P[i2]
            if cross(a, b, c) <= 1e-12:
                continue
            inside = False
            for q in idx:
                if q in (i0, i1, i2):
                    continue
                p = P[q]
                if cross(a, b, p) >= 0 and cross(b, c, p) >= 0 and cross(c, a, p) >= 0:
                    inside = True
                    break
            if not inside:
                out.append((i0, i1, i2))
                idx.pop(k)
                cut = True
                break
        if not cut:                                   # degenerate remainder: fan it
            break
    for k in range(1, len(idx) - 1):
        out.append((idx[0], idx[k], idx[k + 1]))
    return np.asarray(out, int).reshape(-1, 3)


def prism_mesh(ring_xy, z_bottom, z_top, color=None):
    """Closed prism over a polygon. z_top may be a scalar or one value per ring vertex."""
    R = np.asarray(ring_xy, float)
    if np.allclose(R[0], R[-1]):
        R = R[:-1]
    n = len(R)
    zt = np.broadcast_to(np.asarray(z_top, float), (n,))
    zb = np.broadcast_to(np.asarray(z_bottom, float), (n,))
    V = np.vstack([np.column_stack([R, zb]), np.column_stack([R, zt])])
    tri = ear_clip(R)
    area = 0.5 * np.sum(R[:, 0] * np.roll(R[:, 1], -1) - np.roll(R[:, 0], -1) * R[:, 1])
    i = np.arange(n)
    j = (i + 1) % n
    walls = np.vstack([np.column_stack([i, j, n + j]), np.column_stack([i, n + j, n + i])])
    if area < 0:
        walls = walls[:, ::-1]
    F = np.vstack([tri[:, ::-1], tri + n, walls])
    col = None if color is None else np.tile(np.asarray(color, np.uint8), (len(V), 1))
    return Mesh(V, F, col)


# ---------------------------------------------------------------- export
def _offset(m, local):
    if not local:
        return m.vertices, np.zeros(3)
    o = np.floor(m.vertices.min(axis=0))
    return m.vertices - o, o


def write_obj(m, path, local=False):
    V, o = _offset(m, local)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# PointCloud Studio mesh\n# vertices {len(V)}  faces {len(m.faces)}\n")
        if local:
            f.write(f"# offset {o[0]:.3f} {o[1]:.3f} {o[2]:.3f} (add to get world coordinates)\n")
        if m.colors is not None:
            c = m.colors / 255.0
            rows = np.column_stack([V, c])
            np.savetxt(f, rows, fmt="v %.4f %.4f %.4f %.4f %.4f %.4f")
        else:
            np.savetxt(f, V, fmt="v %.4f %.4f %.4f")
        np.savetxt(f, m.faces + 1, fmt="f %d %d %d")
    return o


def write_ply(m, path, local=False):
    V, o = _offset(m, local)
    has_c = m.colors is not None
    hdr = ["ply", "format binary_little_endian 1.0", "comment PointCloud Studio"]
    if local:
        hdr.append(f"comment offset {o[0]:.3f} {o[1]:.3f} {o[2]:.3f}")
    hdr += [f"element vertex {len(V)}", "property double x", "property double y", "property double z"]
    if has_c:
        hdr += ["property uchar red", "property uchar green", "property uchar blue"]
    hdr += [f"element face {len(m.faces)}", "property list uchar int vertex_indices", "end_header"]
    vt = [("x", "<f8"), ("y", "<f8"), ("z", "<f8")] + ([("r", "u1"), ("g", "u1"), ("b", "u1")] if has_c else [])
    va = np.empty(len(V), dtype=vt)
    va["x"], va["y"], va["z"] = V[:, 0], V[:, 1], V[:, 2]
    if has_c:
        va["r"], va["g"], va["b"] = m.colors[:, 0], m.colors[:, 1], m.colors[:, 2]
    fa = np.empty(len(m.faces), dtype=[("n", "u1"), ("i", "<i4", (3,))])
    fa["n"] = 3
    fa["i"] = m.faces
    with open(path, "wb") as f:
        f.write(("\n".join(hdr) + "\n").encode("ascii"))
        f.write(va.tobytes())
        f.write(fa.tobytes())
    return o


def write_stl(m, path, local=True):
    V, o = _offset(m, local)
    F = m.faces
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    n = np.cross(b - a, c - a)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
    rec = np.zeros(len(F), dtype=[("n", "<f4", (3,)), ("v", "<f4", (9,)), ("attr", "<u2")])
    rec["n"] = n
    rec["v"] = np.hstack([a, b, c])
    with open(path, "wb") as f:
        head = f"PointCloud Studio offset {o[0]:.3f} {o[1]:.3f} {o[2]:.3f}".encode("ascii")[:80]
        f.write(head.ljust(80, b" "))
        f.write(struct.pack("<I", len(F)))
        f.write(rec.tobytes())
    return o


def write_dxf(m, path, layer="MESH"):
    V, F = m.vertices, m.faces
    with open(path, "w", encoding="ascii", newline="\r\n") as f:
        f.write("0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n9\n$INSUNITS\n70\n6\n0\nENDSEC\n")
        f.write(f"0\nSECTION\n2\nTABLES\n0\nTABLE\n2\nLAYER\n70\n1\n0\nLAYER\n2\n{layer}\n70\n0\n62\n7\n6\n"
                "CONTINUOUS\n0\nENDTAB\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n")
        buf = []
        for a, b, c in F:
            pa, pb, pc_ = V[a], V[b], V[c]
            buf.append(f"0\n3DFACE\n8\n{layer}\n"
                       f"10\n{pa[0]:.4f}\n20\n{pa[1]:.4f}\n30\n{pa[2]:.4f}\n"
                       f"11\n{pb[0]:.4f}\n21\n{pb[1]:.4f}\n31\n{pb[2]:.4f}\n"
                       f"12\n{pc_[0]:.4f}\n22\n{pc_[1]:.4f}\n32\n{pc_[2]:.4f}\n"
                       f"13\n{pc_[0]:.4f}\n23\n{pc_[1]:.4f}\n33\n{pc_[2]:.4f}\n")
            if len(buf) >= 20000:
                f.write("".join(buf))
                buf = []
        f.write("".join(buf))
        f.write("0\nENDSEC\n0\nEOF\n")
    return np.zeros(3)


def export_mesh(m, path, local=None):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".obj":
        return write_obj(m, path, bool(local))
    if ext == ".ply":
        return write_ply(m, path, bool(local))
    if ext == ".stl":
        return write_stl(m, path, True if local is None else bool(local))
    if ext == ".dxf":
        return write_dxf(m, path)
    raise ValueError(f"Unsupported mesh format: {ext}")


def read_ply_or_obj(path):
    """Minimal reader for meshes written by this program (OBJ with optional vertex colours, binary PLY)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".obj":
        V, C, F = [], [], []
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("v "):
                    t = line.split()
                    V.append([float(x) for x in t[1:4]])
                    if len(t) >= 7:
                        C.append([float(x) for x in t[4:7]])
                elif line.startswith("f "):
                    ids = [int(x.split("/")[0]) for x in line.split()[1:]]
                    for k in range(1, len(ids) - 1):
                        F.append([ids[0] - 1, ids[k] - 1, ids[k + 1] - 1])
        col = (np.clip(np.asarray(C) * 255, 0, 255) if len(C) == len(V) and C else None)
        return Mesh(np.asarray(V), np.asarray(F), col)
    raise ValueError("Only OBJ meshes can be read back")

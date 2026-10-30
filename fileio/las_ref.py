"""Large LAS / LAZ files: the original point records are NOT kept in memory (they would double the memory of
a cloud). Only the header is kept; dimensions that are not loaded as attributes and LAS → LAS export are read
from the file again, in chunks, when they are needed."""
from __future__ import annotations

import os

import numpy as np

CHUNK = 2_000_000


class SourceChanged(RuntimeError):
    pass


class LasRef:
    """Stands in for laspy.LasData in pc.source['las'] for large files: .header, ['dimension'][indices]."""

    def __init__(self, path, header, n):
        self.path = path
        self.header = header
        self.n = int(n)
        try:
            st = os.stat(path)
            self.stamp = (st.st_size, int(st.st_mtime))
        except OSError:
            self.stamp = None

    def check(self):
        """Raise SourceChanged if the file is missing or not the file that was loaded."""
        if not os.path.isfile(self.path):
            raise SourceChanged(f"The original file {os.path.basename(self.path)} is no longer there")
        st = os.stat(self.path)
        if self.stamp is not None and (st.st_size, int(st.st_mtime)) != self.stamp:
            raise SourceChanged(f"{os.path.basename(self.path)} was changed after it was loaded")

    def __getitem__(self, dim):
        return _LazyDim(self, dim)

    def read_dims(self, idx, dims):
        """{dim: values at idx (in the order of idx)} streamed from the file."""
        import laspy
        self.check()
        idx = np.asarray(idx, dtype=np.int64)
        order = np.argsort(idx, kind="stable")
        sidx = idx[order]
        out = {d: None for d in dims}
        got = {d: [] for d in dims}
        with laspy.open(self.path) as r:
            if len(sidx) == 1:
                r.seek(int(sidx[0]))
                pts = r.read_points(1)
                for d in dims:
                    got[d].append(np.asarray(pts[d]))
            else:
                start = 0
                for pts in r.chunk_iterator(CHUNK):
                    m = len(pts)
                    a, b = np.searchsorted(sidx, [start, start + m])
                    if b > a:
                        loc = sidx[a:b] - start
                        for d in dims:
                            got[d].append(np.asarray(pts[d])[loc])
                    start += m
                    if start > sidx[-1]:
                        break
        for d in dims:
            vals = np.concatenate(got[d]) if got[d] else np.empty(0)
            res = np.empty_like(vals)
            res[order] = vals
            out[d] = res
        return out


class _LazyDim:
    def __init__(self, ref, dim):
        self.ref, self.dim = ref, dim

    def __getitem__(self, i):
        if np.isscalar(i) or isinstance(i, (int, np.integer)):
            return self.ref.read_dims([int(i)], [self.dim])[self.dim][0]
        return self.ref.read_dims(np.asarray(i), [self.dim])[self.dim]


def stream_copy(ref, idx, path, classes=None, compress=False, progress=None):
    """LAS → LAS export of the records idx straight from the source file (all dimensions kept);
    classes: the current classification of every point (edits made in the program)."""
    import copy

    import laspy
    ref.check()
    idx = np.sort(np.asarray(idx, dtype=np.int64))
    with laspy.open(ref.path) as r:
        if r.header.point_count != ref.n:
            raise SourceChanged(f"{os.path.basename(ref.path)} no longer has the same points")
        hdr = copy.deepcopy(r.header)
        with laspy.open(path, mode="w", header=hdr, do_compress=compress) as w:
            start, done = 0, 0
            for pts in r.chunk_iterator(CHUNK):
                m = len(pts)
                a, b = np.searchsorted(idx, [start, start + m])
                if b > a:
                    loc = idx[a:b] - start
                    sub = pts[loc]
                    if classes is not None:
                        sub.classification = np.asarray(classes[idx[a:b]], dtype=np.uint8)
                    w.write_points(sub)
                    done += b - a
                    if progress:
                        progress(done / max(len(idx), 1))
                start += m
                if start > (idx[-1] if len(idx) else -1):
                    break

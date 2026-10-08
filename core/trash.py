"""Trash: deleted points are only flagged in PointCloud.deleted_batch.

  deleted_batch == 0  -> active
  deleted_batch  > 0  -> in trash, batch id
  deleted_batch  < 0  -> permanently emptied (-batch id); never shown or exported
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from core.history import Command


@dataclass
class Batch:
    id: int
    count: int
    note: str
    created: datetime = field(default_factory=datetime.now)


class Trash:
    def __init__(self, pc):
        self.pc = pc
        self.batches = {}
        self._next_id = 1

    def new_id(self):
        i = self._next_id
        self._next_id += 1
        return i

    @property
    def total(self):
        return sum(b.count for b in self.batches.values())

    def sorted_batches(self):
        return sorted(self.batches.values(), key=lambda b: b.id, reverse=True)  # newest first

    def mask(self, ids=None):
        db = self.pc.deleted_batch
        if ids is None:
            return db > 0
        return np.isin(db, list(ids))

    def purge(self, ids=None):
        """Permanently remove batches (not undoable)."""
        ids = list(self.batches) if ids is None else [i for i in ids if i in self.batches]
        if not ids:
            return 0
        m = self.mask(ids)
        n = int(np.count_nonzero(m))
        self.pc.deleted_batch[m] = -self.pc.deleted_batch[m]
        for i in ids:
            del self.batches[i]
        return n


class DeleteCommand(Command):
    def __init__(self, trash, idx, note):
        self.trash = trash
        self.idx = np.asarray(idx, dtype=np.int64)
        self.batch = Batch(trash.new_id(), len(self.idx), note)
        self.label = f"Delete {len(self.idx):,} points"

    def do(self):
        self.trash.pc.deleted_batch[self.idx] = self.batch.id
        self.trash.batches[self.batch.id] = self.batch

    def undo(self):
        self.trash.pc.deleted_batch[self.idx] = 0
        self.trash.batches.pop(self.batch.id, None)


class RestoreCommand(Command):
    def __init__(self, trash, ids):
        self.trash = trash
        db = trash.pc.deleted_batch
        self.saved = [(trash.batches[i], np.flatnonzero(db == i)) for i in ids if i in trash.batches]
        self.count = sum(len(ix) for _, ix in self.saved)
        self.label = f"Restore {self.count:,} points"

    def do(self):
        for b, ix in self.saved:
            self.trash.pc.deleted_batch[ix] = 0
            self.trash.batches.pop(b.id, None)

    def undo(self):
        for b, ix in self.saved:
            self.trash.pc.deleted_batch[ix] = b.id
            self.trash.batches[b.id] = b

    @property
    def restored_indices(self):
        return np.concatenate([ix for _, ix in self.saved]) if self.saved else np.empty(0, np.int64)

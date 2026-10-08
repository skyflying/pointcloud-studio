"""Undo / redo command stack."""
from __future__ import annotations


class Command:
    label = ""

    def do(self):
        raise NotImplementedError

    def undo(self):
        raise NotImplementedError


class History:
    def __init__(self, limit=200):
        self.limit = limit
        self._undo = []
        self._redo = []

    def push(self, cmd):
        cmd.do()
        self._undo.append(cmd)
        self._redo.clear()
        if len(self._undo) > self.limit:
            self._undo.pop(0)

    def undo(self):
        if not self._undo:
            return None
        cmd = self._undo.pop()
        cmd.undo()
        self._redo.append(cmd)
        return cmd

    def redo(self):
        if not self._redo:
            return None
        cmd = self._redo.pop()
        cmd.do()
        self._undo.append(cmd)
        return cmd

    def clear(self):
        self._undo.clear()
        self._redo.clear()

    @property
    def undo_label(self):
        return self._undo[-1].label if self._undo else ""

    @property
    def redo_label(self):
        return self._redo[-1].label if self._redo else ""

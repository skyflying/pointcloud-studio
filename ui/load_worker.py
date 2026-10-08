"""在背景執行緒讀檔，避免介面凍結；支援進度與取消。"""
from __future__ import annotations

import traceback

from PySide6.QtCore import QThread, Signal

from fileio.xyz_reader import ImportCancelled


class LoadThread(QThread):
    progress = Signal(int)          # 0–100
    loaded = Signal(object)         # PointCloud
    failed = Signal(str)            # 空字串表示使用者取消

    def __init__(self, func, *args, parent=None):
        super().__init__(parent)
        self._func = func
        self._args = args
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            pc = self._func(*self._args,
                            progress=lambda p: self.progress.emit(int(p * 100)),
                            cancel=lambda: self._cancel)
        except ImportCancelled:
            self.failed.emit("")
        except MemoryError:
            self.failed.emit("記憶體不足，無法載入此檔案。")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.loaded.emit(pc)

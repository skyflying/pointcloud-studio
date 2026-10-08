# 打包成執行檔（PyInstaller）

## Windows（建議做法）
1. 安裝 **python.org 版** Python 3.12（64-bit）：https://www.python.org/downloads/windows/
   - **不可使用 Anaconda / Miniconda 的 Python 打包**：Anaconda 自帶 Qt，且其 Python 會把 `anaconda3\Library\bin`
     加入 DLL 搜尋路徑，導致 `DLL load failed while importing QtCore: The specified procedure could not be found`
   - Anaconda 可以繼續保留，兩者互不影響；安裝 python.org 版時「Add python.exe to PATH」可不勾選，腳本會自動找到
2. 解壓縮專案，在資料夾中雙擊 `build_windows.bat`
   - 自動建立乾淨的虛擬環境 `.venv`、安裝套件、執行 PyInstaller
   - 完成後自動執行自我檢測（--selftest），跳出結果視窗
   - 腳本會依序尋找 Python：環境變數 `PYTHON_EXE` → `py` 啟動器 → PATH 上的 `python`（略過 Microsoft Store 佔位程式）→ python.org 預設安裝路徑
   - 若仍找不到，可手動指定後再執行：
     ```
     set PYTHON_EXE=C:\路徑\python.exe
     build_windows.bat
     ```
   - Anaconda 使用者：請在「Anaconda Prompt」中執行 build_windows.bat
3. 成品：`dist\PointCloudStudio\PointCloudStudio.exe`
   - 發佈時請將整個 `dist\PointCloudStudio` 資料夾壓縮後分享（exe 需搭配 `_internal` 資料夾）

## 自我檢測
任何電腦上都可以執行：
```
PointCloudStudio.exe --selftest
```
會依序測試 LAZ 讀寫、TIN、DEM（含 Shoalest）、GeoTIFF + 座標系統、LandXML、剖面、OpenGL 繪圖，
結果寫入 exe 旁的 `selftest_report.txt`。若同事電腦開不起來，請他執行這個指令並把報告傳回。

## 說明
- 採用 one-folder 模式：啟動快。one-file 模式每次啟動都要解壓約 500 MB，較慢，不建議
- 大小約 450–500 MB（壓縮後約 200–250 MB），主要為 GDAL（GeoTIFF）、SciPy、Qt
- 一定要用乾淨的虛擬環境打包（build_windows.bat 已處理），否則系統上其他套件（OpenCV 等）可能被一起打包，體積暴增
- 執行檔未經數位簽章，第一次執行時 Windows SmartScreen 可能提示「已保護您的電腦」→ 點「其他資訊」→「仍要執行」
- PyInstaller 不能跨平台：Windows 版需在 Windows 上打包，Linux 版需在 Linux 上打包
- `PointCloudStudio.spec` 註記：`PySide6.QtTest` 為 VisPy 所需，請勿排除

## 疑難排解

### `DLL load failed while importing QtCore: The specified procedure could not be found`（代碼 0xc0000139）
代表 Qt 載入時碰到**版本不符的 DLL**。最常見原因：**用 Anaconda 的 Python 打包**（Anaconda 自帶 Qt，版本與 pip 的 PySide6 不同）。
解法：安裝 python.org 版 Python 3.12 → 刪除專案內 `.venv` 與 `dist` → 重新執行 build_windows.bat
（v1.0.8 起腳本會優先使用 python.org 版、拒絕 Anaconda，並自動重建以其他 Python 建立的 .venv）。
1. 在專案資料夾雙擊 **`check_env.bat`**（需先執行過 build_windows.bat）→ 產生 `check_env_report.txt`
   - Test A：直接用 .venv 的 Python 載入 Qt（原始碼）
   - Test B：執行打包後的除錯版 exe
   - A 正常、B 失敗 → 打包時收錄了不相符的 DLL；A、B 都失敗 → PySide6 安裝本身有問題
   - 報告列出 System32、Python、PySide6、打包資料夾與 PATH 上所有執行階段 DLL 的版本，並自動標示「比系統舊」的檔案
2. 將 `check_env_report.txt` 與 `%LOCALAPPDATA%\PointCloudStudio\startup.log` 回報
3. Visual C++ Redistributable：若安裝時出現「已安裝較新版本」（0x80070666），代表系統的執行階段已是最新，不需安裝

### `Failed to load Python DLL ...\build\PointCloudStudio\_internal\python3xx.dll`
執行到了 **build** 資料夾裡的 exe。`build` 只是打包過程的暫存檔，裡面的 exe 不能執行。
請執行 **`dist\PointCloudStudio\PointCloudStudio.exe`**（build_windows.bat 打包成功後會自動刪除 build 並開啟 dist 資料夾）。

### `DLL load failed while importing QtOpenGL: The specified procedure could not be found.`
原因：執行檔內混入了**不同版本的 Qt DLL**（打包時 PyInstaller 從 PATH 抓到其他軟體的 Qt，
例如 Anaconda 的 `Library\bin`、GIS 軟體；或 PySide6 各子套件版本不一致；或新舊打包檔案混在同一資料夾）。

處理方式：
1. **整個刪除**舊的 `dist\PointCloudStudio` 資料夾（以及複製到別處的舊版本），不要覆蓋貼上
2. 刪除專案內的 `.venv` 資料夾，重新執行 `build_windows.bat`
   - 腳本會隔離 PATH 後再打包；spec 也會自動剔除非 PySide6 來源的 Qt DLL，並在打包訊息中顯示
     `*** WARNING: dropping Qt libraries ...`（若出現，代表電腦上確實有其他 Qt）
   - spec 會檢查 PySide6 / PySide6-Essentials / PySide6-Addons / shiboken6 版本一致，不一致時會停止並提示修正指令
3. 不要在 Anaconda base 環境打包；若只有 Anaconda，請在 Anaconda Prompt 先建立乾淨環境：
   `conda create -n pcs python=3.12` → `conda activate pcs` → 再執行 build_windows.bat

### 診斷報告
- 程式啟動失敗時會跳出說明視窗，並在 exe 旁寫入 `startup_error.txt`
- 隨時可執行 `PointCloudStudio.exe --diagnose`（或 `python main.py --diagnose`），產生 `diagnose_report.txt`，
  內容包含實際載入的 Qt6Core.dll 路徑、Qt 與 PySide6 版本、PATH 上的其他 Qt 安裝
- 遠端桌面或虛擬機沒有 OpenGL 2.1 時：先 `set QT_OPENGL=software` 再啟動

# 發佈到 GitHub

這個資料夾是一個 Git repository，版本歷程從 **v0.1.5** 開始：

| commit | 標籤 | 內容 |
|---|---|---|
| 1 | `v0.1.5` | 起始版本（匯入／編輯／Subset／濾除／專案／TIN・DEM／剖面／路由／體積／土方設計／坡度） |
| 2 | `v0.1.6` | 等深線（SHP PolyLineZ、DXF 3D 聚合線） |
| 3 | `v0.1.7` | DXF 等深線深度標註 |

## 1. 在 GitHub 建立空白專案
1. 登入 https://github.com/new
2. Repository name：`PointCloudStudio`
3. 選 **Private**（建議；之後可改 Public）
4. **不要**勾選 Add README / .gitignore / license（本資料夾已有）
5. 按 Create repository

## 2. 推送（擇一）

**A. 命令列（需安裝 Git for Windows）**，在此資料夾開啟命令提示字元：
```
git remote add origin https://github.com/你的帳號/PointCloudStudio.git
git push -u origin main
git push origin v0.1.7
```
第一次推送會跳出 GitHub 登入視窗。只推送 `v0.1.7` 標籤即可（每推一個標籤就會自動打包一次）；
若也想在 GitHub 顯示舊版標籤，可再執行 `git push origin v0.1.5 v0.1.6`。

**B. GitHub Desktop（不用打指令）**
File → Add local repository → 選這個資料夾 → Publish repository
（標籤：Repository → Push 後，到網頁的 Releases → Draft a new release → 選 `v0.1.7`）

**C. GitHub CLI**
```
gh repo create PointCloudStudio --private --source=. --push
git push origin v0.1.7
```

## 如果之前已經推送過舊版（v1.0.2）
版本號已改為從 0.1.5 起算，歷程也重新整理過，請先清掉舊的標籤與 Release，再強制推送：
```
git remote add origin https://github.com/你的帳號/PointCloudStudio.git
git push origin --delete v1.0.2
git push -u origin main --force
git push origin v0.1.7
```
並在網頁 Releases 頁面刪除舊的 v1.0.2 Release（若有）。

## 3. 自動產生 Windows 執行檔
- 推送標籤後，GitHub Actions 會在 Windows 主機上自動打包、執行自我檢測，
  並在 **Releases** 頁面放上 `PointCloudStudio-windows.zip`（約 10–15 分鐘）
- 也可以隨時手動執行：Actions → Build Windows → Run workflow → 完成後在該次執行頁面下載 Artifact
- 之後發新版：修改 `core/version.py` 的版本號 → 在 README「版本紀錄」加一行 → commit →
  `git tag v0.1.8` → `git push origin main v0.1.8`
- GitHub 的主機沒有顯示卡，自我檢測的 3D 繪圖項目會改用軟體 OpenGL；即使該項失敗也不會中斷打包

## 注意
- 點雲資料（.las/.laz/.xyz…）、專案檔（.pcsproj）、GIS 輸出（.shp/.dxf…）與打包結果都列在 .gitignore，不會被上傳
- Private 專案的 Actions 每月有免費額度（一般帳號 2,000 分鐘），每次打包約用 10–15 分鐘

# PointCloud Studio 使用說明（v0.1.12）

點雲檢視、清理與地形建模工具（英文暗色介面），適用於 MBES／LiDAR 測量資料與海纜工程。
製作者：Mingyi Hsu（smingyi.hsu@gmail.com）

功能總覽：XYZ/ASC/LAS/LAZ 匯入 → 3D 檢視與色階 → 選取、Subset 編輯、自動濾除 → Reject（Class 7）／垃圾空間（可還原、Undo/Redo）
→ TIN／DEM（含 Shoalest）→ 剖面、海纜路由與 KP → 體積與多期比較 → 土方設計（浚挖／填平／海纜開溝）與坡度分析
→ 等深線（SHP / DXF 3D）→ 匯出

## 下載 Windows 版
到 GitHub 專案的 **Releases** 頁面下載 `PointCloudStudio-windows.zip`，解壓後執行 `PointCloudStudio.exe`
（不需安裝 Python；第一次執行若出現 SmartScreen，按「其他資訊 → 仍要執行」）。

## 下載 macOS 版
到 **Releases** 頁面下載 DMG：Apple Silicon（M 系列晶片）選 `…-macos-arm64.dmg`，Intel Mac 選 `…-macos-x86_64.dmg`
（需 macOS 11 以上）。打開 DMG 後，把 PointCloudStudio 拖進「應用程式」資料夾。

程式沒有 Apple 開發者簽章，第一次開啟會被擋下（顯示「無法打開」或「已損毀」）。請在 App 上按右鍵 →「打開」→「打開」，
或在「終端機」執行一次：
```
xattr -cr /Applications/PointCloudStudio.app
```
Mac 操作差異：快捷鍵的 Ctrl 改為 ⌘（Command），鍵盤上的 delete 鍵即可剔除選取點；
觸控板雙指滑動或捏合可縮放，Shift + 拖曳可平移。

## 從原始碼執行 / 安裝
```
pip install -r requirements.txt
```

## 執行
```
python main.py              # 開啟空白視窗
python main.py 檔案.laz      # 直接載入
```

## 操作
- 開檔：Ctrl+O 或拖曳檔案進視窗（支援 .xyz .asc .txt .csv .pts .las .laz）
- 旋轉：左鍵拖曳；縮放：右鍵拖曳或滾輪；平移：Shift＋左鍵拖曳
- 視角快捷鍵：1 俯視、2 前視、3 側視、4 斜視、F 全覽

## 海域與陸域資料
- 程式內部的 Z 一律為**高程（向上為正）**；水深以負值表示。畫面可切換 Depth 顯示（深度向下為正）
- **Z 為正值深度的檔案**（常見於水深 XYZ）：XYZ 匯入對話框選「Z values are：Depth (down +)」，會自動轉成高程；
  預覽中 Z 全為正值時會提醒。LAS、GeoTIFF 等其他檔案可在 Data 面板 ⋯ →「Z values are depths (flip sign)」翻轉，
  依賴它的 TIN/DEM/等深線會標示 Out of date；設定存入專案。文字匯出可勾選「Write Z as depth」寫回正值深度
- **海陸交界資料**（同時有正負值）自動使用 **Topo-bathy** 色階：0 m 以下藍色系、以上綠褐色系，分界固定在 0 m；
  0 m 等高線（海岸線）以青色標示，DXF 為 CONTOUR_ZERO 圖層，SHP 的 TYPE 為 zero
- 土方設計與體積基準面的高程欄位可各自選擇 Elevation／Depth；自動濾除的用語以「上方／下方」表示
- **Reject 分類**（Subset 面板）：Class 7（低雜訊）、Class 18（高雜訊，LiDAR）或自訂分類；7、18 與用過的自訂分類都視為
  已剔除（隱藏、可顯示、Accept 還原原分類）。LAS Point Format 0–5 只能存 0–31 類，超過時會提示
- **依分類建立 TIN／DEM**：Create TIN／DEM 的「Only classes」輸入分類（例如 2 = 地面點）即只用該分類建立地形；
  對話框列出資料中的分類與點數，產生的圖層名稱註明分類
- **鬆方／壓實係數**（Earthworks Design）：膨脹係數（實方 → 鬆方，例如土 1.25、岩 1.5–1.7）、
  收縮係數（實方 → 壓實方，例如 0.90）；結果另列挖方鬆方量、填方所需實方量與挖填平衡（剩餘／不足），並寫入 CSV

## 匯入與顯示
- XYZ/ASC 匯入對話框：自動偵測編碼（UTF-8／Big5）、分隔符號、起始列；可手動調整並逐欄指定用途
- LAS/LAZ 讀取（保留原始檔頭與 VLR，供第四階段匯出沿用）
- 3D 顯示：高程／Intensity／RGB／Classification／單色著色、點大小、Z 誇大、顯示上限抽稀
- 背景執行緒載入，大檔案不會凍結視窗

## 選取
- 模式（工具列或快捷鍵）：V 導覽、P 點選、B 框選、L 套索
- Shift 加選、Ctrl 減選；Ctrl+A 全選、Ctrl+I 反選、Esc 清除（繪製中按 Esc 則取消繪製）
- 選取模式下左鍵專用於選取；滾輪縮放、右鍵拖曳縮放、中鍵拖曳平移仍可使用
- 框選／套索會選取區域內所有深度的點（穿透選取），並以全部點計算，不受顯示抽稀影響
- 點選會取游標 8 px 內最前方的點
- 右側 Selection 面板：單點顯示全部欄位，多點顯示範圍與屬性統計；Copy 可複製為 Tab 分隔文字

## 刪除、垃圾空間、復原
- Delete / Backspace：把選取的點移到垃圾空間，每次刪除為一個批次（記錄編號、時間、選取方式、點數）
- Trash 面板：點選批次以橘色預覽；勾選「Show deleted points in view」以淡紅色顯示全部已刪點
- Restore（選取的批次）、Restore all、右鍵選單可單獨永久刪除批次
- Empty…：永久清空（需確認），永久刪除的點不會匯出、無法還原，並清除 Undo 紀錄
- Ctrl+Z / Ctrl+Y（或 Ctrl+Shift+Z）：復原 / 重做所有刪除與還原動作；復原刪除時，那些點會回到選取狀態

## 匯出（File → Export…，Ctrl+E）
- 內容：有效點（預設）、只匯出選取的點、只匯出垃圾空間；可勾選另存垃圾空間為 *_trash 檔
- LAS/LAZ（來源為 LAS）：完整沿用原檔頭、Point Format、Scale/Offset、所有欄位與 VLR（含座標系統）
- LAS/LAZ（來源為 XYZ）：可選 LAS 1.2/1.4、座標精度、EPSG（例如 3826 = TWD97 TM2 121，需 pyproj）、自訂欄位寫為 Extra Bytes；有 RGB 時自動選用含顏色的 Point Format，0–255 顏色自動轉為 16-bit
- 文字檔：勾選欄位並拖曳排序、分隔符號、XYZ 小數位數、是否輸出表頭；預設沿用原檔欄位順序與分隔符號
- 背景執行並顯示進度；輸出路徑與來源相同時會先確認
- 有未匯出的編輯時，標題列顯示 ●；開新檔或關閉程式前會提醒

## 色階（Color bar 與高度／深度範圍）
- 「Color by」為 Elevation 或 Intensity（或有 TIN/DEM 顯示時）出現 Color Scale 卡片與畫面右側色階條
- **色階條兩端 = 固定刻度範圍**，只由 Color Scale 的 Shallow / Deep（或 Min / Max）決定，拖曳時不會改變
- **色階條上的兩個把手 = 顯示範圍**：顏色只在兩把手之間展開，把手以外用端點顏色（或 Gray out 時為灰色）
  - 拖曳把手：調整顯示範圍上下限（限制在刻度範圍內）
  - 拖曳兩把手之間：整段平移；滾輪：以游標為中心縮放顯示範圍；雙擊：把手回到全刻度
- Color Scale 卡片顯示目前的 Display 範圍，「Reset」可讓把手回到全刻度
- 輸入 Shallow / Deep：改變刻度範圍；若把手原本為全刻度則跟著改變，已縮小的把手範圍會被限制在新刻度內
- Values as：Elevation（Z，向上為正）或 Depth（−Z，向下為正）；資料 Z 全 ≤ 0 時自動切為 Depth
- Auto 2–98% / Full range：依資料重設刻度（把手一併回到全刻度）
- Palette：Rainbow、Turbo、Viridis、Bathymetry、Grayscale，可反轉；Out of range：Clamp 或 Gray out

## TIN 與 DEM（Surface 選單 / Layers 卡片）
- Layers：點雲、TIN、DEM 各自開關、透明度；⋯ 選單可匯出、線框（TIN）、重建、移除；可開關地形陰影
- 點雲在建立表面後被編輯時，圖層顯示「Out of date」提醒重建
- 表面以 Elevation 色階著色，與點雲共用色階條與 Color Scale 設定
- Create TIN（Ctrl+T）：Delaunay 2.5D；最大邊長（避免跨越資料空隙）；可先格網抽稀（保留真實點：中位數 / 最淺 / 最深 / 第一點）
- Create DEM（Ctrl+G）：網格大小；方法 Linear (TIN)、Mean、Median、Min Z、Max Z、Point count、IDW；
  可沿用現有 TIN、填補小破洞、原點對齊網格
- 匯出 TIN：LandXML、DXF 3DFACE、OBJ、PLY；匯出 DEM：GeoTIFF（含座標系統）、ESRI ASCII Grid、XYZ 網格點

### Shoalest depth (true position)
- 水文測量常用的淺點偏向網格：每格保留「最淺的一個真實測點」，並記錄該測點的真實 X/Y（不是網格中心）
- 不做平均、平滑或移位，最淺水深不會被抹平；3D 表面直接通過這些真實淺點位置
- Shoalest = Highest Z（Z 向上、水深為負，預設）或 Lowest Z（Z 為正值水深；資料 Z 全為正時自動選此項）
- 此方法預設不填補破洞（避免產生非實測水深）；仍可手動開啟，填補的格子在 XYZ 匯出時以網格中心表示
- 匯出 XYZ：寫出每個淺點的真實座標；GeoTIFF：網格值為各格最淺水深，並標記 shoal_biased=true

## 剖面工具（Section，快捷鍵 S）
- 進入 Section 模式時**視角維持不變**（任何角度都可以畫）；點擊加入節點，雙擊或 Enter 完成，Backspace 刪除最後一點，Esc 取消
- 點選位置會落在畫面上實際看到的海床／表面上（沿視線找交點），斜視與 Z 誇大時也準確；
  畫出的剖面線、多邊形、Subset 方框與路由都貼著地形顯示
- 完成後的剖面線可直接拖曳節點修改，剖面會自動重算；可建立多條剖面並在下方面板切換
- 下方 Section profile 面板：橫軸里程（可設定起始里程 Start，例如 KP 偏移），縱軸高程或深度（依 Depth 模式）
  - Width：走廊半寬（± m），只顯示此範圍內的點
  - 可分別開關 Points / TIN / DEM；TIN 剖面為三角網精確交線，資料空隙會斷開
  - V.exag 垂直誇大（或 Shift+滾輪）；滾輪縮放、拖曳平移、Fit 全覽
  - Measure：點兩點顯示水平距離、高差（深度差）、斜距、坡度（% 與 °）
  - 游標即時顯示里程、Z／深度與 XY 座標
  - CSV 匯出（type, chainage, x, y, z, offset, point_index；含點、TIN、DEM 三種）與 PNG 圖片
- 點雲被編輯、TIN/DEM 重建或移除時，剖面自動更新


## Help 選單
- Keyboard Shortcuts（F1）：所有快捷鍵一覽
- About：版本、製作者 Mingyi Hsu（smingyi.hsu@gmail.com）與使用的函式庫版本

## 打包成執行檔
請見英文 README 的「Building a standalone executable」：執行 `build_windows.bat`（需 python.org 版 Python，不可使用 Anaconda）；
程式無法啟動時查看 `%LOCALAPPDATA%\\PointCloudStudio\\startup.log` 或執行 `check_env.bat`。（Windows：雙擊 build_windows.bat）。打包後可用 `--selftest` 驗證。
macOS：在專案資料夾執行 `./build_mac.sh`（同樣需 python.org 版 Python），產生 `dist/PointCloudStudio.app` 與 DMG；
啟動記錄與報告位於 `~/Library/Application Support/PointCloudStudio/`。

## 專案架構（多份資料、座標系統）
- **Open**（Ctrl+O）：開新專案；**Add Data**（Ctrl+Shift+O 或拖曳多個檔案）：加入點雲或 DEM 到目前專案
- 支援 DEM 匯入：GeoTIFF、ESRI ASCII Grid（.asc 以 `ncols` 開頭者自動判斷為網格）、規則 XYZ 網格（XYZ 匯入對話框選「DEM grid」）
- **座標系統**：專案座標系統由第一份資料決定；檔案未記錄座標系統時會跳出 EPSG 選擇（可搜尋代碼或名稱，內建 TWD97 TM2 121/119、TWD67、UTM 50N/51N、WGS84）
  - 之後加入的資料若座標系統不同，會自動轉換到專案座標系統（點雲轉換 X/Y；DEM 以雙線性重新取樣）
  - 資料為經緯度時，會要求選擇投影座標系統作為專案座標系統
  - LAS 匯出時沿用原檔內容與原始座標系統
- **Data 面板**：每份資料可開關、透明度（DEM/TIN）、⋯ 選單（Zoom to、Export、Wireframe、Remove）；DEM/TIN 標示來源點雲，來源被編輯後顯示「Out of date」
- **DRAWINGS（畫面上畫出的線與範圍）**：每條剖面線、體積／浚挖多邊形、目前 Subset 方框、已審查範圍都列在 Data 面板，
  可個別開關顯示；⋯ 選單可顯示剖面圖、刪除剖面線、移除多邊形、清除已審查範圍；開關狀態與多邊形都存入專案
- 右側 **Navigation** 說明卡片可按 ✕ 關閉，或由 View → Navigation Help 開關（設定會記住）
- **刪點鎖定**：專案只有一份點雲時可直接編輯；有多份點雲或只有 DEM 時自動鎖定，在點雲列按 **✎ Edit** 指定編輯目標（一次一份），每份點雲各自保留選取、垃圾空間與 Undo 紀錄
- Create TIN / DEM：以編輯中的點雲為來源（鎖定時會詢問使用哪一份），結果成為專案中的新資料
- **專案存檔**（File → Save Project，Ctrl+S；Save Project As… Ctrl+Shift+S；Open 可直接開啟 .pcsproj）
  - `.pcsproj`（JSON）記錄：資料路徑（絕對與相對路徑）、座標系統、匯入設定、編輯目標、剖面、色階與顯示範圍、深度模式、Z 誇大、點大小、視角
  - `<名稱>.pcsproj_data\` 存放每份點雲的刪除／垃圾空間紀錄，以及產生的 TIN、DEM（原始資料檔不會被修改或複製）
  - 開啟時重新讀取原始檔並套用編輯；找不到檔案會詢問位置；原始檔點數改變時不套用該檔的編輯並提出警告
  - 有未存檔的變更時（編輯、加入／移除資料、TIN/DEM、剖面），關閉或開新專案前會提示 Save Project
- 修正：LAS 檔帶有全為 0 的 RGB 欄位時，不再預設以 RGB（全黑）顯示，改以高程著色

## Subset 編輯器（類似 CARIS Bathy DataBASE / BASE Editor）
- **Subset 模式**（工具列 Subset 或按 U）：在 3D 畫面（任何視角）拖曳出 Subset 的長軸（可任意方向，例如沿海纜），寬度在 Subset 面板設定
- **Subset 面板**（下方）：
  - View：Along axis（沿長軸側視）／Across axis（垂直長軸端視）
  - 工具：Pan、Box、Lasso；Shift 加選、Ctrl 減選；選取同步顯示在 3D 主視窗（主視窗即為 3D 檢視，Focus 3D 可縮放到目前 Subset）
  - Reference surface：同時畫出 TIN / DEM 切線作為判斷依據
  - ◀ Prev / Next ▶（PageUp / PageDown）：沿長軸翻頁，Overlap 設定重疊比例
  - ✓ Mark complete（Ctrl+M）：標記已審查，俯視圖以綠色顯示，並統計已審查點數百分比
- **Reject**（Delete 鍵、Subset 面板或選取卡片）：
  - LAS / LAZ：預設標示為 **Class 7（雜訊）**，不刪除；亦可改為移到垃圾空間
  - 其他格式：移到垃圾空間
  - Class 7 的點預設隱藏，且不參與選取、TIN/DEM、剖面；勾選「Show rejected (Class 7)」以紅色顯示
  - **Accept**：把選取的 Class 7 點恢復為原本的分類；所有動作皆可 Undo / Redo
- 匯出 LAS/LAZ：Class 7 的點預設保留並帶分類輸出；可勾選「Exclude rejected points」排除
- 專案存檔會一併保存分類變更、目前 Subset、已審查範圍與 Reject 設定

## 自動濾除（Edit → Auto Filter…，Ctrl+F；Subset 面板的 Filter…）
- 濾除結果只會成為「選取」，不會自動刪除：預覽（Preview）→ 在 3D 主畫面／Subset 編輯器檢查 → Reject
- Cancel 會還原開啟對話框前的選取；Keep selection 保留候選點供檢查
- 方法（使用者自行選擇與設定參數）：
  | 方法 | 參數 | 適用 |
  |---|---|---|
  | Grid median deviation（建議 MBES 首選） | 網格大小、門檻（m 或 robust σ 倍數）、只標淺側／深側／兩側 | 突刺 |
  | Reference surface deviation | TIN/DEM、上方／下方容許距離 | 已有乾淨表面時（參考面須由已清理資料建立） |
  | Statistical outlier (SOR) | 鄰點數 k、σ 倍數 | 密集資料中的散布雜訊 |
  | Radius outlier | 搜尋半徑、最少鄰點數 | 孤立點（密集資料） |
  | Depth / elevation range | 最淺／最深（Depth 模式以正值深度輸入） | 明顯錯誤值 |
- 範圍：全部有效點、目前選取、目前 Subset；結果可「取代」或「加入」目前選取
- Grid median 以網格中位數建立中位面並內插，消除網格內海床坡度的影響；結果列會顯示資料雜訊水準（robust σ）作為門檻參考
- 預設參數依資料平均點距自動調整（網格 ≈ 2.5 倍點距、搜尋半徑 ≈ 3 倍點距）
- Reject 時批次名稱會記錄使用的濾除方法（例如「Filter: Grid median deviation」）

## 海纜路由與 KP（File → Import Route…，Ctrl+R）
- 支援格式：
  - **Shapefile**（.shp）：線段（每個 part 一條路由）或點（依檔案順序連線）；名稱取自 NAME/ROUTE/CABLE/ID 欄位；有 .prj 時自動判斷座標系統
  - **RPL CSV / TXT**：欄位對應對話框（同 XYZ 匯入），角色：Easting/Northing 或 Longitude/Latitude、KP、Route ID（一個檔案可含多條海纜）
- **匯入時必須確認座標系統（EPSG）**：.prj 或經緯度欄位會預先帶入，仍須確認；與專案不同時自動轉換到專案座標系統
- 欄位自動判斷：優先使用表頭名稱；無表頭時以「KP 增量 = 相鄰座標距離」的幾何檢驗判斷 KP 與 E/N 欄，並判斷 KP 單位（km / m）
- 沒有 KP 欄位時：KP = 起始 KP + 沿線距離
- 路由為專案資料（RTE），3D 畫面以青色線顯示，並在整數 KP 位置標示
- Data 面板路由的 ⋯ 選單：
  - **Section along route**：以路由為剖面線，橫軸直接顯示檔案中的 **KP（km）**（依各頂點 KP 內插），游標顯示 KP
  - **Subset editor along route**：Subset 沿路由翻頁，方向隨路由轉折；狀態列顯示目前 KP 範圍
- Subset 面板新增 **Length**（Subset 長度／翻頁大小）
- 版面：Section profile 與 Subset editor 改為同一區域的分頁；兩者在 1366 × 768 螢幕也可完整顯示

## 體積計算與多期比較（Analysis 選單）
### Volume / Difference（Ctrl+Shift+V）
- **A**（例如新一期 DEM/TIN）對 **B**（另一個表面，例如前一期或設計面）或 **基準面**（以 Elevation 或 Depth 輸入，海域、陸域皆可）
- 符號：**Δ = A − B**；正值（紅）＝ A 高於 B：堆積／填方；負值（藍）＝ A 低於 B：沖刷／挖方
- 範圍：整體重疊區、**海纜路由走廊**（路由 ± 半寬）、目前 Subset 方框、**多邊形**（在 3D 畫面點選繪製，或由 Shapefile 匯入，例如拋石設計範圍；匯入時確認 EPSG）
- 結果：比較面積、**覆蓋率**、填方／挖方體積與面積、淨體積、Δ 平均／標準差／極值／5%–95%，以及 Δ 分布直方圖
- **覆蓋率不足 100% 時會警告**：任一表面沒有資料的網格不計入體積，體積會偏低；請使用 DEM 填補破洞、TIN 內插 DEM 或較大網格
- 輸出：**Add Δ layer**（以 A 的形狀顯示、依 Δ 著色的圖層，可存入專案）、**Report CSV**、**Δ GeoTIFF**
### Point Cloud to Surface
- 編輯中的點雲每一點相對所選表面的高差，以「Δ to surface」色階（藍白紅、0 置中）顯示，狀態列顯示統計
### 多期剖面
- 剖面上同時畫出所有可見的 DEM/TIN（線）與其他點雲（點），每份資料各自顏色並有圖例；勾選 Surfaces / Other clouds 可開關
- CSV 匯出會包含每一份資料的剖面

## 土方設計（Analysis → Earthworks Design，Ctrl+Shift+D）
適用於**海域與陸域**：現況表面可以是海床或地面（DEM/TIN）。所有高程輸入欄位都可各自選擇
**Elevation（高程，向上為正）** 或 **Depth（深度，向下為正）**，與畫面的 Depth 顯示模式無關；
預設依資料判斷（全為負值的水深資料預設 Depth，其他預設 Elevation），切換時數值會自動換算、代表同一高度，
並顯示現況表面在該基準下的範圍作為參考。
### Cut / fill（挖方／填方：浚挖、開挖、整地、填築、回填）
- 現況表面（DEM/TIN）＋設計範圍（多邊形繪製或 SHP、路由走廊、Subset 方框）
- 作業：挖填至設計高程（整地）／僅挖方（浚挖、開挖）／僅填方（填土、路堤、填海）
- 設計高程（Elevation 或 Depth）；超挖容許量（浚挖或開挖的超挖，另外報告「含超挖」挖方量）
- 邊坡：挖方坡、填方坡分別以 **1 : n（垂直 : 水平）** 設定，自設計範圍邊緣向外延伸至與現況表面相交；0 = 垂直壁
- 結果：設計範圍面積、挖方（設計高程／含超挖）、填方、淨量、含邊坡影響面積、最大挖深／填高
### Trench（開溝斷面：海纜、管線、排水溝）
- 沿路由的梯形溝：底寬、邊坡 1 : n、溝底＝「現況表面（海床或地面，沿中心線平滑）以下的深度」或「設計高程（Elevation 或 Depth）」
- 結果：總開挖量、平均斷面積（與平坦表面的標稱斷面比較）、最大開挖深度、**每 KP 區間開挖量表**
- 兩種設計皆可：Add layers（設計面＋挖填 Δ 圖層，藍＝挖、紅＝填）、Report CSV（含參數與 KP 表；報表中高程一律為向上為正）、Design / Δ GeoTIFF
### Slope Map（海床坡度）
- 由 DEM/TIN 計算坡度（°），以 Turbo 色階建立圖層；統計平均、95%、最大坡度與超過門檻（預設 5°、10°、15°）的面積比例
- 剖面游標同時顯示第一個表面圖層的局部坡度（±2 m）

## 等深線（Analysis → Contours…，Ctrl+Shift+C）
- 來源：DEM 或 TIN（TIN 先以設定的網格內插）
- 參數：等深線間距、基準值（等深線位於 基準 + k × 間距）、每 N 條一條計曲線（主曲線）、平滑（網格高斯平滑，單位：網格數）、簡化（Douglas–Peucker 容許距離）、最短長度（去除雜訊小圈）
- 等深線為專案圖層（CNT），在 3D 畫面中位於實際深度（計曲線較亮），並在計曲線旁顯示**深度值**
  （無計曲線時標示所有等深線；關閉 Depth 模式時改為高程值），存入專案
- 匯出（Data 面板 ⋯ → Export）：
  - **Shapefile**：PolyLineZ，屬性 ELEV（高程）、DEPTH（深度，正值）、TYPE（major / minor），附 .prj
  - **DXF**（R12）：每條等深線為 **3D 聚合線**，每個頂點帶 Z 值；計曲線與首曲線分別在 CONTOUR_MAJOR / CONTOUR_MINOR 圖層；封閉等深線標示為封閉聚合線
  - **DXF 深度標註**：匯出時選擇標註計曲線／全部／不標註、數值（正值深度或高程）、小數位數、字高（m）、間距（m）；
    文字放在 CONTOUR_LABEL 圖層，位於等深線的高程上，沿等深線走向旋轉並保持正向可讀（近垂直者一律由下往上讀），
    太短的小圈不標註；預設字高與間距依資料範圍自動設定

## 點雲分類（Edit → Classify Points…，Ctrl+K）
**步驟一：地面點濾除**（擇一，參數可調，對話框附說明）
| 演算法 | 原理 | 適用 |
|---|---|---|
| SMRF（Pingel 2013） | 最低點網格上逐步擴大窗口的開運算＋坡度門檻，最後依局部坡度判斷 | 通用首選（PDAL 預設方法） |
| PMF（Zhang 2003） | 窗口指數擴大的形態學濾波，高差門檻隨窗口與坡度增加 | 平坦至中等起伏；陡坡需提高 Slope |
| CSF（Zhang 2016） | 點雲上下翻轉後模擬布料落下，接近布料的點為地面 | 陡坡、複雜地形；大型平屋頂用較硬、較粗的布（3、2 m 以上） |
| Grid minimum | 網格最低點內插為參考面，高差門檻＋坡度修正 | 平坦或緩坡、海灘、水深；網格須大於最大地物 |

**步驟二：地面以上（可選）**
- 植生：依離地高度分為低（3）、中（4）、高（5），高度門檻可調
- 建物（6）：離地高於門檻、局部表面粗糙度（λ3 ⁄ Σλ）低且非垂直的屋頂點，面積大於門檻者，並包含屋頂範圍內的牆面點

**其他**：作用範圍可選全部／選取／Subset；可勾選「只改未分類點（0／1）」保留手動分類；整次分類可一次 Undo；
完成後自動以分類著色並列出各類點數；XYZ 等無分類欄位的資料會自動新增分類欄位，匯出 LAS 時一併寫出。

**合成地形驗證**（28 萬點：起伏地形＋陡坡、60 棵樹與灌木、3 棟建物；總誤差 = 誤判點比例）
| 演算法 | Type I | Type II | 總誤差 |
|---|---|---|---|
| SMRF | 0.00% | 1.29% | 0.31% |
| PMF（slope 0.6） | 0.72% | 0.36% | 0.63% |
| CSF（rigidness 3, 2 m） | 0.31% | 1.41% | — |
| Grid minimum（5 m） | 0.00% | 5.77% | 1.38% |
SMRF＋植生＋建物：地面 100%／99.6%（召回／精確），植生 98%，建物 75%／92%（牆面與鄰近樹木為主要誤差）。

## 里程名稱
View → Distance Label 可選 **KP**（海纜、海上管線）或 **Chainage**（道路、陸上管線）；套用於剖面橫軸、游標、
3D 路由標示、Subset 狀態與開溝體積表（設定會記住）。

## 版本紀錄
- **0.1.12**：點雲分類（SMRF／PMF／CSF／Grid minimum 地面濾除＋植生分層＋建物偵測，可整次 Undo）；KP／Chainage 名稱可選；自我檢測加入陸域分類
- **0.1.11**：Reject 分類可選（7／18／自訂）；Create TIN／DEM 可依分類篩選（例如只用地面點）；土方設計加入鬆方與壓實係數、挖填平衡
- **0.1.10**：Z 為正值深度的資料（XYZ 匯入選項、Data 面板翻轉、匯出寫回深度）；Topo-bathy 色階與 0 m 海岸線；濾除用語海陸通用
- **0.1.9**：土方設計與體積基準面適用陸域：每個高程欄位可選 Elevation／Depth（與顯示模式無關、自動換算、顯示現況範圍）；
  用語改為 Cut / fill、Over-cut、Trench（海床或地面）
- **0.1.8**：剖面不再自動轉俯視（任何視角皆可精準點選，線條貼地形顯示）；Data 面板 DRAWINGS 可開關剖面／多邊形／Subset；
  3D 等深線顯示深度值；Navigation 卡片可關閉；多邊形存入專案
- **0.1.7**：DXF 等深線深度標註；GitHub Actions 自動打包
- **0.1.6**：等深線（SHP PolyLineZ／DXF 3D 聚合線、計曲線、平滑與簡化）
- **0.1.5**（起始版本）：點雲匯入／編輯／匯出、Subset 編輯與 Class 7、自動濾除、專案與座標系統、TIN/DEM（含 Shoalest）、剖面、海纜路由與 KP、體積與多期比較、土方設計（浚挖／填平／海纜開溝）、坡度分析

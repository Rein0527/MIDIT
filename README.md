# 🎼 MIDI Piano Roll Studio

## 📖 專案簡介
MIDI Piano Roll Studio 是一款基於 **PySide6** 的跨平台 MIDI 編輯與播放工具。  
它結合 **MIDI 匯入/匯出**、**鋼琴卷軸 (Piano Roll) 編輯**、**多音軌管理**、**即時播放 (Transport)** 以及 **軟體合成/外部 MIDI 輸出**，可作為輕量級 DAW 或 MIDI 教學/練習工具。

特色：
- 🎹 **Piano Roll**：直觀的鋼琴卷軸編輯介面  
- 🎧 **即時播放**：支援 FluidSynth (SoundFont) 或 系統 MIDI 輸出  
- 🎚 **多音軌管理**：支援分組、Mute/Solo、音量/音色控制  
- 🎨 **可換主題**：深色/淺色/自訂顏色方案  
- 📝 **MIDI 匯入匯出**：保留 tempo、拍號、標記  
- ⚡ **延遲最佳化**：三種 Latency Profile，兼顧省電與低延遲  

---

## 🚀 安裝與執行

### 1. 系統需求
- Python 3.10+
- Windows / macOS / Linux
- 建議安裝 **fluidsynth** 以使用 SoundFont 輸出

### 2. 安裝依賴
```bash
pip install PySide6 mido python-rtmidi pyfluidsynth
```

### 3. 啟動程式
```bash
python main_window.py
```

---

## 🖥 使用說明

### 介面總覽
- **功能列 (Menu Bar)**  
  File / View / Time / Track / Setting / Marker / Edit
- **左側面板 (Tracks Panel)**  
  - 顯示各音軌、分組  
  - 支援新增/刪除/拖曳排序  
  - 控制 Program (樂器)、Volume、Mute、Solo、Freeze、Lock  
- **中央鋼琴卷軸 (Piano Roll)**  
  - 顯示與編輯音符  
  - 可自動捲動、對齊網格、顯示音名  
- **底部 Transport**  
  - 播放、暫停、停止、Loop  
  - 速度調整 (50%–150%)  
- **狀態列**  
  - 當前 BPM、Tick、播放狀態、選取資訊

---

## 🎛 偏好設定 (Preferences)
可於 **Setting → Preferences** 開啟。

- **UI Theme**：dark / light / slate / indigo / emerald / amber / rose / custom  
- **Default BPM / Tracks**：新專案的預設值  
- **Default Output**：
  - `system` → 輸出至系統 MIDI 裝置 (如 LoopMIDI、虛擬合成器)  
  - `fluidsynth` → 使用 SoundFont (.sf2) 發聲  
- **Storage**：自動備份 / 自動還原  
- **Latency Profile**：
  - low (省電，高延遲)  
  - medium (平衡，預設)  
  - high (高效能，低延遲，適合即時彈奏)

---

## 🎶 MIDI 匯入/匯出

### 匯入
1. File → Import MIDI  
2. 支援 tempo map、拍號、markers  
3. 自動建立 Track，並依 Channel 分配

### 匯出
1. File → Export MIDI  
2. 輸出為 Type-1 (多軌)  
3. 會保留 tempo、拍號、marker 與音符資訊

---

## ⚡ 延遲最佳化 (Transport)
Transport 內建高精度播放排程：
- **WAKE_HZ**：播放輪詢頻率  
- **SAFETY_BACKTRACK_SEC**：安全回溯，確保不漏音  
- **UI_THROTTLE_MS**：UI 更新頻率  

使用者可透過 `Preferences → Latency Profile` 切換，以兼顧省電或即時性。

---

## 🎨 主題與自訂顏色
- 預設提供 **dark / light / slate / indigo / emerald / amber / rose**  
- 可選擇 **custom**，進入完整顏色編輯器，調整 12 種 UI 元素顏色  

---

## 🔧 進階操作
- **音軌分組**：可建立群組，並將音軌拖入群組中  
- **Freeze 功能**：將軌道渲染為音訊檔，加快播放效率  
- **Lock 功能**：避免誤編輯音軌  
- **Markers**：支援在時間軸上插入標記，用於段落定位  

---

## 📂 設定檔
`modules/utils/config.json`  
- 存放使用者偏好  
- 若刪除會自動重建  
- 可手動編輯 JSON 修改參數  

---

## 🛠 常見問題

### Q1: 沒有聲音？
- 請確認 **Preferences → Output** 已設定  
- system 模式 → 是否有啟用 MIDI 裝置 (如 loopMIDI)  
- fluidsynth 模式 → SoundFont (.sf2) 是否存在

### Q2: 鋼琴卷軸音符播放延遲？
- 嘗試將 Latency Profile 設為 `high`  

### Q3: 匯入 MIDI 後沒有音軌？
- 檔案內可能沒有音符事件 (僅控制訊號)  
- 可檢查原 MIDI 檔內容  

---

## 📌 未來改進方向
- Drum Editor 模式  
- MIDI 即時錄製 (Live Record)  
- VST Plugin 支援  
- 匯出 WAV / MP3

---

## 📂 Project Structure

```text
Project/
├─ app.py                         # 入口整合（啟動 UI、載入設定與專案，掛上 Transport / 合成器）
├─ debug_runner.py                # 偵錯啟動器：統一 stdout/stderr、hook excepthook/threads，便於抓取例外
└─ modules/
   ├─ core/
   │  ├─ models.py               # 核心資料模型：Project/Track/Note/Marker/Tempo/TimeSig 等資料結構與工具
   │  ├─ io_midi.py              # MIDI 匯入/匯出：mido.MidiFile ↔ Project（含 tempo/time-signature/marker）
   │  └─ transport.py            # 播放排程器 Transport：tick 推進、loop、延遲參數、tickChanged 訊號
   ├─ audio/
   │  └─ synth.py                # 音訊輸出：FluidSynth 內建合成器 & 系統 MIDI 輸出 (mido) 的輕量封裝
   ├─ ui/
   │  ├─ main_window.py          # 主視窗與高層 UI 組裝、選單/工具列、事件連動與偏好設定入口
   │  ├─ piano_roll.py           # 鋼琴卷軸視圖：繪製網格/音符、編輯（新增/拖曳/複製貼上/Undo-Redo）、尺規/Marker
   │  ├─ preferences_dialog.py   # 偏好設定對話框：主題/輸出模式/預設值/備份及延遲模式設定
   │  ├─ tracks_panel.py         # 左側音軌面板：Track/Group 管理、Program/Volume、Mute/Solo、Freeze/Lock、拖放排序
   │  └─ theme.py                # 主題系統：多組預設色票 & 自訂色，產生 QPalette / Stylesheet
   └─ utils/
      └─ config.py               # 設定檔載入/儲存與預設值（latency presets、GM 名稱、顏色池等常數）
```

> 小提示：
> - `transport.py` 會送出 `tickChanged` 供 `piano_roll.py` 同步播放頭與自動捲動。
> - `io_midi.py` 會去重 tempo/拍號，並把 MIDI `marker` 轉成 `Project.markers`，匯出時也會寫回。
> - `synth.py` 的 `SynthOut` 支援載入 `.sf2` SoundFont；`MidiOut` 可直送外部裝置或虛擬埠。
```


# 🎬 Cheat Clip PRO

> **AI Powered Auto Clipper** — Ubah video YouTube panjang, siaran langsung, Google Drive, atau video dari situs mana pun menjadi TikTok / Shorts / Reels viral dengan subtitle animasi, face centering, dan musik — dalam hitungan menit.

Proyek ini adalah replika **Cheat Clip PRO** dengan beberapa tambahan penting:

| Fitur tambahan | Keterangan |
|---|---|
| 🔴 **Clip Siaran Langsung (LIVE)** | Rekam & potong live streaming YouTube langsung dari aplikasi. |
| 🎬 **Clip film dari situs mana pun** | Dukung lk21 / idlix / link `.mp4` / stream `.m3u8` (per part). |
| 🌐 **Terjemahan transkrip & judul (119 bahasa)** | Ubah bahasa transkrip + judul output, tanpa API key tambahan. |
| 🔌 **Provider AI fleksibel** | Gemini **atau** server OpenAI-compatible (9router, OpenRouter, LM Studio, Ollama, vLLM). |
| ⏰ **Penjadwal Jam Tayang (BARU)** | Hitung **berapa kali upload per jam/hari** yang aman + buat jadwal tayang konkret. |
| 🛠️ **Render anti-gagal** | Fallback unduh video penuh + potong lokal (mengatasi error *moov atom* / 403). |

---

## 🚀 Cara Install & Menjalankan (Paling Cepat)

### ⚡ Cara 1 — Windows, sekali klik (paling mudah)

1. Pastikan **Node.js**, **Python**, **ffmpeg**, dan **yt-dlp** sudah terpasang (lihat [Prasyarat](#-prasyarat) di bawah — hanya sekali).
2. Klik dua kali file **`Jalankan Cheat Clip PRO.bat`** di folder proyek (atau ikon **“Cheat Clip PRO”** di Desktop).
3. Skrip otomatis:
   * menyiapkan PATH `ffmpeg` / `yt-dlp`,
   * menjalankan **backend** + **frontend**,
   * membuka browser ke aplikasi.
4. Selesai — aplikasi terbuka di **http://localhost:5173/**. ✅

> Kalau muncul jendela hitam (terminal), **biarkan tetap terbuka** selama memakai aplikasi.

### 🧑‍💻 Cara 2 — Manual (semua OS)

```bash
# 1) Ambil kode
git clone <URL-REPO-ANDA> cheat-clip-pro
cd cheat-clip-pro

# 2) Dependensi frontend
npm install

# 3) Dependensi backend (disarankan pakai virtualenv)
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate
pip install -r backend/requirements.txt

# 4) Jalankan (backend + frontend sekaligus)
npm run dev
```

Lalu buka **http://localhost:5173/**.

| Alamat | Fungsi |
|---|---|
| **http://localhost:5173** | 🌐 Aplikasi web (buka ini) |
| http://localhost:8000 | ⚙️ Backend API |
| http://localhost:8000/docs | 📘 Dokumentasi API (Swagger) |

> Jika port **5173** sudah terpakai, Vite otomatis memakai port lain — lihat URL yang tertulis di terminal.

---

## 📋 Prasyarat

| Perangkat | Versi | Catatan |
|---|---|---|
| **Node.js** | v18+ (disarankan v20+) | [nodejs.org](https://nodejs.org/) |
| **Python** | 3.10+ (disarankan 3.11–3.13) | Centang **“Add Python to PATH”** saat install |
| **ffmpeg** | terbaru | Untuk potong & render video |
| **yt-dlp** | terbaru | Untuk mengunduh video/live |

**Windows (PowerShell):**
```powershell
winget install Git.Git
winget install OpenJS.NodeJS.LTS
winget install Python.Python.3.12
winget install Gyan.FFmpeg
winget install yt-dlp.yt-dlp
```
*(Tutup & buka ulang terminal setelah install agar PATH dikenali.)*

**macOS:**
```bash
brew install git node python ffmpeg-full yt-dlp
```
*(`ffmpeg-full` wajib untuk filter subtitle `libass`.)*

**Linux (Debian/Ubuntu):**
```bash
sudo apt update && sudo apt install -y git nodejs npm python3 python3-pip python3-venv ffmpeg
pip install yt-dlp
```

---

## 🎯 Cara Pakai Singkat

1. **Tempel URL** video (YouTube / Drive / live / situs film / `.mp4` / `.m3u8`) di kolom atas.
2. **Pilih durasi** — `~15s` (hook cepat), `~30s` (standar), `~60s` (cerita).
3. Klik **“Analisis Video”** → AI mencari momen paling menarik.
4. **Atur di Clip Studio** — rasio 9:16, face tracking, gaya subtitle, watermark, musik, akselerasi hardware.
5. **Batch Render** → unduh semua hasil sekaligus dalam satu **.ZIP**.

---

## ⏰ Fitur Baru: Penjadwal Jam Tayang

Menjawab pertanyaan *“berapa kali upload per jam yang pas?”* — panel **⏰ Jadwal Jam Tayang** (di bagian bawah **Clip Studio**) menghitung jadwal tayang otomatis dengan **aturan anti-shadowban**:

* **Maksimal 1 upload per jam** (aturan keras).
* **Jarak ideal antar-upload 2–4 jam**.
* **Hindari upload borongan** (penyebab utama shadowban).
* Batas aman & maksimum **per hari, per platform**.

**Cara pakai:**
1. Analisis klip seperti biasa.
2. Buka panel **⏰ Jadwal Jam Tayang**.
3. Pilih **platform** (YouTube Panjang / Shorts / TikTok / Multi / Umum), **berapa klip per hari**, dan **tanggal mulai**.
4. Klik **📅 Buat Jadwal** → muncul rekomendasi **berapa per jam**, **berapa per hari**, jam terbaik, total hari, dan tabel jadwal tiap klip.
5. Klik **📋 Salin** atau **⬇️ CSV** untuk mengekspor jadwal.

> **Catatan:** fitur ini membuat **jadwal + rekomendasi frekuensi** (planner offline, tanpa kredensial). Ini **tidak meng-upload otomatis** ke YouTube/TikTok — upload otomatis butuh login OAuth akun Anda.

**Endpoint API terkait:**
* `GET /api/schedule/platforms` — daftar platform + batas per hari
* `GET /api/schedule/recommend` — rekomendasi frekuensi saja
* `POST /api/schedule/plan` — jadwal lengkap (tanggal + jam per klip)

---

## 🔴 Cara Meng-clip Siaran Langsung (LIVE)

1. Tempel link YouTube live, mis. `https://www.youtube.com/watch?v=iipR5yUp36o`.
2. Aplikasi otomatis menampilkan badge **🔴 SIARAN LANGSUNG** (atau **⏳ PREMIERE**).
3. Klik **“Rekam Live”** → konfirmasi → rekaman berjalan (menampilkan durasi, ukuran, kecepatan).
   * Centang **“Rekam dari awal”** bila ingin merekam dari awal siaran.
4. Klik **“Stop & Pakai”** saat selesai → hasil jadi video lokal biasa.
5. Semua fitur Cheat Clip PRO berlaku penuh untuk hasil rekaman.

> Merekam live memerlukan `yt-dlp` + `ffmpeg` (sudah disertakan/diatur otomatis). Tombol **Analisis** pada link live akan diarahkan ke proses rekam terlebih dahulu.

---

## 🎬 Cara Meng-clip Film dari Situs Mana Pun (per part)

1. Tempel link halaman video dari **situs film apa pun** (lk21, idlix, dll.), link langsung `.mp4`, atau stream HLS `.m3u8`.
2. Aplikasi otomatis mengenali sumber non-YouTube/Drive dan mengunduh lewat `yt-dlp` (dengan cookies browser bila ada).
   * Progres unduhan ditampilkan real-time (**Mengunduh dari &lt;situs&gt;**).
3. Video **di-cache** — analisis link sama lagi jadi instan.
4. Di **Riwayat**, sumber ini diberi label **🎬 Film / Site**.

> Cocok untuk membuat **clip per part** dari serial/film.

---

## 🔌 Memakai 9router / Provider OpenAI-compatible

Kunci AI **tidak wajib dari Gemini**. Aplikasi mendukung **provider OpenAI-compatible** apa pun.

1. Buka **Pengaturan AI**.
2. Pilih **Provider**: `Gemini` atau `OpenAI-compatible (9router / OpenRouter / LM Studio / Ollama / vLLM)`.
3. Isi **Base URL** (default `http://localhost:20128/v1`) dan **API Key** (boleh kosong untuk server lokal).
4. Klik **Muat Model** untuk mengambil daftar model dari server.

> Pengaturan provider & Base URL disimpan otomatis di browser (localStorage).

---

## 🌐 Ubah Bahasa Transkrip & Judul (119 Bahasa)

* Pilih bahasa pada panel **Bahasa Output** (transkrip & judul terpisah).
* `auto`/kosong = tanpa terjemahan (bahasa asli).
* Timestamp transkrip tetap **1:1** dengan video.
* Daftar bahasa: `GET /api/languages` (119 bahasa).

---

## ❓ Masalah Umum & Solusinya

### 1. “Failed to render video” / `The system cannot find the file specified`
**Penyebab:** `ffmpeg` atau `yt-dlp` belum terpasang.
**Solusi:**
```powershell
winget install Gyan.FFmpeg
winget install yt-dlp.yt-dlp
```
*(lalu tutup & buka ulang terminal)* — atau `pip install yt-dlp`.

### 2. `No such filter: 'subtitles'`
**Penyebab:** FFmpeg tanpa filter `libass`.
**Solusi (macOS):** `brew install ffmpeg-full` lalu restart backend. Aplikasi otomatis memilih binary `ffmpeg-full` yang mendukung subtitle.

### 3. “Sign in to confirm you're not a bot”
**Penyebab:** YouTube memblokir unduhan tanpa login.
**Solusi:** klik tombol 🍪 **Cookies**, ekspor cookies YouTube (ekstensi *Get cookies.txt locally*), tempel ke aplikasi.

### 4. `moov atom not found` / unduhan klip gagal
**Penyebab:** unduhan potongan YouTube korup.
**Solusi:** sudah ditangani otomatis — aplikasi mengunduh **video penuh** lalu memotong **lokal** (fallback *Method 5*). Tidak perlu tindakan manual.

### 5. Apakah jalan di GPU AMD / Intel / Mac?
**Ya.** Otomatis mendukung **NVIDIA** (`h264_nvenc`), **AMD** (`h264_amf` di Radeon & Ryzen), **Intel** (`h264_qsv` di Arc & UHD), dan **CPU** (`libx264`). Bisa diganti di Render Settings / History.

### 6. Port 5173 / 8000 sudah dipakai
* Tutup instance lain yang berjalan, **atau** biarkan Vite memakai port alternatif (URL tertulis di terminal).
* Backend default di port **8000** (lihat `scripts/start-backend.js`).

---

## 🔑 Google Gemini API Key Gratis (1 Menit)

1. Buka **[Google AI Studio](https://aistudio.google.com/)** dan login dengan akun Google.
2. Klik **“Get API key”** → **“Create API key”**.
3. Salin key (diawali `AIzaSy...`).
4. Tempel ke kolom **Gemini API Key** di aplikasi.

> 💡 **Tips:** ketik `mock` di kolom API Key untuk menguji aplikasi dengan data contoh tanpa API key.

---

## 🔄 Update ke Versi Terbaru

```bash
git pull
npm install
pip install -r backend/requirements.txt
```
*(Aktifkan virtualenv dulu bila memakainya.)*

---

## 🗂️ Struktur Proyek Singkat

```
cheat-clip-pro/
├─ src/                     # Frontend (React + Vite + TypeScript)
│  └─ components/
│     ├─ ClipStudioSection.tsx   # Editor klip utama
│     └─ SchedulePanel.tsx       # ⏰ Panel Jadwal Jam Tayang (BARU)
├─ backend/                 # Backend (FastAPI + Python)
│  ├─ main.py               # Entry point, daftar router
│  ├─ video_engine.py       # Unduh/potong/render (ffmpeg + yt-dlp)
│  ├─ routers/              # Endpoint API (analyze, render, live, schedule, …)
│  ├─ services/
│  │  └─ schedule_service.py     # Logika penjadwal jam tayang (BARU)
│  └─ schemas/
│     └─ schedule.py             # Skema request/response jadwal (BARU)
├─ scripts/start-backend.js # Launcher backend
├─ Jalankan Cheat Clip PRO.bat   # Launcher sekali klik (Windows)
└─ package.json
```

---

## 📄 Lisensi

**MIT License** — bebas untuk penggunaan pribadi & komersial.

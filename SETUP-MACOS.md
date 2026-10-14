# Cheat Clip PRO — Panduan Install di macOS / Hackintosh

Panduan ini untuk menjalankan Cheat Clip PRO di **macOS** (termasuk **Hackintosh**).
Aplikasi ini **cross-platform**: frontend React + backend FastAPI/Python. Yang berbeda
dari Windows hanyalah cara install dependensi dan launcher-nya (file `.bat` **tidak**
jalan di macOS).

> Ringkas: **Node.js + Python 3.11/3.12 + ffmpeg-full + yt-dlp + Deno**, lalu `npm run dev`.

---

## 0. Yang perlu diganti dari versi Windows

| Di Windows | Di macOS / Hackintosh |
|---|---|
| `Jalankan Cheat Clip PRO.bat` | `npm run dev` (atau buat `Jalankan.command`) |
| `winget install ...` | `brew install ...` |
| `python_venv\Scripts\python.exe` | `venv/bin/python` |
| ffmpeg bawaan winget (ada libass) | **`ffmpeg-full`** (Homebrew `ffmpeg` biasa **tanpa libass**) |
| Encoder GPU: `h264_nvenc` / `h264_amf` / `h264_qsv` | **tidak ada** → otomatis pakai CPU `libx264` |

Kode backend **sudah** mendeteksi macOS:
- mencari ffmpeg di `/opt/homebrew/opt/ffmpeg-full/bin` dan `/usr/local/opt/ffmpeg-full/bin`
  (lihat `backend/video_engine.py` → `ensure_ffmpeg_in_path()`).
- `start-backend.js` sudah menangani `venv/bin/python` (Unix) selain `venv\Scripts\python.exe`.

---

## 1. Install Homebrew (kalau belum ada)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Setelah selesai, ikuti instruksi di layar untuk menambahkan `brew` ke PATH.
Cek: `brew --version`.

---

## 2. Install dependensi sistem

```bash
brew install git node python@3.12 ffmpeg-full yt-dlp deno
```

Penjelasan:

| Paket | Kenapa |
|---|---|
| `git` | clone & update repo |
| `node` | build frontend (Vite) **dan** JS runtime untuk yt-dlp |
| `python@3.12` | runtime backend (3.10–3.13 aman; **hindari 3.14** karena beberapa wheel belum ada) |
| **`ffmpeg-full`** | WAJIB — `ffmpeg` biasa tidak punya filter `libass` untuk burn-in subtitle |
| `yt-dlp` | unduh video/live |
| `deno` | pemecah tantangan JS YouTube (lihat langkah 6) |

> **Penting:** `ffmpeg-full` bersifat *keg-only* (tidak otomatis masuk PATH). Kode
> Cheat Clip PRO mencarinya sendiri di `/opt/homebrew/opt/ffmpeg-full/bin`
> (Apple Silicon) atau `/usr/local/opt/ffmpeg-full/bin` (Intel / Hackintosh).
> Tidak perlu `brew link --force`.

Cek ffmpeg punya libass:
```bash
/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg -filters | grep subtitles
# Hackintosh Intel: /usr/local/opt/ffmpeg-full/bin/ffmpeg -filters | grep subtitles
```
Harus muncul baris `subtitles` dan `ass`.

---

## 3. Ambil kode

```bash
cd ~
git clone https://github.com/Z-dev-collab/cheat-clip-pro.git CheatClipPro
cd CheatClipPro
```

> Kalau folder sudah Anda copy dari laptop Windows, tetap **lakukan `git pull`**
> agar dapat commit terbaru.

---

## 4. Setup Python (backend)

```bash
python3.12 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r backend/requirements.txt
```

> ⚠️ Ini mengunduh **torch** + **openai-whisper** — besar (1–2 GB+) dan butuh
> beberapa menit. Kalau ingin lebih ringan, transkripsi bisa memakai
> `faster-whisper` (CPU, int8); tapi `requirements.txt` default sudah cukup.

Cek cepat:
```bash
python -c "import fastapi, uvicorn; import backend.config; print('OK')"
```

---

## 5. Setup frontend (Node)

```bash
npm install
```

---

## 6. JS runtime + EJS solver untuk yt-dlp (WAJIB)

YouTube memakai tantangan JS (`n`/signature). yt-dlp butuh **JS runtime** + komponen
pemecah **EJS**. Kode sudah menambahkan `--remote-components ejs:github` secara otomatis
(lihat `backend/video_engine.py`), dan akan memakai `deno` bila ada, kalau tidak `node`.

```bash
deno --version   # pastikan terpasang
node -v          # minimal v18 (disarankan v20+)
```

Tanpa ini, unduhan akan gagal dengan pesan *"Sign in to confirm you're not a bot"*
yang muncul di aplikasi sebagai *"Failed to download video clip segment"*.

---

## 7. Cookies YouTube (bila diminta login)

Kalau YouTube menolak unduhan:
1. Pasang ekstensi browser **"Get cookies.txt LOCALLY"**.
2. Buka YouTube dalam keadaan login → ekspor cookies (format Netscape).
3. Simpan sebagai **`cookies.txt`** di **root folder proyek** (`~/CheatClipPro/cookies.txt`).
4. `video_engine.py` otomatis mendeteksinya.

File ini sudah masuk `.gitignore` — **tidak akan ter-push**.

---

## 8. Jalankan aplikasi

```bash
# Aktifkan venv dulu (kalau belum)
source venv/bin/activate

# Jalankan frontend + backend sekaligus
npm run dev
```

Lalu buka:

| Alamat | Fungsi |
|---|---|
| **http://localhost:5173** | 🌐 Aplikasi (buka ini) |
| http://localhost:8000 | ⚙️ Backend API |
| http://localhost:8000/docs | 📘 Dokumentasi API (Swagger) |

Hentikan dengan `Ctrl + C`.

---

## 9. (Opsional) Launcher sekali klik untuk macOS

Buat file `Jalankan Cheat Clip PRO.command` di root proyek:

```bash
#!/bin/bash
cd "$(dirname "$0")"
export PATH="/opt/homebrew/opt/ffmpeg-full/bin:/usr/local/opt/ffmpeg-full/bin:$PATH"
source venv/bin/activate
( sleep 8 && open http://localhost:5173/ ) &
npm run dev
```

Lalu:
```bash
chmod +x "Jalankan Cheat Clip PRO.command"
```

Klik dua kali file itu untuk menjalankan. (Kalau macOS menolak karena Gatekeeper:
klik kanan → **Open** → **Open** sekali saja.)

---

## 10. Catatan khusus Hackintosh

- **Homebrew path**: Hackintosh umumnya **Intel** → prefix Homebrew di **`/usr/local`**
  (bukan `/opt/homebrew`). Kode sudah mengecek **kedua** lokasi, jadi aman.
- **CPU/GPU render**: Tidak ada NVENC/AMF/QSV di macOS. Aplikasi otomatis turun ke
  **`libx264` (CPU)**. Hasil sama, hanya lebih lambat. (Kalau nanti ingin GPU,
  perlu tambahan dukungan `h264_videotoolbox` — belum ada di kode saat ini.)
- **Gatekeeper / quarantine**: kalau ada file yang diblokir, jalankan
  `xattr -dr com.apple.quarantine ~/CheatClipPro`.
- **Filesystem**: APFS default *case-insensitive* → tidak ada masalah huruf besar/kecil
  seperti kalau pindah ke filesystem case-sensitive.
- **Disk**: siapkan ruang untuk `torch`/whisper + cache video (bisa beberapa GB).
- **Python**: pakai **3.11 atau 3.12**. Hindari 3.14 (banyak wheel belum tersedia).

---

## Troubleshooting cepat

| Gejala | Solusi |
|---|---|
| `No such filter: 'subtitles'` | Pakai **`ffmpeg-full`**, bukan `ffmpeg` biasa |
| `Failed to download video clip segment` | Pasang `deno` (langkah 6) & perbarui `yt-dlp` |
| `Sign in to confirm you're not a bot` | Tambahkan `cookies.txt` (langkah 7) |
| Port 8000/5173 sudah dipakai | Tutup instance lain; backend otomatis reclaim port 8000 |
| `command not found: python3.12` | `brew install python@3.12` lalu buka terminal baru |
| Font/subtitle aneh | Pastikan font di `backend/fonts/` ada; URL sudah di-encode otomatis |

import re
from typing import List, Optional

try:
    from backend.utils.languages import SUPPORTED_LANGUAGES as _SUPPORTED_LANGUAGES
except Exception:  # pragma: no cover - defensive, keeps this module importable standalone
    _SUPPORTED_LANGUAGES = {}


def parse_time_str(time_str: str) -> float:
    """Parses time string in formats like HH:MM:SS,mmm or MM:SS,mmm or HH:MM:SS or MM:SS to seconds."""
    time_str = time_str.strip().replace(',', '.')
    # Extract millisecond if present
    ms = 0.0
    if '.' in time_str:
        parts = time_str.split('.')
        time_str = parts[0]
        try:
            ms = float('0.' + parts[1])
        except ValueError:
            pass
            
    time_parts = time_str.split(':')
    try:
        if len(time_parts) == 3:
            return int(time_parts[0]) * 3600 + int(time_parts[1]) * 60 + int(time_parts[2]) + ms
        elif len(time_parts) == 2:
            return int(time_parts[0]) * 60 + int(time_parts[1]) + ms
        elif len(time_parts) == 1:
            return float(time_parts[0]) + ms
    except ValueError:
        return 0.0

def parse_manual_subtitles(content: str, default_duration: float = 0.0) -> List[dict]:
    # Normalize line endings
    content = content.replace('\r\n', '\n').strip()
    
    # 1. Try standard SRT parsing first
    srt_regex = r'(?:\d+\n)?(\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{3})\n(.*?)(?=\n\n|\n\d+\n|\Z)'
    srt_matches = re.findall(srt_regex, content, re.DOTALL)
    
    if srt_matches:
        results = []
        for start_str, end_str, text in srt_matches:
            start = parse_time_str(start_str)
            end = parse_time_str(end_str)
            cleaned_text = text.replace('\n', ' ').strip()
            results.append({
                "text": cleaned_text,
                "start": start,
                "duration": max(0.1, end - start)
            })
        if results:
            return results

    # 2. Try parsing line-by-line for timestamped lines
    line_time_range_regex = r'^[\[\(]?(\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?)\s*(?:-|-->|\s)\s*(\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?)[\]\)]?\s*(.*)'
    line_single_time_regex = r'^[\[\(]?(\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?)[\]\)]?\s*(.*)'
    
    lines = content.split('\n')
    results = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Match range first (e.g. 00:12 - 00:15 Text)
        m_range = re.match(line_time_range_regex, line)
        if m_range:
            start = parse_time_str(m_range.group(1))
            end = parse_time_str(m_range.group(2))
            text = m_range.group(3).strip()
            results.append({
                "text": text,
                "start": start,
                "duration": max(0.1, end - start)
            })
            continue
            
        # Match single timestamp (e.g. 00:12 Text)
        m_single = re.match(line_single_time_regex, line)
        if m_single:
            start = parse_time_str(m_single.group(1))
            text = m_single.group(2).strip()
            results.append({
                "text": text,
                "start": start,
                "duration": -1.0  # Will fill in later
            })
            continue

    if results:
        # Resolve duration for single timestamps
        for i in range(len(results)):
            if results[i]["duration"] == -1.0:
                if i + 1 < len(results):
                    diff = results[i+1]["start"] - results[i]["start"]
                    results[i]["duration"] = max(0.5, diff)
                else:
                    results[i]["duration"] = 3.0  # default for the last line
        return results

    # 3. Fallback: split text into paragraphs or sentences and distribute evenly across video duration
    duration_to_use = default_duration if default_duration > 0 else 60.0
    raw_sentences = [s.strip() for s in re.split(r'(?<=[.?!])\s+|\n+', content) if s.strip()]
    if raw_sentences:
        num_sentences = len(raw_sentences)
        sec_per_sentence = duration_to_use / num_sentences
        results = []
        for i, text in enumerate(raw_sentences):
            start = i * sec_per_sentence
            results.append({
                "text": text,
                "start": round(start, 2),
                "duration": round(sec_per_sentence, 2)
            })
        return results
        
    return []

def extract_video_id(url: str) -> Optional[str]:
    """Extracts the 11-character YouTube video ID from various URL formats including live streams, shorts, embed, watch?v=, youtu.be, etc."""
    if not url:
        return None
    trimmed = url.strip()
    if re.match(r"^[a-zA-Z0-9_-]{11}$", trimmed):
        return trimmed

    patterns = [
        r"(?:[?&]v=)([a-zA-Z0-9_-]{11})",
        r"(?:youtu\.be\/|(?:www\.|m\.)?youtube(?:-nocookie)?\.com\/(?:embed|v|shorts|live)\/)([a-zA-Z0-9_-]{11})",
        r"(?:v=|\/v\/|embed\/|shorts\/|live\/|youtu\.be\/|\/embed\/|\/watch\?v=|\/watch\?.+&v=)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, trimmed)
        if match:
            return match.group(1)

    return None

def lowercase_hashtags_in_string(text: str) -> str:
    """Finds all hashtags (#word) in a string and converts them to lowercase."""
    if not text:
        return text
    return re.sub(r'#\w+', lambda m: m.group(0).lower(), text)

LANGUAGE_NAMES = {
    'id': 'Indonesian (Bahasa Indonesia)',
    'en': 'English',
    'es': 'Spanish (Español)',
    'pt': 'Portuguese (Português)',
    'fr': 'French (Français)',
    'de': 'German (Deutsch)',
    'ja': 'Japanese (日本語)',
    'ko': 'Korean (한국어)',
    'zh': 'Chinese (中文)',
    'ar': 'Arabic (العربية)',
    'ru': 'Russian (Русский)',
}


def language_display_name(code: Optional[str]) -> str:
    """Map a language code to a human-readable name, preferring the full registry."""
    if not code:
        return ""
    low = str(code).strip().lower().split('-')[0]
    if low in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[low]
    for c, name in _SUPPORTED_LANGUAGES.items():
        if c.lower().split('-')[0] == low:
            return name
    return str(code).strip()


# Unicode script ranges used for high-precision non-Latin language detection.
SCRIPT_RANGES = {
    'ar': r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]',
    'he': r'[\u0590-\u05FF]',
    'el': r'[\u0370-\u03FF\u1F00-\u1FFF]',
    'th': r'[\u0E00-\u0E7F]',
    'ja': r'[\u3040-\u309F\u30A0-\u30FF]',
    'ko': r'[\uAC00-\uD7AF\u1100-\u11FF]',
    'zh': r'[\u4E00-\u9FFF]',
    'ru': r'[\u0400-\u04FF]',
    'hi': r'[\u0900-\u097F]',
    'bn': r'[\u0980-\u09FF]',
    'ta': r'[\u0B80-\u0BFF]',
    'te': r'[\u0C00-\u0C7F]',
    'kn': r'[\u0C80-\u0CFF]',
    'ml': r'[\u0D00-\u0D7F]',
    'gu': r'[\u0A80-\u0AFF]',
    'pa': r'[\u0A00-\u0A7F]',
    'my': r'[\u1000-\u109F]',
    'km': r'[\u1780-\u17FF]',
    'lo': r'[\u0E80-\u0EFF]',
    'si': r'[\u0D80-\u0DFF]',
    'ka': r'[\u10A0-\u10FF]',
    'hy': r'[\u0530-\u058F]',
    'am': r'[\u1200-\u137F]',
}

# Latin-script stopword sets for languages beyond English/Indonesian.
IT_STOPWORDS = {
    'il', 'di', 'che', 'la', 'e', 'in', 'un', 'per', 'non', 'con', 'una', 'del',
    'sono', 'si', 'come', 'ma', 'anche', 'questo', 'questo', 'le', 'da', 'gli',
    'ho', 'hai', 'ha', 'cosa', 'più', 'molto', 'tutto', 'bene', 'essere'
}
NL_STOPWORDS = {
    'de', 'het', 'een', 'van', 'en', 'in', 'is', 'dat', 'op', 'te', 'met', 'voor',
    'niet', 'zijn', 'er', 'aan', 'om', 'ook', 'als', 'dan', 'maar', 'bij', 'of',
    'je', 'we', 'ze', 'ik', 'hij', 'zij', 'wat', 'hoe', 'waarom', 'omdat'
}
TR_STOPWORDS = {
    'bir', 've', 'bu', 'da', 'de', 'için', 'ile', 'mi', 'ne', 'çok', 'daha',
    'var', 'yok', 'ama', 'ki', 'ben', 'sen', 'o', 'biz', 'siz', 'onlar',
    'nasıl', 'neden', 'çünkü', 'şey', 'gibi', 'kadar', 'sonra'
}
VI_STOPWORDS = {
    'và', 'của', 'có', 'là', 'không', 'được', 'trong', 'người', 'những', 'cho',
    'một', 'các', 'với', 'này', 'đã', 'để', 'khi', 'như', 'từ', 'về', 'rất',
    'cũng', 'nhưng', 'thì', 'ở', 'ra', 'vào', 'lên'
}
PL_STOPWORDS = {
    'nie', 'się', 'jest', 'że', 'to', 'na', 'do', 'ale', 'jak', 'tak', 'co',
    'po', 'za', 'przez', 'czy', 'już', 'tylko', 'bardzo', 'może', 'kiedy',
    'gdzie', 'dlaczego', 'ponieważ', 'oraz', 'lub', 'albo'
}
RO_STOPWORDS = {
    'este', 'nu', 'și', 'de', 'la', 'în', 'cu', 'un', 'o', 'care', 'să', 'se',
    'pe', 'ce', 'mai', 'dar', 'pentru', 'din', 'sunt', 'foarte', 'când', 'unde'
}
MS_STOPWORDS = {
    'yang', 'dan', 'di', 'ke', 'dari', 'pada', 'dalam', 'untuk', 'dengan',
    'saya', 'awak', 'kamu', 'dia', 'kami', 'kita', 'mereka', 'ini', 'itu',
    'tidak', 'tak', 'ada', 'ialah', 'adalah', 'akan', 'sudah', 'boleh'
}
TL_STOPWORDS = {
    'ang', 'ng', 'sa', 'na', 'ay', 'at', 'para', 'hindi', 'ito', 'iyan',
    'ako', 'ikaw', 'siya', 'kami', 'tayo', 'sila', 'may', 'mayroon', 'wala',
    'pero', 'kung', 'dahil', 'kapag', 'paano', 'bakit'
}

ID_DISTINCT_MARKERS = {
    'yang', 'untuk', 'dengan', 'karena', 'adalah', 'sudah', 'belum', 'tidak', 'nggak',
    'banget', 'mereka', 'kalian', 'seperti', 'apakah', 'bagaimana', 'kenapa', 'sekarang',
    'kemudian', 'tetapi', 'walaupun', 'maksudnya', 'artinya', 'bisa', 'akan', 'harus',
    'jadi', 'pada', 'dalam', 'kami', 'kita', 'saya', 'kamu', 'beliau', 'makanya', 'padahal',
    'bahwa', 'ketika', 'tersebut', 'mengapa', 'sebab'
}

EN_DISTINCT_MARKERS = {
    'the', 'that', 'with', 'from', 'they', 'their', 'what', 'which', 'would', 'could',
    'should', 'about', 'there', 'were', 'been', 'have', 'having', 'your', 'just', 'when',
    'where', 'because', 'people', 'think', 'thinking', 'going', 'really', 'something',
    'always', 'never', 'every', 'before', 'after', 'through', 'between', 'without',
    'these', 'those', 'also', 'wouldn\'t', 'couldn\'t', 'shouldn\'t', 'doesn\'t', 'isn\'t'
}

ID_STOPWORDS = {
    # Pronouns & People
    'saya', 'aku', 'gue', 'gua', 'gw', 'kamu', 'lu', 'lo', 'elu', 'kita', 'kami', 'mereka',
    'dia', 'beliau', 'kalian', 'orang', 'bang', 'mas', 'mbak', 'kak', 'kakak',
    'pak', 'bapak', 'bu', 'ibu', 'om', 'tante', 'anak', 'teman', 'temen',
    # Connectives, Prepositions & Articles
    'yang', 'dan', 'di', 'ke', 'dari', 'pada', 'dalam', 'untuk', 'utk', 'dengan', 'dgn',
    'sama', 'karena', 'krn', 'sebab', 'oleh', 'bagi', 'antara', 'tentang', 'seperti', 'kayak',
    'kalo', 'kalau', 'klo', 'jika', 'apabila', 'tapi', 'tp', 'tetapi', 'namun', 'melainkan',
    'walaupun', 'meskipun', 'supaya', 'agar', 'atau', 'maupun', 'serta', 'yaitu', 'yakni',
    # Particles & Conversational Markers
    'ya', 'nih', 'tuh', 'dong', 'kan', 'lah', 'deh', 'sih', 'kok', 'loh', 'lho', 'kah', 'pun',
    'nggak', 'gak', 'ngga', 'ga', 'tak', 'bukan', 'jangan', 'udah', 'udh', 'sudah', 'belum', 'blm',
    'banget', 'bgt', 'aja', 'saja', 'doang', 'cuma', 'cuman', 'hanya', 'sangat', 'amat',
    'gitu', 'gini', 'begitu', 'begini', 'makanya', 'makannya', 'padahal', 'emang', 'memang',
    'bener', 'beneran', 'benar', 'pasti', 'bisa', 'gabisa', 'dapat', 'boleh', 'harus', 'hrs',
    'mau', 'ingin', 'bakal', 'akan', 'lagi', 'lg', 'sedang', 'masih', 'msh', 'terus', 'trus',
    'dulu', 'dl', 'kemarin', 'besok', 'nanti', 'sekarang', 'skrg', 'saat', 'ketika', 'waktu', 'pas',
    # Question words
    'apa', 'apakah', 'siapa', 'kenapa', 'knp', 'mengapa', 'gimana', 'bagaimana', 'dimana', 'kapan',
    # Common speech verbs & concepts
    'jadi', 'adalah', 'ada', 'punya', 'tau', 'tahu', 'tonton', 'nonton', 'bilang', 'katanya',
    'kata', 'ngomong', 'bikin', 'buat', 'liat', 'lihat', 'pikir', 'kira', 'rasa', 'rasanya',
    'menurut', 'maksud', 'maksudnya', 'paham', 'ngerti', 'banyak', 'kurang', 'lebih', 'semua',
    'sendiri', 'lain', 'lainnya', 'biasa', 'luar', 'soal', 'soalnya', 'masalah', 'cerita'
}

EN_STOPWORDS = {
    'the', 'and', 'to', 'of', 'a', 'in', 'that', 'is', 'it', 'you', 'for', 'on',
    'are', 'as', 'with', 'they', 'at', 'be', 'this', 'have', 'from', 'or', 'one',
    'had', 'by', 'but', 'not', 'what', 'all', 'were', 'we', 'when', 'your', 'can',
    'there', 'an', 'which', 'she', 'do', 'how', 'their', 'if', 'will', 'up', 'about',
    'out', 'so', 'would', 'like', 'just', 'know', 'people', 'think', 'going', 'been',
    'them', 'some', 'could', 'him', 'into', 'other', 'than', 'then', 'now', 'look',
    'only', 'come', 'its', 'over', 'also', 'back', 'after', 'use', 'two', 'our',
    'work', 'first', 'well', 'way', 'even', 'new', 'want', 'because', 'any', 'these',
    'give', 'day', 'most', 'us', 'time', 'really', 'something', 'good', 'make',
    'gonna', 'wanna', 'gotta', 'yeah', 'yes', 'no', 'okay', 'right', 'here', 'why',
    'where', 'who', 'should', 'must', 'said', 'say', 'tell', 'told', 'see', 'saw',
    'much', 'many', 'very', 'still', 'never', 'always', 'today', 'tomorrow', 'yesterday'
}

ES_STOPWORDS = {
    'de', 'la', 'que', 'el', 'en', 'y', 'a', 'los', 'del', 'se', 'las', 'por',
    'un', 'para', 'con', 'no', 'una', 'su', 'al', 'lo', 'como', 'más', 'pero',
    'sus', 'le', 'ya', 'o', 'este', 'sí', 'porque', 'esta', 'son', 'entre',
    'está', 'cuando', 'muy', 'sin', 'sobre', 'ser', 'tiene', 'también', 'me',
    'hasta', 'hay', 'donde', 'quien', 'desde', 'todo', 'nos', 'durante', 'todos',
    'uno', 'les', 'ni', 'contra', 'otros', 'ese', 'eso', 'ante', 'ellos', 'esto'
}

PT_STOPWORDS = {
    'de', 'a', 'o', 'que', 'e', 'do', 'da', 'em', 'um', 'para', 'é', 'com',
    'não', 'uma', 'os', 'no', 'se', 'na', 'por', 'mais', 'as', 'dos', 'como',
    'mas', 'foi', 'ao', 'ele', 'das', 'tem', 'à', 'seu', 'sua', 'ou', 'ser',
    'quando', 'muito', 'há', 'nos', 'já', 'está', 'eu', 'também', 'só', 'pelo',
    'pela', 'você', 'isso', 'ela', 'entre', 'depois'
}

FR_STOPWORDS = {
    'de', 'la', 'le', 'et', 'les', 'des', 'en', 'un', 'du', 'une', 'que', 'est',
    'pour', 'qui', 'dans', 'a', 'par', 'plus', 'pas', 'au', 'sur', 'ne', 'se',
    'ce', 'il', 'sont', 'avec', 'son', 'cette', 'aux', 'ses', 'mais', 'ou',
    'ont', 'tout', 'comme', 'nous', 'sa', 'vous', 'on', 'y', 'donc', 'quand',
    'toujours', 'leur', 'bien', 'cela', 'être', 'fait', 'aussi', 'très', 'peut',
    'vraiment', 'chose', 'gens', 'mieux', 'travail', 'déjà', 'même', 'après',
    'où', 'ça', 'était', 'avoir', 'faire', 'dire', 'sans', 'sous', 'entre',
    'encore', 'autre', 'tous', 'elles', 'ils', 'je', 'tu', 'moi', 'toi'
}

DE_STOPWORDS = {
    'der', 'die', 'und', 'in', 'den', 'von', 'zu', 'das', 'mit', 'sich', 'des',
    'auf', 'für', 'ist', 'im', 'dem', 'nicht', 'ein', 'eine', 'als', 'auch',
    'es', 'an', 'werden', 'aus', 'er', 'hat', 'dass', 'sie', 'nach', 'wird',
    'bei', 'einer', 'um', 'am', 'sind', 'noch', 'wie', 'einem', 'über'
}

# Maps a language code to its Latin-script stopword set. Defined AFTER every set
# so the references resolve at import time.
LATIN_STOPWORD_SETS = {
    'es': ES_STOPWORDS,
    'pt': PT_STOPWORDS,
    'fr': FR_STOPWORDS,
    'de': DE_STOPWORDS,
    'it': IT_STOPWORDS,
    'nl': NL_STOPWORDS,
    'tr': TR_STOPWORDS,
    'vi': VI_STOPWORDS,
    'pl': PL_STOPWORDS,
    'ro': RO_STOPWORDS,
    'ms': MS_STOPWORDS,
    'tl': TL_STOPWORDS,
}

def detect_transcript_language(
    transcript_lines: List[dict],
    title: str = "",
    hint: Optional[str] = None,
    hint_confidence: float = 0.0,
) -> dict:
    """
    Detects the primary spoken language of the video transcript with high precision.

    Strategy (most reliable signal first):
    1. `hint` — a language code supplied by a real ASR (Whisper's `info.language`)
       or by the caption/audio track metadata. Whisper is very accurate, so a
       confident hint wins immediately.
    2. Non-Latin script detection (Arabic, CJK, Cyrillic, Devanagari, Thai, ...).
    3. Latin-script scoring across many languages. Stopwords are weighted by how
       DISCRIMINATIVE they are (a word found in only one candidate language
       counts fully; a word shared by many counts little), which fixes the
       Spanish/Portuguese/French/Italian collisions caused by shared function
       words like 'de', 'la', 'que'.

    Sampling is spread across the entire transcript (up to 1,000 lines) and
    cross-referenced with the video title.

    Returns dict: {'code': str, 'name': str, 'confidence': float}
    """
    # 1. Trust a confident ASR/metadata hint first (works even with no text).
    hint_result = _resolve_hint(hint, hint_confidence, bool(transcript_lines) or bool(title))
    if hint_result:
        return hint_result

    if not transcript_lines and not title:
        return _finalize_language('en', 0.5)

    sample_texts = [title] if title else []

    # Sample up to 1,000 lines across the transcript
    if transcript_lines:
        total_lines = len(transcript_lines)
        if total_lines <= 1000:
            lines_to_sample = transcript_lines
        else:
            step = total_lines / 1000.0
            lines_to_sample = [transcript_lines[int(i * step)] for i in range(1000)]
        for line in lines_to_sample:
            t = line.get("text", "")
            if t:
                sample_texts.append(t)

    full_sample = " ".join(sample_texts).strip()
    if not full_sample:
        return _finalize_language('en', 0.5)

    # Extract words
    tokens = re.findall(r'\b[a-zA-Z\u00C0-\u024F\u1E00-\u1EFF]+', full_sample.lower())
    title_tokens = set(re.findall(r'\b[a-zA-Z]+', title.lower())) if title else set()

    # 2. Non-Latin Script Check — count characters per script.
    all_letters = re.findall(r'\w', full_sample, flags=re.UNICODE)
    total_letters = len(all_letters) if all_letters else 1

    script_counts = {}
    for code, pattern in SCRIPT_RANGES.items():
        count = len(re.findall(pattern, full_sample))
        if count:
            script_counts[code] = count

    if script_counts:
        best_script, best_count = max(script_counts.items(), key=lambda kv: kv[1])
        ratio = best_count / total_letters
        # Japanese vs Chinese: kana presence is decisive.
        if 'ja' in script_counts and script_counts['ja'] / total_letters > 0.15:
            best_script, ratio = 'ja', script_counts['ja'] / total_letters
        # Require a meaningful share so a stray foreign word cannot dominate.
        if ratio >= 0.30:
            return _finalize_language(best_script, min(0.97, 0.80 + ratio * 0.17))

    # 3. Indonesian grammatical prefixes / suffixes pattern (a strong signal that
    #    helps separate Indonesian from Malay and from English).
    id_morphology_matches = sum(
        1 for w in tokens
        if len(w) >= 5 and (
            w.startswith(('meng', 'men', 'mem', 'peng', 'pen', 'pem', 'ber', 'ter')) or
            w.endswith(('kan', 'nya', 'lah', 'kah', 'pun'))
        )
    )

    id_distinct = sum(1 for w in tokens if w in ID_DISTINCT_MARKERS) + (id_morphology_matches // 3)
    en_distinct = sum(1 for w in tokens if w in EN_DISTINCT_MARKERS)

    # 4. Unified, discriminative Latin-script scoring.
    latin_sets = {
        'id': ID_STOPWORDS,
        'en': EN_STOPWORDS,
        **LATIN_STOPWORD_SETS,
    }
    token_set = set(tokens)
    # How many candidate languages each token appears in → lower = more distinctive.
    token_spread = {}
    for w in token_set:
        token_spread[w] = sum(1 for s in latin_sets.values() if w in s) or 1

    scores = {}
    for code, stopset in latin_sets.items():
        matched = [w for w in token_set if w in stopset]
        weighted = sum(1.0 / token_spread[w] for w in matched)
        scores[code] = weighted

    # Indonesian gets a small boost from its morphology signal.
    scores['id'] = scores.get('id', 0.0) + (id_morphology_matches // 3) * 0.5

    best_lang, best_score = max(scores.items(), key=lambda kv: kv[1])
    sorted_scores = sorted(scores.values(), reverse=True)
    runner_up = sorted_scores[1] if len(sorted_scores) > 1 else 0.0

    total_tokens = max(1, len(token_set))

    # 5. Decide. Prefer a clear leader; otherwise fall back to id/en markers.
    if best_score >= 2.0 and best_score >= runner_up * 1.25 + 0.5:
        density = best_score / total_tokens
        confidence = round(min(0.95, max(0.6, 0.55 + density * 1.5)), 2)
        return _finalize_language(best_lang, confidence)

    # Strong English / Indonesian distinct-marker signals as a tie-breaker.
    if en_distinct >= 3 and en_distinct > id_distinct * 1.5:
        return _finalize_language('en', 0.8)
    if id_distinct >= 3 and id_distinct > en_distinct * 1.3:
        return _finalize_language('id', 0.8)

    # Title language indicators.
    title_id = sum(1 for w in title_tokens if w in ID_DISTINCT_MARKERS or w in ID_STOPWORDS)
    title_en = sum(1 for w in title_tokens if w in EN_DISTINCT_MARKERS or w in EN_STOPWORDS)
    if title_id > title_en:
        return _finalize_language('id', 0.7)
    if title_en > title_id:
        return _finalize_language('en', 0.7)

    # Otherwise trust the top scorer if it has any real signal, else English.
    if best_score >= 1.0:
        return _finalize_language(best_lang, 0.65)
    return _finalize_language('en', 0.6)


def _finalize_language(code: str, confidence: float) -> dict:
    """Build the canonical detection result, resolving a display name."""
    base = str(code or 'en').strip().lower().split('-')[0] or 'en'
    return {
        'code': base,
        'name': language_display_name(base) or LANGUAGE_NAMES.get(base, base.upper()),
        'confidence': round(float(confidence), 2),
    }


def _resolve_hint(hint: Optional[str], hint_confidence: float, has_text: bool) -> Optional[dict]:
    """Return a detection result from an ASR/metadata language hint, or None.

    A hint is trusted when it names a known language and either comes with a
    confident probability (>= 0.5) or there is no transcript text to cross-check
    against.
    """
    if not hint:
        return None
    raw = str(hint).strip().lower()
    if not raw or raw in ('unknown', 'und', 'none', 'auto'):
        return None
    base = raw.split('-')[0]
    known = base in LANGUAGE_NAMES or any(c.lower().split('-')[0] == base for c in _SUPPORTED_LANGUAGES)
    if not known:
        return None
    if has_text and hint_confidence and hint_confidence < 0.5:
        # Weak hint with plenty of text — let the text-based detection decide.
        return None
    confidence = hint_confidence if hint_confidence else 0.9
    return _finalize_language(base, min(0.99, max(0.6, confidence)))


def sanitize_first_person_title(title: str, speaker_or_channel: str = "", lang: str = "en") -> str:
    """
    Sanitizes accidental first-person perspective ('I', 'Me', 'My', 'Saya', 'Aku', 'Gue')
    from generated clip titles and title suggestions, replacing them with speaker or channel attribution,
    or objective framing so titles never appear as the user's personal opinion.
    Preserves the target language (Indonesian, English, Spanish, etc.).
    """
    if not title:
        return title
    t = title.strip()
    speaker = speaker_or_channel.strip() if speaker_or_channel else ""
    if lang == "id":
        subject = speaker if speaker else "Host"
    elif lang == "es":
        subject = speaker if speaker else "El Presentador"
    else:
        subject = speaker if speaker else "The Speaker"

    # 1. English Why / How / What / When / Where
    t = re.sub(r"^why\s+i\s+think\b", f"{subject} Explains Why", t, flags=re.IGNORECASE)
    t = re.sub(r"^why\s+i\s+believe\b", f"{subject} Explains Why", t, flags=re.IGNORECASE)
    t = re.sub(r"^why\s+i\s+", f"Why {subject} ", t, flags=re.IGNORECASE)
    t = re.sub(r"^how\s+i\s+", f"How {subject} ", t, flags=re.IGNORECASE)
    t = re.sub(r"^what\s+i\s+think\b", f"{subject}'s Thoughts On", t, flags=re.IGNORECASE)
    t = re.sub(r"^what\s+i\s+learned\b", f"What {subject} Learned", t, flags=re.IGNORECASE)
    t = re.sub(r"^what\s+i\s+", f"What {subject} ", t, flags=re.IGNORECASE)
    t = re.sub(r"^when\s+i\s+", f"When {subject} ", t, flags=re.IGNORECASE)
    t = re.sub(r"^where\s+i\s+", f"Where {subject} ", t, flags=re.IGNORECASE)

    # 2. English My [Noun] (e.g. My Opinion, My Story, My Regret)
    t = re.sub(r"^my\s+([a-zA-Z]+)", lambda m: f"{subject}'s {m.group(1)}" if speaker else f"The {m.group(1)}", t, flags=re.IGNORECASE)

    # 3. English First-person action verbs (e.g. I Tried, I Discovered, I Built)
    t = re.sub(r"^i\s+(tried|found|made|discovered|bought|quit|lost|learned|realized|spent|built|saw|went|started|joined|left|hate|love)\b", rf"{subject} \1", t, flags=re.IGNORECASE)
    t = re.sub(r"^i\s+was\b", f"{subject} Was", t, flags=re.IGNORECASE)
    t = re.sub(r"^i\s+am\b", f"{subject} Is", t, flags=re.IGNORECASE)
    t = re.sub(r"^i\s+have\b", f"{subject} Has", t, flags=re.IGNORECASE)
    t = re.sub(r"^i\s+had\b", f"{subject} Had", t, flags=re.IGNORECASE)
    t = re.sub(r"^i\s+got\b", f"{subject} Got", t, flags=re.IGNORECASE)
    t = re.sub(r"^i\s+think\b", f"{subject} Thinks", t, flags=re.IGNORECASE)
    t = re.sub(r"^i\s+believe\b", f"{subject} Believes", t, flags=re.IGNORECASE)

    # 4. Indonesian / Malay first-person replacements (Saya, Aku, Gue, Gw)
    indo_subject = speaker if speaker else "Host"
    t = re.sub(r"^(kenapa|mengapa)\s+(saya|aku|gue|gw)\s+", rf"\1 {indo_subject} ", t, flags=re.IGNORECASE)
    t = re.sub(r"^(cara|bagaimana)\s+(saya|aku|gue|gw)\s+", rf"Cara {indo_subject} ", t, flags=re.IGNORECASE)
    t = re.sub(r"^(alasan)\s+(saya|aku|gue|gw)\s+", rf"Alasan {indo_subject} ", t, flags=re.IGNORECASE)
    t = re.sub(r"^(saya|aku|gue|gw)\s+(mencoba|menemukan|membuat|yakin|berpikir|menyesal|kehilangan|belajar|mulai|berhenti)\b", rf"{indo_subject} \2", t, flags=re.IGNORECASE)
    t = re.sub(r"^(pendapat|opini)\s+(saya|aku|gue|gw)\b", rf"Opini {indo_subject}", t, flags=re.IGNORECASE)

    return t.strip()


def is_safe_remote_url(url: str, allowed_domains: Optional[set] = None) -> bool:
    """
    Validates if a URL is safe to fetch remotely, preventing SSRF attacks to
    local loopback, private IP subnets, or cloud metadata services.
    """
    if not url:
        return False
    u = url.strip()
    try:
        import ipaddress
        import urllib.parse

        parsed = urllib.parse.urlsplit(u)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower().strip()
        if not hostname:
            return False

        # Block localhost / link-local / loopback hostnames
        if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "metadata.google.internal"):
            return False

        # Check if hostname is an IP address and verify if private/reserved
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        except ValueError:
            # Not an IP literal, it's a domain name
            pass

        if allowed_domains:
            if not any(hostname == d or hostname.endswith("." + d) for d in allowed_domains):
                return False

        return True
    except Exception:
        return False

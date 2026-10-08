"""Comprehensive language registry for Cheat Clip Pro.

Provides a large, curated list of languages (code -> human readable name) so the
UI can offer "all languages" for transcript translation and clip-title/output
translation, and a resolver that maps a code (or a raw name) to the display name
used inside AI prompts.
"""

from typing import Dict, Optional

# Ordered roughly by global usage; codes follow BCP-47/ISO-639-1 where possible.
SUPPORTED_LANGUAGES: Dict[str, str] = {
    "auto": "Auto (same as video)",
    "id": "Indonesian (Bahasa Indonesia)",
    "en": "English",
    "es": "Spanish (Español)",
    "pt": "Portuguese (Português)",
    "fr": "French (Français)",
    "de": "German (Deutsch)",
    "it": "Italian (Italiano)",
    "nl": "Dutch (Nederlands)",
    "ru": "Russian (Русский)",
    "uk": "Ukrainian (Українська)",
    "pl": "Polish (Polski)",
    "ro": "Romanian (Română)",
    "tr": "Turkish (Türkçe)",
    "ar": "Arabic (العربية)",
    "fa": "Persian (فارسی)",
    "he": "Hebrew (עברית)",
    "hi": "Hindi (हिन्दी)",
    "bn": "Bengali (বাংলা)",
    "ur": "Urdu (اردو)",
    "pa": "Punjabi (ਪੰਜਾਬੀ)",
    "gu": "Gujarati (ગુજરાતી)",
    "mr": "Marathi (मराठी)",
    "ta": "Tamil (தமிழ்)",
    "te": "Telugu (తెలుగు)",
    "kn": "Kannada (ಕನ್ನಡ)",
    "ml": "Malayalam (മലയാളം)",
    "si": "Sinhala (සිංහල)",
    "ne": "Nepali (नेपाली)",
    "zh": "Chinese (Simplified) (中文 简体)",
    "zh-TW": "Chinese (Traditional) (中文 繁體)",
    "ja": "Japanese (日本語)",
    "ko": "Korean (한국어)",
    "th": "Thai (ไทย)",
    "vi": "Vietnamese (Tiếng Việt)",
    "ms": "Malay (Bahasa Melayu)",
    "tl": "Filipino (Tagalog)",
    "jv": "Javanese (Basa Jawa)",
    "su": "Sundanese (Basa Sunda)",
    "my": "Burmese (မြန်မာ)",
    "km": "Khmer (ខ្មែរ)",
    "lo": "Lao (ລາວ)",
    "mn": "Mongolian (Монгол)",
    "kk": "Kazakh (Қазақ)",
    "uz": "Uzbek (Oʻzbek)",
    "ky": "Kyrgyz (Кыргызча)",
    "tg": "Tajik (Тоҷикӣ)",
    "tk": "Turkmen (Türkmençe)",
    "az": "Azerbaijani (Azərbaycan)",
    "hy": "Armenian (Հայերեն)",
    "ka": "Georgian (ქართული)",
    "sw": "Swahili (Kiswahili)",
    "am": "Amharic (አማርኛ)",
    "ha": "Hausa",
    "yo": "Yoruba (Yorùbá)",
    "ig": "Igbo",
    "zu": "Zulu (isiZulu)",
    "xh": "Xhosa (isiXhosa)",
    "st": "Sesotho",
    "sn": "Shona",
    "rw": "Kinyarwanda",
    "so": "Somali (Soomaali)",
    "mg": "Malagasy",
    "af": "Afrikaans",
    "el": "Greek (Ελληνικά)",
    "cs": "Czech (Čeština)",
    "sk": "Slovak (Slovenčina)",
    "hu": "Hungarian (Magyar)",
    "bg": "Bulgarian (Български)",
    "hr": "Croatian (Hrvatski)",
    "sr": "Serbian (Српски)",
    "bs": "Bosnian (Bosanski)",
    "sl": "Slovenian (Slovenščina)",
    "mk": "Macedonian (Македонски)",
    "sq": "Albanian (Shqip)",
    "lt": "Lithuanian (Lietuvių)",
    "lv": "Latvian (Latviešu)",
    "et": "Estonian (Eesti)",
    "fi": "Finnish (Suomi)",
    "sv": "Swedish (Svenska)",
    "no": "Norwegian (Norsk)",
    "da": "Danish (Dansk)",
    "is": "Icelandic (Íslenska)",
    "ga": "Irish (Gaeilge)",
    "gd": "Scots Gaelic (Gàidhlig)",
    "cy": "Welsh (Cymraeg)",
    "eu": "Basque (Euskara)",
    "ca": "Catalan (Català)",
    "gl": "Galician (Galego)",
    "mt": "Maltese (Malti)",
    "lb": "Luxembourgish (Lëtzebuergesch)",
    "fy": "Frisian (Frysk)",
    "eo": "Esperanto",
    "la": "Latin (Latina)",
    "yi": "Yiddish (ייִדיש)",
    "ku": "Kurdish (Kurdî)",
    "ps": "Pashto (پښتو)",
    "sd": "Sindhi (سنڌي)",
    "or": "Odia (ଓଡ଼ିଆ)",
    "as": "Assamese (অসমীয়া)",
    "ceb": "Cebuano",
    "haw": "Hawaiian (ʻŌlelo Hawaiʻi)",
    "mi": "Maori (Te Reo Māori)",
    "sm": "Samoan (Gagana Samoa)",
    "to": "Tongan (Lea Fakatonga)",
    "fj": "Fijian (Na Vosa Vakaviti)",
    "ny": "Chichewa",
    "co": "Corsican (Corsu)",
    "ht": "Haitian Creole (Kreyòl Ayisyen)",
    "hmn": "Hmong",
    "be": "Belarusian (Беларуская)",
    "tt": "Tatar (Татарча)",
    "ug": "Uyghur (ئۇيغۇرچە)",
    "bo": "Tibetan (བོད་ཡིག)",
    "ti": "Tigrinya (ትግርኛ)",
    "om": "Oromo (Afaan Oromoo)",
    "qu": "Quechua (Runasimi)",
    "ay": "Aymara",
    "gn": "Guarani (Avañe'ẽ)",
}


def resolve_language_name(code_or_name: Optional[str]) -> str:
    """Resolve a language code (or free-form name) to a display name for prompts.

    Returns an empty string when the caller explicitly asked for 'auto' (meaning
    "keep the video's own language") so callers can skip translation entirely.
    """
    if not code_or_name:
        return ""
    value = str(code_or_name).strip()
    if not value:
        return ""
    low = value.lower()
    if low in ("auto", "none", "original", "default", "video"):
        return ""
    # Direct code match (case-insensitive for the canonical keys)
    for code, name in SUPPORTED_LANGUAGES.items():
        if code.lower() == low:
            return name
    # Exact / partial name match (lets the UI send a raw name too)
    for name in SUPPORTED_LANGUAGES.values():
        if name.lower() == low:
            return name
    # Unknown code/name: pass it through verbatim so the model still understands.
    return value

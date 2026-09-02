import re
import unicodedata
from collections import Counter

STOPWORDS = {
    "a", "ao", "aos", "as", "com", "como", "da", "das", "de", "do", "dos",
    "e", "ela", "ele", "em", "essa", "esse", "eu", "foi", "mais", "mas", "na",
    "nas", "no", "nos", "o", "os", "para", "por", "que", "se", "sem", "um", "uma",
}


def slug_tag(word: str) -> str:
    normalized = unicodedata.normalize("NFKD", word).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]", "", normalized).lower()


def suggest_metadata(transcript: str) -> tuple[str, list[str]]:
    clean = " ".join(transcript.split()).strip()
    if not clean:
        return "Confira este momento da live!", ["#viral", "#cortes", "#aovivo"]
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    description = sentences[0][:157].rstrip() + ("..." if len(sentences[0]) > 157 else "")
    words = [slug_tag(w) for w in re.findall(r"[\wÀ-ÿ]{4,}", clean.lower())]
    keywords = [word for word, _ in Counter(w for w in words if w and w not in STOPWORDS).most_common(5)]
    hashtags = ["#viral", "#cortes"] + [f"#{word}" for word in keywords]
    return description, list(dict.fromkeys(hashtags))


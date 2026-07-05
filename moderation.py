# Simple profanity filter for English and Russian words.
# Extend the WORDS list as needed.

PROFANE_WORDS = {
    "en": {"badword", "fuck", "shit"},
    "ru": {"плохоеcлово", "хер", "ебаный"},
}


def moderate_message(content: str) -> str:
    """Replace profane words with asterisks."""
    words = content.split()
    cleaned = []
    for w in words:
        low = w.lower()
        if low in PROFANE_WORDS["en"] or low in PROFANE_WORDS["ru"]:
            cleaned.append("*" * len(w))
        else:
            cleaned.append(w)
    return " ".join(cleaned)
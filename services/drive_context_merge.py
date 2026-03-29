"""Pure helpers for merging Google Drive context-folder docs into a bounded prompt string.

Kept dependency-free so unit tests do not import db_ops or Drive clients.
"""


def merge_context_folder_documents(named_texts, *, max_total_chars, max_per_file_chars):
    """Merge context-folder docs with deterministic order (caller sorts) and hard caps.

    named_texts: iterable of (filename, full_text).
    Returns (merged_string, num_blocks_included).
    """
    parts = []
    total_chars = 0
    for name, full in named_texts:
        text = (full or "").strip()
        if not text or len(text) < 50:
            continue
        if total_chars >= max_total_chars:
            break
        header = f"--- {name} ---\n"
        sep_len = 2 if parts else 0
        budget = max_total_chars - total_chars - sep_len - len(header) - 24
        if budget < 50:
            break
        per_cap = min(max_per_file_chars, budget)
        take = min(len(text), per_cap)
        if take < 50:
            continue
        chunk = text[:take]
        block = header + chunk
        if len(text) > take:
            block += "\n...(truncated)"
        if total_chars + sep_len + len(block) > max_total_chars:
            room = max_total_chars - total_chars - sep_len - len(header) - 20
            if room < 50:
                break
            take2 = min(room, len(text), max_per_file_chars)
            if take2 < 50:
                break
            block = header + text[:take2] + "\n...(truncated)"
            parts.append(block)
            total_chars += sep_len + len(block)
            break
        parts.append(block)
        total_chars += sep_len + len(block)
    out = "\n\n".join(parts) if parts else ""
    return out, len(parts)

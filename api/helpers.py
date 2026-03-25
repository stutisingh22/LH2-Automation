def research_notes_from_company(comp: dict) -> list:
    if not comp:
        return []
    extra = comp.get("extra_data") or {}
    rn = extra.get("research_notes") or []
    if not rn and (extra.get("research_full") or "").strip():
        return [{"title": "Research Document", "content": extra.get("research_full")}]
    return rn if isinstance(rn, list) else []


def is_placeholder_contact(ct: dict | None, comp: dict | None) -> bool:
    if not ct:
        return True
    email = (ct.get("email") or "").strip()
    linkedin = (ct.get("linkedin") or "").strip()
    name = (ct.get("name") or "").strip()
    domain_like = not name or name in ("—", "?") or "@" in name or ".com" in name.lower()
    has_identity = email or linkedin or (name and not domain_like)
    return not has_identity

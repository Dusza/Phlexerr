"""Protection rules: keep media safe from deletion by actor, director, studio, collection or title keyword.

Matching is case-insensitive and accent-insensitive. `*` matches any run of characters and `?` matches one character.
Actor / director / studio / collection patterns must match the whole name (use `Tom*` for "starts with Tom");
a title pattern with no wildcard is a keyword that matches anywhere in the title.
"""
import functools
import re
import time
import unicodedata
import uuid

TYPES = {
    "actor": "Actor",
    "director": "Director",
    "studio": "Studio",
    "collection": "Collection",
    "title": "Title keyword",
    "genre": "Genre",
    "tag": "Tag / label",
    "library": "Library",
}
MAX_RULES = 500


@functools.lru_cache(maxsize=200000)
def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.casefold().split())


def _has_wild(p):
    return "*" in p or "?" in p


def label(rule):
    extra = f" (top {rule['top']} billed)" if rule.get("top") else ""
    return f"{TYPES.get(rule['type'], rule['type'])}: {rule['pattern']}{extra}"


def validate(d):
    """Return (clean_rule_fields, error). Rejects anything that could silently protect (or miss) everything."""
    t = d.get("type")
    if t not in TYPES:
        return None, "unknown rule type"
    pattern = " ".join(str(d.get("pattern", "")).split())
    if not pattern:
        return None, "enter a name or keyword"
    if len(pattern) > 200:
        return None, "pattern is too long"
    if not re.search(r"[^*?\s]", pattern):
        return None, "that pattern would match everything - include at least one real character"
    top = d.get("top")
    if top in ("", None) or t != "actor":
        top = None
    else:
        try:
            top = int(top)
        except (TypeError, ValueError):
            return None, "'top billed' must be a number"
        if not 1 <= top <= 50:
            return None, "'top billed' must be between 1 and 50"
    note = str(d.get("note", "")).strip()[:200]
    return {"type": t, "pattern": pattern, "top": top, "note": note}, None


def new_rule(fields):
    return {"id": uuid.uuid4().hex[:12], "enabled": True, "created": int(time.time()), **fields}


def _regex(pattern):
    return "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in norm(pattern))


def matcher(rule):
    """Return a function item -> bool for this rule."""
    t, p = rule["type"], norm(rule["pattern"])
    if t == "title" and not _has_wild(p):
        rx = re.compile(re.escape(p))
        test = lambda s: bool(rx.search(s))          # keyword anywhere in the title
    else:
        rx = re.compile(_regex(rule["pattern"]) + r"\Z")
        test = lambda s: bool(rx.match(s))           # whole-name match, wildcards allowed
    top = rule.get("top")

    if t == "title":
        return lambda it: test(norm(it.get("title")))
    if t == "studio":
        return lambda it: bool(it.get("studio")) and test(norm(it["studio"]))
    if t == "collection":
        # "Alien" also protects Plex/Radarr's "Alien Collection" when no wildcard is used
        ctest = test if _has_wild(p) else (lambda s: s == p or s == p + " collection")
        return lambda it: any(ctest(norm(c)) for c in it.get("collections") or [])
    if t == "director":
        return lambda it: any(test(norm(n)) for n in it.get("directors") or [])
    if t == "genre":
        return lambda it: any(test(norm(n)) for n in it.get("genres") or [])
    if t == "tag":
        return lambda it: any(test(norm(n)) for n in it.get("labels") or [])
    if t == "library":
        return lambda it: bool(it.get("library")) and test(norm(it["library"]))
    if t == "actor":
        def actor(it):
            names = it.get("actors") or []
            return any(test(norm(n)) for n in (names[:top] if top else names))
        return actor
    return lambda it: False


def name_test(pattern):
    """Whole-name, case/accent-insensitive test with * and ? wildcards: returns f(str) -> bool."""
    rx = re.compile(_regex(pattern) + r"\Z")
    return lambda s: bool(rx.match(norm(s)))


def protection_map(items, rules, keep_ids):
    """item id -> list of human-readable reasons it is protected (empty/absent = not protected)."""
    active = [(label(r), matcher(r)) for r in rules if r.get("enabled", True)]
    out = {}
    for it in items:
        reasons = []
        if it.get("keep_id") in keep_ids:
            reasons.append("Marked Keep")
        reasons += [lab for lab, m in active if m(it)]
        if reasons:
            out[it["id"]] = reasons
    return out

"""Input and output guardrails for the scenario planner (Day 4).

Runs before any LLM request, so unsafe input never leaves the machine and
costs no quota. Detection is pattern-based and deliberately narrow: it
targets clear signals (instruction-override phrases, SQL statements, real
looking contact/card numbers, API keys) and is checked against the core
scenario set for false positives. It is a guardrail, not a guarantee.

Output side: free text the model returns (assumptions, questions) is
cleaned, capped and redacted before it reaches the plan, because those
strings are shown to users later.
"""

from __future__ import annotations

import re
import unicodedata

from qaseed.llm_client import public_text

MAX_FREE_TEXT_ITEMS = 10
MAX_FREE_TEXT_CHARS = 300

# Instruction-override and role-play phrases typical of prompt injection.
_INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(instructions?|rules|prompts?|guidelines|schema)\b",
    r"\b(system|developer|hidden)\s+(prompt|message|instructions?)\b",
    r"\byou\s+are\s+now\b",
    r"\b(reveal|print|show|repeat|output)\s+(your|the)\s+(instructions?|prompt|rules|api\s*key|key)\b",
    r"\b(admin|developer|god|jailbreak|dan)\s+mode\b",
    r"(^|[\s\"'])(system|assistant)\s*:",
    r"</?\s*(system|instructions?|scenario)\s*>",
]
# SQL statements (not words): QASeed never executes SQL from text, but a
# scenario carrying a statement is either an attack or a misuse.
_SQL_PATTERNS = [
    r"\b(drop|truncate|alter)\s+(table|database|schema)\b",
    r"\bdelete\s+from\b",
    r"\binsert\s+into\b",
    r"\bupdate\s+\w+\s+set\b",
    r"\bunion\s+(all\s+)?select\b",
    r";\s*--",
    r"'\s*(or|and)\s+'?\d+'?\s*=\s*'?\d+",
]
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
# Reserved for documentation/testing (RFC 2606 / 6761): clearly synthetic.
_SAFE_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
_SAFE_EMAIL_TLDS = (".test", ".example", ".invalid", ".localhost")
# A long digit run with phone/card separators. The boundaries skip numbers
# written with thousands separators ("1,000,000") but still allow a number
# that ends a clause ("+971 50 123 4567, plus ...").
_LONG_NUMBER_RE = re.compile(r"(?<!\w)(?<!\d[.,])\+?\d[\d\s().-]{7,}\d(?!\w)(?![.,]\d)")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_SECRET_RES = [
    re.compile(r"AIza[0-9A-Za-z_\-]{10,}"),                   # Google API key
    re.compile(r"\bsk-(proj-|ant-)?[A-Za-z0-9_\-]{16,}"),      # "sk-" style provider keys
    re.compile(r"\b(ghp|gho|github_pat)_[A-Za-z0-9_]{20,}"),   # GitHub tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                       # AWS access key id
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(password|passwd|pwd|secret|token)\s*[:=]\s*\S{6,}", re.IGNORECASE),
]


def clean_text(text: str) -> str:
    """Drop control/format characters (incl. zero-width) and collapse whitespace."""
    kept = "".join(ch if ch in "\t\n" or unicodedata.category(ch)[0] != "C" else " " for ch in text or "")
    return " ".join(kept.split())


def check_scenario(text: str) -> list[tuple[str, str]]:
    """Return [(issue_code, message)] for unsafe input. Empty means no finding.

    Messages never repeat the matched secret or personal data.
    """
    findings: list[tuple[str, str]] = []
    lowered = text.lower()
    if any(rx.search(text) for rx in _SECRET_RES):
        findings.append(("secret_detected", "The scenario contains something that looks like an API key, token or password. Remove it; QASeed never needs credentials in a scenario."))
    pii = _pii_kinds(text)
    if pii:
        findings.append(("pii_detected", f"The scenario contains real-looking personal data ({', '.join(pii)}). Describe the records instead; QASeed creates synthetic values itself and does not send personal data to the model."))
    if any(re.search(p, lowered) for p in _INJECTION_PATTERNS):
        findings.append(("unsafe_input", "The scenario contains instructions aimed at the planner (for example 'ignore previous instructions' or a request for its prompt). Describe only the test data you need."))
    if any(re.search(p, lowered) for p in _SQL_PATTERNS):
        findings.append(("unsafe_input", "The scenario contains a SQL statement. QASeed plans data from a plain-English description and never runs SQL from input."))
    return findings


def redact_sensitive(text: str) -> str:
    """Replace secrets, emails and long numbers so a rejected scenario can be shown or logged."""
    for rx in _SECRET_RES:
        text = rx.sub("[redacted]", text)
    text = _EMAIL_RE.sub("[email redacted]", text)
    return _LONG_NUMBER_RE.sub(lambda m: "[number redacted]" if len(re.sub(r"\D", "", m.group(0))) >= 9 else m.group(0), text)


def clean_free_text(items: list[str], secret: str = "") -> list[str]:
    """Clean, redact and cap model-written strings before they enter a plan."""
    cleaned = []
    for item in items:
        original = clean_text(item)
        text = public_text(original, secret)  # redacts first, then shortens
        if not text:
            continue
        if len(original) > MAX_FREE_TEXT_CHARS or len(text) > MAX_FREE_TEXT_CHARS:
            text = text[: MAX_FREE_TEXT_CHARS - 1] + "…"
        cleaned.append(text)
    if len(cleaned) > MAX_FREE_TEXT_ITEMS:
        cleaned = cleaned[:MAX_FREE_TEXT_ITEMS] + [f"({len(cleaned) - MAX_FREE_TEXT_ITEMS} more items omitted)"]
    return cleaned


def _pii_kinds(text: str) -> list[str]:
    kinds = []
    for match in _EMAIL_RE.finditer(text):
        domain = match.group(0).rsplit("@", 1)[1].lower()
        if domain not in _SAFE_EMAIL_DOMAINS and not domain.endswith(_SAFE_EMAIL_TLDS):
            kinds.append("email address")
            break
    numbers = [re.sub(r"\D", "", m.group(0)) for m in _LONG_NUMBER_RE.finditer(text)]
    if any(13 <= len(n) <= 19 and _luhn(n) for n in numbers):
        kinds.append("card number")
    elif any(len(n) >= 9 for n in numbers):
        kinds.append("phone or ID number")
    if _SSN_RE.search(text) and "phone or ID number" not in kinds:
        kinds.append("phone or ID number")
    if _IBAN_RE.search(text):
        kinds.append("bank account number")
    return kinds


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0

"""
Negotiation Agent — Contract frozen in AGENTS.md section 2 (revised Step 0).

Responsibility: run one negotiation thread between a seller agent and one buyer
agent — separate LLM contexts, each holding only its own private constraints —
and return an accept/reject/counter outcome whose terms passed the validators.

Architecture (NEXT_STEPS.md §1):
- LLM proposes moves via validated JSON (not native tool calling).
- Validator checks every move (move-legal) and final acceptance (deal-legal).
- Bounced moves return to author with reason; 2 invalid in a row or timeout
  → deterministic fallback (visible fallback event).
- Hidden info: each agent sees only its own constraints + public history.
- Leak / number-mismatch / false leverage claims are bounced.

Legacy deterministic engine (midpoint + transcript paraphrase) is preserved as
fallback and for backward-compat with tests that call negotiate() without the
new contract_months / emit fields.
"""

from __future__ import annotations

import os
import re
import json
import time
import datetime
import logging
import random
import pathlib
import threading
from typing import Any, Dict, List, Optional, Tuple, Callable

from agents.validation import number

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_LOGISTICS_COST_PER_TONNE_USD: float = 8.0
DEFAULT_HANDLING_COST_PER_TONNE_USD: float = 2.0
DEFAULT_PROCESSING_COST_PER_TONNE_USD: float = 1.5

CONTRACT_TERM_MONTHS: int = 12
MAX_ROUNDS = 6
MAX_INVALID_RETRIES = 2
LLM_TIMEOUT_S = 20.0

PROVIDER_URLS: Dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openrouter": "https://openrouter.ai/api/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "openai": "https://api.openai.com/v1",
}

# pacing per bucket (provider or provider:model) + RPD tracking
_PACING_LOCK = threading.Lock()
_NEXT_SLOT: Dict[str, float] = {}
_RPD_COUNTS: Dict[str, int] = {}
_RPD_DATE: str = ""  # UTC date of current counts

# cross-process quota persistence (runs/.quota.json) — R2-4
_QUOTA_LOCK = threading.Lock()

# provider aliases: <base>_<n> (e.g. openrouter_2, gemini_3) reuse the base
# provider's URL/quirks/limit-shape but get their own key, bucket and counters — R2-6
_ALIAS_RE = re.compile(r"^([a-z]+)_(\d+)$")

# ---------------------------------------------------------------------------
# Legacy validators (kept for backward compat)
# ---------------------------------------------------------------------------

def validate_proposal(
    price_per_tonne_usd: Optional[float],
    seller_min: float,
    buyer_max: float,
) -> Tuple[bool, str]:
    try:
        for value, name in ((price_per_tonne_usd, 'price'), (seller_min, 'seller floor'), (buyer_max, 'buyer ceiling')):
            number(value, name)
    except ValueError as exc:
        return False, str(exc)
    eps = 1e-9
    if price_per_tonne_usd + eps < seller_min:
        return False, f"price {price_per_tonne_usd} below seller min {seller_min}"
    if price_per_tonne_usd - eps > buyer_max:
        return False, f"price {price_per_tonne_usd} above buyer max {buyer_max}"
    return True, "within bounds"


def validate_quantity(
    quantity_tonnes: Optional[float],
    available_quantity_tonnes: float,
    annual_demand_tonnes: float,
) -> Tuple[bool, str]:
    try:
        for value, name in ((quantity_tonnes, 'quantity'), (available_quantity_tonnes, 'availability'), (annual_demand_tonnes, 'demand')):
            number(value, name)
    except ValueError as exc:
        return False, str(exc)
    if quantity_tonnes < 0:
        return False, "quantity negative"
    if quantity_tonnes - 1e-9 > available_quantity_tonnes:
        return False, "quantity exceeds seller availability"
    if quantity_tonnes - 1e-9 > annual_demand_tonnes:
        return False, "quantity exceeds buyer demand"
    return True, "quantity valid"


def compute_margin_per_tonne(
    price_per_tonne_usd: float,
    logistics_cost_per_tonne_usd: float = DEFAULT_LOGISTICS_COST_PER_TONNE_USD,
    handling_cost_per_tonne_usd: float = DEFAULT_HANDLING_COST_PER_TONNE_USD,
    processing_cost_per_tonne_usd: float = DEFAULT_PROCESSING_COST_PER_TONNE_USD,
) -> float:
    for value, name in ((price_per_tonne_usd, 'price'), (logistics_cost_per_tonne_usd, 'logistics'),
                        (handling_cost_per_tonne_usd, 'handling'), (processing_cost_per_tonne_usd, 'processing')):
        number(value, name)
    return (
        price_per_tonne_usd
        - logistics_cost_per_tonne_usd
        - handling_cost_per_tonne_usd
        - processing_cost_per_tonne_usd
    )

# ---------------------------------------------------------------------------
# Legacy deterministic transcript (preserved for fallback / tests)
# ---------------------------------------------------------------------------

def _deterministic_transcript(
    *,
    seller_min: float,
    seller_preferred: float,
    buyer_max: float,
    logistics_cost: float,
    price: Optional[float],
    quantity: int,
    status: str,
    margin: Optional[float],
    buyer_id: str,
    batna_note: Optional[str] = None,
) -> List[Dict[str, str]]:
    batna_suffix = f" {batna_note}" if batna_note else ""
    if status == "rejected":
        return [
            {
                "speaker": "seller_agent",
                "message": (
                    f"Seller opening: we can supply LD slag at ${seller_preferred:.2f}/t "
                    f"(minimum acceptable ${seller_min:.2f}/t, quantity {quantity}t, "
                    f"logistics ~${logistics_cost:.2f}/t).{batna_suffix}"
                ),
            },
            {
                "speaker": "buyer_agent",
                "message": (
                    f"Buyer {buyer_id}: our ceiling is ${buyer_max:.2f}/t for this grade "
                    f"(proposed quantity {quantity}t). These terms do not yield a viable deal."
                ),
            },
            {
                "speaker": "seller_agent",
                "message": (
                    "Seller: deal cannot proceed at current price and quantity constraints. Rejected."
                ),
            },
        ]
    if status == "countered":
        return [
            {
                "speaker": "seller_agent",
                "message": (
                    f"Seller opening: offering LD slag at ${seller_preferred:.2f}/t "
                    f"(floor ${seller_min:.2f}/t). Quantity {quantity}t, logistics "
                    f"${logistics_cost:.2f}/t.{batna_suffix}"
                ),
            },
            {
                "speaker": "buyer_agent",
                "message": (
                    f"Buyer {buyer_id}: we can stretch to ${buyer_max:.2f}/t max. "
                    f"We are reviewing the proposed terms against our budget."
                ),
            },
            {
                "speaker": "seller_agent",
                "message": (
                    f"Seller counter: we can meet at ${price:.2f}/t "
                    f"(margin ${margin:.2f}/t after logistics/handling/processing). "
                    f"Quantity {quantity}t, term {CONTRACT_TERM_MONTHS} months. "
                    f"Please confirm or counter."
                ),
            },
            {
                "speaker": "buyer_agent",
                "message": (
                    f"Buyer {buyer_id}: ${price:.2f}/t needs internal "
                    f"approval at this margin. Countered, pending confirmation."
                ),
            },
        ]
    return [
        {
            "speaker": "seller_agent",
            "message": (
                f"Seller opening: LD slag available, ${seller_preferred:.2f}/t preferred "
                f"(floor ${seller_min:.2f}/t), logistics ${logistics_cost:.2f}/t, "
                f"quantity {quantity}t.{batna_suffix}"
            ),
        },
        {
            "speaker": "buyer_agent",
            "message": (
                f"Buyer {buyer_id}: we accept the quality; our ceiling is "
                f"${buyer_max:.2f}/t for {quantity}t annual demand. "
                f"Can we settle near ${price:.2f}/t?"
            ),
        },
        {
            "speaker": "seller_agent",
            "message": (
                f"Seller: confirmed at ${price:.2f}/t for {quantity}t "
                f"(margin ${margin:.2f}/t net of logistics/handling/processing). "
                f"Term {CONTRACT_TERM_MONTHS} months. Ready to proceed."
            ),
        },
        {
            "speaker": "buyer_agent",
            "message": (
                f"Buyer {buyer_id}: accepted at ${price:.2f}/t, {quantity}t, "
                f"{CONTRACT_TERM_MONTHS} months. Proceed to contract."
            ),
        },
    ]


def _resolve_llm_config() -> Optional[Tuple[Dict[str, Any], str]]:
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        return (
            {"api_key": openrouter_key, "base_url": "https://openrouter.ai/api/v1"},
            os.getenv("NEGOTIATION_LLM_MODEL", "nex-agi/nex-n2.5-mini:free"),
        )
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
    if api_key:
        return {"api_key": api_key}, os.getenv("NEGOTIATION_LLM_MODEL", "gpt-4o-mini")
    return None


def _try_llm_transcript(
    *,
    seller_min: float,
    seller_preferred: float,
    buyer_max: float,
    buyer_id: str,
    logistics_cost: float,
    price: Optional[float],
    quantity: int,
    status: str,
    margin: Optional[float],
    batna_note: Optional[str] = None,
) -> Optional[List[Dict[str, str]]]:
    config = _resolve_llm_config()
    if config is None:
        return None
    client_kwargs, model = config
    try:
        from openai import OpenAI  # type: ignore
        client = OpenAI(**client_kwargs, timeout=20.0, max_retries=0)
        canonical = _deterministic_transcript(
            seller_min=seller_min, seller_preferred=seller_preferred,
            buyer_max=buyer_max, buyer_id=buyer_id, logistics_cost=logistics_cost,
            price=price, quantity=quantity, status=status, margin=margin, batna_note=batna_note)
        values = {}
        def protect(match):
            token = f"{{{{VALUE_{len(values)}}}}}"
            values[token] = match.group()
            return token
        templates = [{**entry, 'message': re.sub(r'\d+(?:\.\d+)?', protect, entry['message'])}
                     for entry in canonical]
        instruction = json.dumps({'status': status, 'transcript': templates})
        resp = client.chat.completions.create(
            model=model,
            temperature=0.7,
            max_tokens=1200,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a negotiation dialogue generator for industrial by-product sales. "
                        'Paraphrase the supplied transcript without changing its meaning or status. '
                        'Preserve all {{VALUE_n}} placeholders in exactly the same order in each message. '
                        'Do not add numbers or number words. Preserve speakers and turn count. '
                        'Return a JSON object with a "transcript" list of {speaker, message} objects.'
                    ),
                },
                {"role": "user", "content": instruction},
            ],
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or ""
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "transcript" in parsed:
            transcript = parsed["transcript"]
        elif isinstance(parsed, list):
            transcript = parsed
        else:
            transcript = next((v for v in parsed.values() if isinstance(v, list)), None)
            if transcript is None:
                return None
        if not isinstance(transcript, list) or len(transcript) != len(templates):
            return None
        cleaned: List[Dict[str, str]] = []
        for entry, template in zip(transcript, templates):
            if not isinstance(entry, dict):
                return None
            speaker = entry.get("speaker", "")
            message = entry.get("message", "")
            if speaker != template['speaker'] or not isinstance(message, str) or not message.strip() or len(message) > 2000:
                return None
            pattern = r'\{\{VALUE_\d+\}\}'
            if re.findall(pattern, message) != re.findall(pattern, template['message']):
                return None
            remaining = re.sub(pattern, '', message)
            if re.search(r'\d|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|hundred|thousand|million|billion)\b', remaining, re.I):
                return None
            cleaned.append({'speaker': speaker, 'message': re.sub(pattern, lambda m: values[m.group()], message)})
        if 2 <= len(cleaned) <= 4:
            return cleaned
        return None
    except Exception as exc:
        logging.getLogger(__name__).warning("LLM transcript unavailable (%s); using local transcript", type(exc).__name__)
        return None


def generate_transcript(
    *,
    seller_min: float,
    seller_preferred: float,
    buyer_max: float,
    buyer_id: str,
    logistics_cost: float,
    price: Optional[float],
    quantity: int,
    status: str,
    margin: Optional[float],
    use_llm: bool = True,
    batna_note: Optional[str] = None,
) -> List[Dict[str, str]]:
    if use_llm:
        llm_result = _try_llm_transcript(
            seller_min=seller_min,
            seller_preferred=seller_preferred,
            buyer_max=buyer_max,
            buyer_id=buyer_id,
            logistics_cost=logistics_cost,
            price=price,
            quantity=quantity,
            status=status,
            margin=margin,
            batna_note=batna_note,
        )
        if llm_result is not None:
            return llm_result
    return _deterministic_transcript(
        seller_min=seller_min,
        seller_preferred=seller_preferred,
        buyer_max=buyer_max,
        logistics_cost=logistics_cost,
        price=price,
        quantity=quantity,
        status=status,
        margin=margin,
        buyer_id=buyer_id,
        batna_note=batna_note,
    )

# ---------------------------------------------------------------------------
# New helpers: chain parsing, pacing, numbers, leak & leverage detection
# ---------------------------------------------------------------------------

def _parse_chain(chain_str: str) -> List[Tuple[str, str]]:
    """Parse 'provider:model' chain, splitting on first colon."""
    result: List[Tuple[str, str]] = []
    if not chain_str:
        return result
    for part in chain_str.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            continue
        provider, model = part.split(":", 1)
        provider = provider.strip().lower()
        model = model.strip()
        if provider and model:
            result.append((provider, model))
    return result


def _parse_limits(raw: Optional[str]) -> tuple[Optional[float], Dict[str, float]]:
    if not raw or not raw.strip():
        return None, {}
    raw = raw.strip()
    if ":" not in raw:
        try:
            v = float(raw)
            return (v if v > 0 else None), {}
        except ValueError:
            return None, {}
    mp: Dict[str, float] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        k, v = part.split(":", 1)
        k = k.strip()
        v = v.strip()
        try:
            fv = float(v)
            if fv > 0:
                mp[k] = fv
        except ValueError:
            continue
    return None, mp


def _base_provider(provider: str) -> str:
    """Resolve a provider alias (openrouter_2, gemini_3, ...) to its base provider.

    An alias uses the base provider's URL/quirks/limit-shape but its own key,
    pacing bucket and RPD counters (R2-6). Non-aliases (and unknown-base names)
    resolve to themselves.
    """
    m = _ALIAS_RE.match(provider)
    if m and m.group(1) in PROVIDER_URLS:
        return m.group(1)
    return provider


def _provider_timeout_s(provider: str) -> float:
    """Per-base-provider call timeout override (`<BASE>_TIMEOUT_S`, e.g.
    NVIDIA_TIMEOUT_S=10) — aliases inherit the base's value, never set their
    own. Defaults to the global LLM_TIMEOUT_S when unset/invalid (R3-4: NVIDIA
    stalls ~40s on some calls, so a shorter per-call timeout lets the chain
    fall through to the next model sooner instead of waiting the full 20s)."""
    base = _base_provider(provider)
    raw = os.getenv(f"{base.upper()}_TIMEOUT_S")
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return LLM_TIMEOUT_S


def _get_limit(provider: str, model: Optional[str], kind: str) -> Optional[float]:
    """kind is 'RPM' or 'RPD'. Returns per-model or provider-wide limit.

    An alias (e.g. openrouter_2) reads its own <ALIAS>_<KIND> env var first;
    if unset, it inherits the base provider's <BASE>_<KIND> *values* (never the
    base's counter/bucket — that stays separate, see _bucket_key).
    """
    base = _base_provider(provider)
    raw = os.getenv(f"{provider.upper()}_{kind}")
    if raw is None or not raw.strip():
        if base != provider:
            raw = os.getenv(f"{base.upper()}_{kind}")
    wide, mp = _parse_limits(raw)
    if base == "openrouter":
        # account-wide for free models
        if wide is not None:
            return wide
        # fallback if someone configured per-model for openrouter (not expected)
        if mp and model and model in mp:
            return mp[model]
        return None
    # gemini / nvidia / openai: per-model if present else provider-wide
    if model and model in mp:
        return mp[model]
    if wide is not None:
        return wide
    return None


def _get_provider_rpm(provider: str, model: Optional[str] = None) -> Optional[float]:
    return _get_limit(provider, model, "RPM")


def _get_provider_rpd(provider: str, model: Optional[str] = None) -> Optional[float]:
    return _get_limit(provider, model, "RPD")


def _bucket_key(provider: str, model: Optional[str]) -> str:
    """Bucket key for pacing/RPD. Keyed on the alias itself (its own counters),
    shaped (account-wide vs per-model) by the base provider — R2-6."""
    base = _base_provider(provider)
    if base == "openrouter":
        return provider
    if base == "gemini":
        return f"{provider}:{model}" if model else provider
    return provider


def _quota_file_path() -> pathlib.Path:
    """runs/.quota.json — overridable via NEGOTIATION_QUOTA_FILE so tests never
    touch the real (gitignored) file shared by live processes."""
    override = os.getenv("NEGOTIATION_QUOTA_FILE")
    if override:
        return pathlib.Path(override)
    return pathlib.Path(__file__).resolve().parents[2] / "runs" / ".quota.json"


def _load_quota() -> Dict[str, Any]:
    path = _quota_file_path()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("rpd", {})
                data.setdefault("exhausted_until", {})
                return data
    except Exception:
        pass
    return {"rpd": {}, "exhausted_until": {}}


def _save_quota(data: Dict[str, Any]) -> None:
    path = _quota_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        pass


def _mark_quota_exhausted(provider: str, until_iso: Optional[str]) -> None:
    """Mark an alias's whole bucket exhausted (account-wide daily limit hit)
    until `until_iso`, persisted so other processes see it too — R2-4."""
    if not until_iso:
        return
    with _QUOTA_LOCK:
        data = _load_quota()
        data.setdefault("exhausted_until", {})[provider] = until_iso
        _save_quota(data)


def _quota_exhausted_until(provider: str) -> Optional[str]:
    with _QUOTA_LOCK:
        return _load_quota().get("exhausted_until", {}).get(provider)


def _is_quota_exhausted_now(provider: str) -> bool:
    until_iso = _quota_exhausted_until(provider)
    if not until_iso:
        return False
    try:
        until_dt = datetime.datetime.fromisoformat(until_iso.replace("Z", "+00:00"))
        return datetime.datetime.now(datetime.timezone.utc) < until_dt
    except Exception:
        return False


def _is_rpd_exhausted(provider: str, model: Optional[str]) -> bool:
    limit = _get_provider_rpd(provider, model)
    if limit is None:
        return False
    # reset per UTC day
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    global _RPD_DATE
    with _PACING_LOCK:
        if _RPD_DATE != today:
            _RPD_COUNTS.clear()
            _RPD_DATE = today
        bucket = _bucket_key(provider, model)
        key = f"{bucket}:{today}"
        in_mem = _RPD_COUNTS.get(key, 0)
    with _QUOTA_LOCK:
        persisted = _load_quota().get("rpd", {}).get(key, 0)
    return max(in_mem, persisted) >= limit


def _record_rpd(provider: str, model: Optional[str]) -> None:
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    global _RPD_DATE
    with _PACING_LOCK:
        if _RPD_DATE != today:
            _RPD_COUNTS.clear()
            _RPD_DATE = today
        bucket = _bucket_key(provider, model)
        key = f"{bucket}:{today}"
        _RPD_COUNTS[key] = _RPD_COUNTS.get(key, 0) + 1
    # persist so separate processes (tests, CLI runs) share RPD counts — R2-4
    with _QUOTA_LOCK:
        data = _load_quota()
        rpd = data.setdefault("rpd", {})
        rpd[key] = rpd.get(key, 0) + 1
        _save_quota(data)


def _pace_provider(provider: str, model: Optional[str] = None) -> None:
    rpm = _get_provider_rpm(provider, model)
    if rpm is None or rpm <= 0:
        return
    bucket = _bucket_key(provider, model)
    # reserve slot under lock, sleep outside
    with _PACING_LOCK:
        now = time.monotonic()
        slot = max(now, _NEXT_SLOT.get(bucket, 0.0))
        _NEXT_SLOT[bucket] = slot + 60.0 / rpm
        wait = slot - now
    if wait > 0:
        time.sleep(wait)


def _provider_api_key(provider: str) -> Optional[str]:
    mapping = {
        "gemini": "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    env_key = mapping.get(provider, f"{provider.upper()}_API_KEY")
    return os.getenv(env_key)


def _resolve_chain_for_role(role: str, buyer_index: Optional[int] = None) -> List[Tuple[str, str]]:
    """
    role: 'seller' or 'buyer'. For buyer, buyer_index 1-based picks BUYER_1_CHAIN etc.
    Falls back to generic chains.
    """
    if role == "seller":
        chain_str = os.getenv("SELLER_LLM_CHAIN", "")
        chain = _parse_chain(chain_str)
        if chain:
            return chain
        # fallback to old single model config
        cfg = _resolve_llm_config()
        if cfg:
            _, model = cfg
            return [("openrouter", model)]
        return []
    else:
        # buyer
        if buyer_index is not None:
            chain_str = os.getenv(f"BUYER_{buyer_index}_LLM_CHAIN", "")
            chain = _parse_chain(chain_str)
            if chain:
                return chain
        # try generic buyer chain
        chain_str = os.getenv("BUYER_LLM_CHAIN", "") or os.getenv("BUYER_1_LLM_CHAIN", "")
        chain = _parse_chain(chain_str)
        if chain:
            return chain
        cfg = _resolve_llm_config()
        if cfg:
            _, model = cfg
            return [("openrouter", model)]
        return []


def _numbers_in_text(text: str) -> List[float]:
    if not isinstance(text, str):
        return []
    raw = re.findall(r"\d[\d,]*(?:\.\d+)?", text)
    out = []
    for n in raw:
        try:
            out.append(float(n.replace(",", "")))
        except ValueError:
            continue
    return out


# --- number-word conversion ---
_WORD_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_WORD_SCALE = {"hundred": 100, "thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000}

_WORD_NUM_RE = re.compile(r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion)(?:[\s-]+(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion))*\b", re.I)

def _parse_word_number_phrase(phrase: str) -> Optional[float]:
    phrase = phrase.lower().replace("-", " ").strip()
    tokens = phrase.split()
    # skip isolated pronoun "one"
    if tokens == ["one"]:
        return None
    total = 0
    current = 0
    for tok in tokens:
        if tok in _WORD_NUM:
            current += _WORD_NUM[tok]
        elif tok in _WORD_SCALE:
            scale = _WORD_SCALE[tok]
            if scale == 100:
                if current == 0:
                    current = 1
                current *= scale
            else:
                if current == 0:
                    current = 1
                current *= scale
                total += current
                current = 0
        else:
            return None
    total += current
    return float(total) if total != 0 or "zero" in tokens else None


def _word_numbers_in_text(text: str) -> List[float]:
    if not isinstance(text, str) or not text:
        return []
    out: List[float] = []
    for m in _WORD_NUM_RE.finditer(text):
        phrase = m.group(0)
        # handle hyphenated month/term vs general - split
        # e.g. "twelve-month" -> regex finds "twelve", value 12
        val = _parse_word_number_phrase(phrase)
        if val is not None:
            out.append(val)
    return out


_NUMBER_WORDS_RE = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion)\b",
    re.I,
)

# leverage detection
_GENERIC_LEVERAGE_RE = re.compile(
    r"\b(other buyers|other buyer|other interest|competing interest|multiple buyers|other parties|alternative buyer|other offer)\b",
    re.I,
)
_BETTER_OFFER_RE = re.compile(
    r"\b(better offer|higher offer|better bid|higher bid|more attractive offer|superior offer|better price elsewhere|higher.*offer)\b",
    re.I,
)


def _has_generic_leverage_claim(msg: str) -> bool:
    return bool(_GENERIC_LEVERAGE_RE.search(msg or ""))


def _has_better_offer_claim(msg: str) -> bool:
    return bool(_BETTER_OFFER_RE.search(msg or ""))


def _history_allowed_values(delivered_offers: Optional[List[Dict[str, Any]]]) -> List[float]:
    """Structured field values (price, quantity, contract_months) of every
    offer already delivered earlier in this thread, by either side — public
    history a message may legitimately quote (R3-1; EVENTS.md "Numbers in
    message", amended 2026-09-12). `delivered_offers` only ever holds
    delivered offers, so a bounced offer never counts, per the amendment."""
    values: List[float] = []
    for o in delivered_offers or []:
        for field in ("price_per_tonne_usd", "quantity_tonnes", "contract_months"):
            v = o.get(field)
            if v is None:
                continue
            try:
                values.append(float(v))
            except (TypeError, ValueError):
                continue
    return values


def _message_states_reservation(
    msg: str,
    reservation_value: Optional[float],
    allowed_values: Optional[List[float]] = None,
    eps: float = 0.01,
) -> bool:
    """A number equal to `reservation_value` is a leak UNLESS that same value
    is also covered by `allowed_values` (the move's own structured fields
    and/or public thread history) — R3-2, EVENTS.md "Own-reservation leak"
    (amended 2026-09-12): coincidence with public history is not a leak."""
    if reservation_value is None or not msg:
        return False
    covered = [float(v) for v in (allowed_values or []) if v is not None]
    for n in _numbers_in_text(msg):
        if abs(n - reservation_value) < eps:
            if any(abs(n - a) < eps for a in covered):
                continue
            return True
    return False


def _message_numbers_match_structured(msg: str, allowed_values: List[float], eps: float = 0.01) -> Tuple[bool, str]:
    """
    Every number in message must match one of allowed_values.
    Callers pass the move's own structured fields (price, quantity,
    contract_months) PLUS `_history_allowed_values()` of the thread so far
    (R3-1) — quoting a price/quantity/term from an offer already delivered
    earlier in this thread, by either side, is grounded in public history,
    not invented (EVENTS.md "Numbers in message", amended 2026-09-12).
    Checks both digit numbers and word numbers (twenty-nine -> 29). Isolated pronoun "one"
    is ignored so "the best one we can do" passes.
    """
    if not isinstance(msg, str) or not msg:
        return True, ""
    digit_nums = _numbers_in_text(msg)
    word_nums = _word_numbers_in_text(msg)
    nums = digit_nums + word_nums
    if not nums:
        return True, ""
    allowed_set = [float(v) for v in allowed_values if v is not None]
    for n in nums:
        matched = any(abs(n - a) < eps for a in allowed_set)
        if not matched:
            return False, f"number {n} in message does not match structured fields {allowed_set}"
    return True, ""

# ---------------------------------------------------------------------------
# New validators: move_legal and deal_legal + leak / leverage
# ---------------------------------------------------------------------------

def _validate_move_legal(
    move: Dict[str, Any],
    role: str,
    seller_constraints: Dict[str, Any],
    buyer: Dict[str, Any],
    effective_seller_min: float,
    logistics_cost: float,
    shared_state: Optional[Dict[str, Any]] = None,
    delivered_offers: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bool, str, str]:
    """
    Returns (ok, reason, gate). gate is move_legal or deal_legal.
    For make_offer: check own bounds only.
    Also checks leak, number mismatch, leverage.

    `delivered_offers` is this thread's offers delivered so far (either side,
    from the caller's `delivered_offers` list) — used to allow a message to
    quote public thread history (R3-1/R3-2, EVENTS.md amended 2026-09-12).
    """
    action = move.get("action")
    msg = move.get("message", "") if isinstance(move.get("message"), str) else ""
    rationale = move.get("rationale", "")
    history_values = _history_allowed_values(delivered_offers)

    # basic action check
    if action not in ("make_offer", "accept_offer", "reject", "request_info"):
        return False, f"unknown action {action!r}", "move_legal"

    if action == "request_info":
        topic = move.get("topic")
        if topic not in ("route_cost", "best_competing_offer"):
            return False, f"unknown info topic {topic!r}", "move_legal"
        if topic == "best_competing_offer" and role != "seller_agent":
            return False, "only seller can request best_competing_offer", "move_legal"
        # no number/leak checks for info_request beyond basic
        return True, "within your bounds", "move_legal"

    if action in ("make_offer", "accept_offer", "reject"):
        if not isinstance(msg, str) or not msg.strip():
            return False, "message is required", "move_legal"
        if len(msg) > 2000:
            return False, "message too long", "move_legal"

    if action == "make_offer":
        # required fields
        for field in ("price_per_tonne_usd", "quantity_tonnes", "contract_months"):
            if field not in move:
                return False, f"missing field {field}", "move_legal"
        try:
            price = float(move["price_per_tonne_usd"])
            qty = float(move["quantity_tonnes"])
            months = int(move["contract_months"])
        except (TypeError, ValueError) as exc:
            return False, f"invalid numeric field: {exc}", "move_legal"
        try:
            number(price, "price")
            number(qty, "quantity")
            number(months, "contract_months")
        except ValueError as exc:
            return False, str(exc), "move_legal"

        # own bounds
        if role == "seller_agent":
            seller_min = effective_seller_min
            avail = float(seller_constraints.get("available_quantity_tonnes", 0))
            c_min = int(seller_constraints.get("contract_months_min", 6))
            c_max = int(seller_constraints.get("contract_months_max", 36))
            if price + 1e-9 < seller_min:
                return False, f"price {price} is below your floor {seller_min}", "move_legal"
            if qty - 1e-9 > avail:
                return False, "quantity exceeds your availability", "move_legal"
            if not (c_min <= months <= c_max):
                return False, f"contract_months {months} is outside your range {c_min}-{c_max}", "move_legal"
            # R3-1: this move's own fields plus public thread history (either
            # side's already-delivered offers) are grounded, not invented —
            # compute before the leak check so R3-2 can exempt the same set.
            # R4-3: the thread's route freight is public to both sides too
            # (EVENTS.md "Numbers in message", amended 2026-09-12) — add it.
            allowed = [price, qty, float(months), float(logistics_cost)] + history_values
            # leak: seller must not state own floor or batna, UNLESS that same
            # number is covered by `allowed` above (R3-2).
            batna = seller_constraints.get("_batna_price")
            for rv, label in [(seller_min, "floor"), (batna, "BATNA")]:
                if rv is not None and _message_states_reservation(msg, float(rv), allowed_values=allowed):
                    return False, f"message states your own {label} value", "move_legal"
        else:  # buyer
            buyer_max = float(buyer["max_acceptable_price_per_tonne_usd"])
            demand = float(buyer.get("annual_demand_tonnes", 0))
            c_min = int(buyer.get("contract_months_min", 6))
            c_max = int(buyer.get("contract_months_max", 24))
            if price - 1e-9 > buyer_max:
                return False, f"price {price} is above your ceiling {buyer_max}", "move_legal"
            if qty - 1e-9 > demand:
                return False, "quantity exceeds your demand", "move_legal"
            if not (c_min <= months <= c_max):
                return False, f"contract_months {months} is outside your range {c_min}-{c_max}", "move_legal"
            # R4-3: route freight is public to both sides — allowed here too.
            allowed = [price, qty, float(months), float(logistics_cost)] + history_values
            for rv in [buyer_max]:
                if _message_states_reservation(msg, float(rv), allowed_values=allowed):
                    return False, "message states your own ceiling value", "move_legal"

        ok, reason = _message_numbers_match_structured(msg, allowed)
        if not ok:
            return False, reason, "move_legal"

        # leverage claim (seller only) — treat None as no other live threads
        if role == "seller_agent":
            effective_shared = shared_state if shared_state is not None else {}
            if _has_better_offer_claim(msg):
                has_better = False
                my_buyer_id = buyer.get("buyer_id")
                for other_id, offers in effective_shared.items():
                    if other_id == my_buyer_id:
                        continue
                    for o in offers:
                        # only a live BUYER bid elsewhere counts as "a better offer" —
                        # the seller's own asks in other threads never qualify (R2-2/EVENTS.md)
                        if o.get("_role") != "buyer_agent" or o.get("_thread_dead"):
                            continue
                        try:
                            op = float(o.get("price_per_tonne_usd", 0))
                            oq = float(o.get("quantity_tonnes", 0))
                            ocost = float(o.get("_logistics_cost", logistics_cost))
                            net = (op - ocost - DEFAULT_HANDLING_COST_PER_TONNE_USD - DEFAULT_PROCESSING_COST_PER_TONNE_USD) * oq
                            cur_net = (price - logistics_cost - DEFAULT_HANDLING_COST_PER_TONNE_USD - DEFAULT_PROCESSING_COST_PER_TONNE_USD) * qty
                            if net > cur_net + 1e-6:
                                has_better = True
                                break
                        except Exception:
                            continue
                    if has_better:
                        break
                if not has_better:
                    other_threads_exist = any(k != my_buyer_id for k in effective_shared.keys())
                    if _has_better_offer_claim(msg):
                        return False, "leverage claim requires a live better offer elsewhere", "move_legal"
                    if _has_generic_leverage_claim(msg) and not other_threads_exist:
                        return False, "leverage claim requires other live threads", "move_legal"
            elif _has_generic_leverage_claim(msg):
                my_buyer_id = buyer.get("buyer_id")
                other_exists = any(k != my_buyer_id for k in effective_shared.keys())
                if not other_exists:
                    return False, "leverage claim requires other live threads", "move_legal"

        return True, "within your bounds", "move_legal"

    if action == "accept_offer":
        offer_id = move.get("offer_id")
        if not isinstance(offer_id, str) or not offer_id:
            return False, "accept_offer requires offer_id", "deal_legal"
        # number / leak checks for accept message
        # For accept, allowed numbers are those of the referenced offer (checked at deal-legal stage)
        # But we still check leak and that numbers match referenced offer or are empty
        # We'll defer full deal_legal to caller who has access to offer store
        # Here just check leak. R4-3: route freight is public too.
        allowed_accept_leak = history_values + [float(logistics_cost)]
        if role == "seller_agent":
            batna = seller_constraints.get("_batna_price")
            for rv in [effective_seller_min, batna]:
                if rv is not None and _message_states_reservation(msg, float(rv), allowed_values=allowed_accept_leak):
                    return False, "message states your own reservation value", "deal_legal"
        else:
            buyer_max = float(buyer["max_acceptable_price_per_tonne_usd"])
            if _message_states_reservation(msg, buyer_max, allowed_values=allowed_accept_leak):
                return False, "message states your own ceiling value", "deal_legal"
        # For accept we allow no numbers or numbers that match referenced offer — validated by caller
        return True, "within your bounds", "deal_legal"

    if action == "reject":
        reason = move.get("reason", "")
        if not isinstance(reason, str) or not reason.strip():
            return False, "reject requires reason", "move_legal"
        # leak check for reject too
        if role == "seller_agent":
            batna = seller_constraints.get("_batna_price")
            for rv in [effective_seller_min, batna]:
                if rv is not None and _message_states_reservation(msg, float(rv), allowed_values=history_values):
                    return False, "message states your own reservation value", "move_legal"
        else:
            buyer_max = float(buyer["max_acceptable_price_per_tonne_usd"])
            if _message_states_reservation(msg, buyer_max, allowed_values=history_values):
                return False, "message states your own ceiling value", "move_legal"
        return True, "within your bounds", "move_legal"

    return False, "unknown action", "move_legal"


def _validate_deal_legal(
    accept_move: Dict[str, Any],
    role: str,
    seller_constraints: Dict[str, Any],
    buyer: Dict[str, Any],
    effective_seller_min: float,
    offer_store: Dict[str, Dict[str, Any]],
    latest_counterparty_offer_id: Optional[str],
    logistics_cost: float = 0.0,
) -> Tuple[bool, str]:
    offer_id = accept_move.get("offer_id")
    if offer_id not in offer_store:
        return False, f"offer_id {offer_id!r} not found"
    if latest_counterparty_offer_id is None or offer_id != latest_counterparty_offer_id:
        return False, f"offer_id {offer_id!r} is not the latest open offer"
    offer = offer_store[offer_id]
    price = float(offer["price_per_tonne_usd"])
    qty = float(offer["quantity_tonnes"])
    months = int(offer["contract_months"])
    buyer_max = float(buyer["max_acceptable_price_per_tonne_usd"])
    avail = float(seller_constraints.get("available_quantity_tonnes", 0))
    demand = float(buyer.get("annual_demand_tonnes", 0))
    s_cmin = int(seller_constraints.get("contract_months_min", 6))
    s_cmax = int(seller_constraints.get("contract_months_max", 36))
    b_cmin = int(buyer.get("contract_months_min", 6))
    b_cmax = int(buyer.get("contract_months_max", 24))
    # Both sides must satisfy every bound. R4-1: the offer being accepted
    # already passed move-legal against its PROPOSER's own bounds when it was
    # made, so the bound most likely to fail here is the ACCEPTING agent's
    # OWN — e.g. a buyer trying to accept_offer a seller offer whose quantity
    # is within the seller's availability but exceeds the buyer's own demand
    # (run-dc3d40f5, Shah's thread). For that case, the reason is actionable
    # and cites only the accepting agent's own constraint, matching
    # move-legal's no-leak rule (AGENTS.md §2) — never the counterparty's
    # bound, since this reason is returned straight to the accepting agent.
    # When the failing bound instead belongs to the COUNTERPARTY (a rarer
    # race, e.g. the dynamic floor rose after the offer was delivered), the
    # reason stays generic with no numbers from either side.
    if price + 1e-9 < effective_seller_min:
        if role == "seller_agent":
            return False, (f"offer price {price} is below your floor {effective_seller_min} — "
                            "counter with make_offer at or above your floor instead of accepting")
        return False, "offer price is below the seller's floor"
    if price - 1e-9 > buyer_max:
        if role == "buyer_agent":
            return False, (f"offer price {price} exceeds your ceiling {buyer_max} — "
                            "counter with make_offer at or below your ceiling instead of accepting")
        return False, "offer price is above the buyer's ceiling"
    if qty - 1e-9 > avail:
        if role == "seller_agent":
            return False, (f"offer quantity {qty} exceeds your availability {avail} — "
                            "counter with make_offer at your quantity instead of accepting")
        return False, "offer quantity exceeds the seller's availability"
    if qty - 1e-9 > demand:
        if role == "buyer_agent":
            return False, (f"offer quantity {qty} exceeds your demand {demand} — "
                            "counter with make_offer at your quantity instead of accepting")
        return False, "offer quantity exceeds the buyer's demand"
    if not (s_cmin <= months <= s_cmax):
        if role == "seller_agent":
            return False, (f"contract_months {months} is outside your range {s_cmin}-{s_cmax} — "
                            "counter with make_offer within your range instead of accepting")
        return False, "contract_months is outside the seller's range"
    if not (b_cmin <= months <= b_cmax):
        if role == "buyer_agent":
            return False, (f"contract_months {months} is outside your range {b_cmin}-{b_cmax} — "
                            "counter with make_offer within your range instead of accepting")
        return False, "contract_months is outside the buyer's range"
    # message numbers must match the accepted offer's structured fields OR a
    # structured field of any offer already delivered earlier in this thread,
    # by either side — R3-1, EVENTS.md "Numbers in message" amended
    # 2026-09-12 ("We accept your $26.00/t -- down from the $28.50 you opened
    # at" quotes public history, not just the offer being accepted). R4-3:
    # the thread's route freight is public to both sides too — allowed here.
    msg = accept_move.get("message", "")
    if isinstance(msg, str) and msg.strip():
        allowed = [price, qty, float(months), float(logistics_cost)] + _history_allowed_values(list(offer_store.values()))
        # also allow no numbers (e.g. "Agreed") — empty set is subset
        nums = _numbers_in_text(msg)
        for n in nums:
            if not any(abs(n - a) < 0.01 for a in allowed):
                return False, f"number {n} in accept message does not match accepted offer"
    return True, "offer satisfies both parties' bounds"

# ---------------------------------------------------------------------------
# LLM calling helpers
# ---------------------------------------------------------------------------

def _extract_rate_limit_reset(exc: Exception) -> Optional[str]:
    """Best-effort parse of X-RateLimit-Reset off an OpenAI-SDK exception's
    response headers. OpenRouter sends a millisecond epoch (not seconds, not an
    HTTP date) — guard defensively and fall back to next UTC midnight."""
    reset_raw = None
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) if resp is not None else None
    if headers is not None:
        try:
            reset_raw = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
        except Exception:
            reset_raw = None
    if reset_raw:
        try:
            val = float(reset_raw)
            if val > 1e11:  # looks like milliseconds, not seconds
                val = val / 1000.0
            dt = datetime.datetime.fromtimestamp(val, tz=datetime.timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")
        except Exception:
            pass
    now = datetime.datetime.now(datetime.timezone.utc)
    next_midnight = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_midnight.isoformat().replace("+00:00", "Z")


def _classify_llm_exception(exc: Exception, provider: str, model: str) -> str:
    """Turn a provider-call exception into a short, non-secret failure reason.

    Never includes the api key (SDK exceptions don't echo the auth header).
    A 429 whose body names the OpenRouter free-tier daily bucket marks that
    alias's whole bucket exhausted (account-wide) until X-RateLimit-Reset — R2-4.
    """
    msg = str(exc)
    status_code = getattr(exc, "status_code", None)
    if "timeout" in type(exc).__name__.lower() or "timed out" in msg.lower():
        return "timeout"
    is_429 = status_code == 429 or "429" in msg or "rate limit" in msg.lower()
    if is_429:
        body = getattr(exc, "body", None)
        body_text = json.dumps(body) if isinstance(body, dict) else (body if isinstance(body, str) else "")
        combined = f"{msg} {body_text}".lower()
        if "free-models-per-day" in combined or "openrouter_free_tier_daily" in combined:
            reset_at = _extract_rate_limit_reset(exc)
            _mark_quota_exhausted(provider, reset_at)
            return f"429 free-models-per-day exhausted for {provider} (resets {reset_at})"
        return f"429 rate limited on {provider}:{model}"
    return f"provider_error {type(exc).__name__}: {msg[:200]}"


def _call_llm_for_move(
    provider: str,
    model: str,
    system_prompt: str,
    history_messages: List[Dict[str, str]],
    temperature: float = 0.6,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Call a single provider/model and return (parsed JSON move, None) on
    success, or (None, reason) on failure. `reason` is None only when a fake
    move is not in play and no failure occurred (i.e. never for this function
    itself — every non-success path sets a reason so callers can report an
    accurate fallback cause instead of always guessing "timeout").
    Handles Gemini reasoning_effort and NVIDIA thinking quirks (by base
    provider, so aliases like gemini_2 get them too). Skips models that have
    hit RPD or whose alias bucket is marked quota-exhausted.
    """
    api_key = _provider_api_key(provider)
    if not api_key:
        return None, "no_api_key"
    base = _base_provider(provider)
    base_url = PROVIDER_URLS.get(base)
    if not base_url:
        return None, "unknown_provider"

    if _is_quota_exhausted_now(provider):
        logging.getLogger(__name__).debug("Skipping %s:%s - quota exhausted", provider, model)
        return None, "quota_exhausted"

    if _is_rpd_exhausted(provider, model):
        logging.getLogger(__name__).debug("Skipping %s:%s - RPD exhausted", provider, model)
        return None, "rpd_exhausted"

    _pace_provider(provider, model)
    _record_rpd(provider, model)

    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        return None, "openai_sdk_missing"

    call_timeout = _provider_timeout_s(provider)
    client_kwargs: Dict[str, Any] = {"api_key": api_key, "base_url": base_url, "timeout": call_timeout, "max_retries": 0}
    client = OpenAI(**client_kwargs)

    extra_body: Dict[str, Any] = {}
    if base == "gemini":
        # low reasoning effort
        eff = os.getenv("GEMINI_REASONING_EFFORT", "low")
        extra_body["reasoning_effort"] = eff
    # build messages - ensure at least one user message (Gemini requires contents)
    messages = [{"role": "system", "content": system_prompt}]
    if history_messages:
        messages.extend(history_messages)
    else:
        messages.append({"role": "user", "content": "Your turn. Return exactly one JSON move as specified in the system prompt."})
    # if last message is not user, add a trailing user prompt to elicit move
    if messages[-1]["role"] != "user":
        messages.append({"role": "user", "content": "Propose your next move now as JSON."})

    kwargs: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": 800,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    if base == "gemini":
        kwargs["extra_body"] = extra_body
    elif base == "nvidia":
        kwargs["extra_body"] = {"chat_template_kwargs": {"thinking": False}}

    try:
        resp = client.chat.completions.create(**kwargs)
        raw = resp.choices[0].message.content or ""
        # try to extract JSON even if wrapped in markdown
        raw = raw.strip()
        # remove code fences
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "action" in parsed:
            return parsed, None
        # sometimes wrapped in {"move": {...}} or {"tool_call": {...}}
        for v in parsed.values() if isinstance(parsed, dict) else []:
            if isinstance(v, dict) and "action" in v:
                return v, None
        return None, "invalid_response"
    except Exception as exc:
        reason = _classify_llm_exception(exc, provider, model)
        logging.getLogger(__name__).debug("LLM call %s:%s failed: %s", provider, model, reason)
        return None, reason


def _get_llm_move(
    role: str,
    system_prompt: str,
    history_for_prompt: List[Dict[str, str]],
    chain: List[Tuple[str, str]],
    buyer_index: Optional[int] = None,
    temperature: float = 0.6,
    fake_moves: Optional[List[Dict[str, Any]]] = None,
    fake_index: Optional[List[int]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """
    Try fake moves first, then provider chains left to right.
    Returns (move, model_string, None) on success, or (None, None, fail_reason)
    on total failure/timeout — fail_reason is the last chain entry's failure
    classification (e.g. "timeout", "429 free-models-per-day exhausted for
    openrouter_2 ...") so the caller can emit an accurate fallback cause (R2-4).
    """
    # fake agent for testing
    if fake_moves is not None and fake_index is not None:
        idx = fake_index[0]
        if idx < len(fake_moves):
            fake_index[0] += 1
            entry = fake_moves[idx]
            # entry can be callable or dict or exception marker
            if isinstance(entry, dict) and entry.get("_raise_timeout"):
                return None, None, "timeout"
            if isinstance(entry, dict):
                return entry, "fake:fake-model", None
            if callable(entry):
                return entry(), "fake:fake-model", None

    last_reason: Optional[str] = None
    for provider, model in chain:
        move, reason = _call_llm_for_move(provider, model, system_prompt, history_for_prompt, temperature)
        if move is not None:
            return move, f"{provider}:{model}", None
        if reason is not None:
            last_reason = reason
    return None, None, last_reason

# ---------------------------------------------------------------------------
# Deterministic fallback offer generation (move-level)
# ---------------------------------------------------------------------------

def _deterministic_fallback_offer(
    role: str,
    seller_constraints: Dict[str, Any],
    buyer: Dict[str, Any],
    effective_seller_min: float,
    logistics_cost: float,
    round_num: int,
    history_offers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Produce a guaranteed-valid make_offer for the given role.
    Used when LLM bounces twice or times out.
    """
    if role == "seller_agent":
        floor = effective_seller_min
        pref = float(seller_constraints.get("preferred_price_per_tonne_usd", floor))
        avail = float(seller_constraints.get("available_quantity_tonnes", 0))
        buyer_demand = float(buyer.get("annual_demand_tonnes", 0))
        qty = min(avail, buyer_demand)
        if qty <= 0:
            qty = min(avail, buyer_demand) if min(avail, buyer_demand) > 0 else 1000
        c_min = int(seller_constraints.get("contract_months_min", 6))
        c_max = int(seller_constraints.get("contract_months_max", 36))
        c_pref = int(seller_constraints.get("preferred_contract_months", 24))
        contract_months = max(c_min, min(c_max, c_pref))
        # concession schedule: start a bit high, shrink
        # fallback should be defensible: hold near last own offer if exists, else use pref
        last_own = next((o for o in reversed(history_offers) if o.get("_role") == "seller_agent"), None)
        if last_own is not None:
            price = float(last_own["price_per_tonne_usd"])
            # clamp to current dynamic floor (live competing offers may have raised it)
            if price + 1e-9 < floor:
                price = floor + 0.5
        else:
            # anchor slightly above preferred but ensure floor
            price = max(floor, pref + 1.0)
            if round_num > 3:
                price = max(floor, price - 0.5 * (round_num - 3))
        price = round(price, 2)
        # keep quantity formatting consistent with structured field so number check passes
        # (e.g. 0.25 must appear as 0.25, not 0)
        if float(qty).is_integer():
            qty_int = int(qty)
            qty_str = str(qty_int)
            qty_val: Any = qty_int
        else:
            qty_str = str(qty)
            qty_val = qty
        # avoid stating reservation value: if price equals floor (within epsilon) nudge it
        if abs(price - floor) < 0.01:
            price = round(floor + 0.5, 2)
        msg = f"Holding at ${price:.2f}/t for {qty_str} t, {contract_months} months. (deterministic fallback)"
        return {
            "action": "make_offer",
            "price_per_tonne_usd": price,
            "quantity_tonnes": qty_val,
            "contract_months": contract_months,
            "message": msg,
            "rationale": "Deterministic fallback: hold last valid offer / defend floor.",
        }
    else:  # buyer
        ceiling = float(buyer["max_acceptable_price_per_tonne_usd"])
        demand = float(buyer.get("annual_demand_tonnes", 0))
        avail = float(seller_constraints.get("available_quantity_tonnes", 0))
        qty = min(avail, demand)
        c_min = int(buyer.get("contract_months_min", 6))
        c_max = int(buyer.get("contract_months_max", 24))
        contract_months = max(c_min, min(c_max, 12))
        last_own = next((o for o in reversed(history_offers) if o.get("_role") == "buyer_agent"), None)
        # R4-2: gradual concession schedule mirroring the seller's, instead of
        # jumping straight to `ceiling - 1.0` on the very first fallback bid.
        # That single jump raised the seller's dynamic floor in OTHER threads
        # (R2-2, correct behavior) above those threads' own ceilings, causing
        # 8 of 11 bounces / 3 of 5 fallbacks in run-dc3d40f5. Formula (doc):
        # no prior bid -> open at round(0.85 x ceiling, 2); otherwise
        # next = last_bid + 0.4 x (ceiling - last_bid), rounded to cents
        # (shrinking steps), capped at ceiling - 0.5 so the bid never states
        # the buyer's own reservation value.
        cap = round(ceiling - 0.5, 2)
        if last_own is None:
            price = round(0.85 * ceiling, 2)
        else:
            last_bid = float(last_own["price_per_tonne_usd"])
            price = round(last_bid + 0.4 * (ceiling - last_bid), 2)
        price = round(max(0.0, min(price, cap)), 2)
        if float(qty).is_integer():
            qty_str = str(int(qty))
            qty_val2: Any = int(qty)
        else:
            qty_str = str(qty)
            qty_val2 = qty
        msg = f"We can do ${price:.2f}/t for {qty_str} t, {contract_months} months."
        return {
            "action": "make_offer",
            "price_per_tonne_usd": price,
            "quantity_tonnes": qty_val2,
            "contract_months": contract_months,
            "message": msg,
            "rationale": "Deterministic fallback buyer offer: gradual concession schedule (R4-2).",
        }

# ---------------------------------------------------------------------------
# Thread loop: LLM-driven negotiation with validator bounces
# ---------------------------------------------------------------------------

def _run_thread_negotiation(
    seller_constraints: Dict[str, Any],
    buyer: Dict[str, Any],
    logistics_cost_per_tonne_usd: float = DEFAULT_LOGISTICS_COST_PER_TONNE_USD,
    handling_cost_per_tonne_usd: float = DEFAULT_HANDLING_COST_PER_TONNE_USD,
    processing_cost_per_tonne_usd: float = DEFAULT_PROCESSING_COST_PER_TONNE_USD,
    batna_price_per_tonne_usd: Optional[float] = None,
    emit: Optional[Callable[[Dict[str, Any]], None]] = None,
    max_rounds: int = MAX_ROUNDS,
    seller_chain: Optional[List[Tuple[str, str]]] = None,
    buyer_chain: Optional[List[Tuple[str, str]]] = None,
    shared_state: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    fake_seller_moves: Optional[List[Dict[str, Any]]] = None,
    fake_buyer_moves: Optional[List[Dict[str, Any]]] = None,
    buyer_index: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Core loop for one buyer thread. Handles LLM proposals, validator bounces,
    retry cap, fallback, request_info, and deal_legal checks.
    Emits events if emit provided (via EVENTS.md envelope, but this function
    only returns the negotiate() dict; orchestrator wraps emit for envelope).
    """
    # validate inputs quickly (keep legacy checks)
    if not isinstance(seller_constraints, dict) or not isinstance(buyer, dict):
        raise ValueError("seller_constraints and buyer must be objects")

    try:
        seller_min = float(seller_constraints["min_acceptable_price_per_tonne_usd"])
        seller_preferred = float(seller_constraints.get("preferred_price_per_tonne_usd", seller_min))
        available = float(seller_constraints.get("available_quantity_tonnes", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid seller_constraints: {exc}") from exc

    try:
        buyer_id = str(buyer.get("buyer_id", "unknown_buyer"))
        buyer_max = float(buyer["max_acceptable_price_per_tonne_usd"])
        demand = float(buyer.get("annual_demand_tonnes", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid buyer: {exc}") from exc

    logistics_cost = float(logistics_cost_per_tonne_usd)
    handling_cost = float(handling_cost_per_tonne_usd)
    processing_cost = float(processing_cost_per_tonne_usd)

    effective_floor_static = max(seller_min, batna_price_per_tonne_usd) if batna_price_per_tonne_usd is not None else seller_min

    def _dynamic_floor() -> float:
        # live competing net value converted to $/t for this buyer's route.
        # Only BUYER bids (never the seller's own asks) in other threads that
        # haven't concluded rejected count — R2-2.
        if shared_state is None:
            return effective_floor_static
        best_net_per_tonne: Optional[float] = None
        for other_id, offers in shared_state.items():
            if other_id == buyer_id:
                continue
            for o in offers:
                if o.get("_role") != "buyer_agent" or o.get("_thread_dead"):
                    continue
                try:
                    p = float(o["price_per_tonne_usd"])
                    oc = float(o.get("_logistics_cost", logistics_cost))
                    net_per = p - oc - handling_cost - processing_cost
                    if best_net_per_tonne is None or net_per > best_net_per_tonne:
                        best_net_per_tonne = net_per
                except Exception:
                    continue
        if best_net_per_tonne is None:
            return effective_floor_static
        live_price = best_net_per_tonne + logistics_cost + handling_cost + processing_cost
        return max(effective_floor_static, round(live_price, 2))

    def _mark_thread_dead() -> None:
        # this thread concluded rejected: its own bids/asks stop counting
        # toward other threads' dynamic floor / leverage checks — R2-2.
        if shared_state is not None:
            for o in shared_state.get(buyer_id, []):
                o["_thread_dead"] = True

    # stash for validators
    seller_constraints = dict(seller_constraints)
    seller_constraints["_batna_price"] = batna_price_per_tonne_usd
    seller_constraints["_effective_floor"] = effective_floor_static

    # quick no-overlap -> immediate rejected without LLM loop (same as legacy)
    if effective_floor_static > buyer_max:
        # we still emit transcript via legacy for simplicity
        quantity = min(available, demand)
        if quantity.is_integer():
            quantity = int(quantity)
        transcript = _deterministic_transcript(
            seller_min=effective_floor_static, seller_preferred=seller_preferred,
            buyer_max=buyer_max, buyer_id=buyer_id, logistics_cost=logistics_cost,
            price=effective_floor_static, quantity=quantity if isinstance(quantity, int) else int(quantity),
            status="rejected", margin=None, batna_note=None,
        )
        # adapt transcript to new output shape
        transcript2 = [
            {"speaker": t["speaker"], "message": t["message"], "offer_id": None, "validator": {"ok": True, "reason": "no overlap"}}
            for t in transcript
        ]
        _mark_thread_dead()
        return {
            "status": "rejected",
            "price_per_tonne_usd": None,
            "quantity_tonnes": 0,
            "contract_term_months": CONTRACT_TERM_MONTHS,
            "transcript": transcript2,
            "rounds": 0,
            "final_offer_id": None,
            "agents": {"seller_agent": None, "buyer_agent": None},
            "margin_per_tonne_usd": None,
            "logistics_cost_per_tonne_usd": logistics_cost,
            "handling_cost_per_tonne_usd": handling_cost,
            "processing_cost_per_tonne_usd": processing_cost,
            "batna_price_per_tonne_usd": batna_price_per_tonne_usd,
            "validator_reason": f"no overlap: effective floor {effective_floor_static} > buyer_max {buyer_max}",
        }

    # state for this thread
    offer_store: Dict[str, Dict[str, Any]] = {}
    delivered_offers: List[Dict[str, Any]] = []
    history_for_prompt: List[Dict[str, Any]] = []  # events for prompt history
    transcript: List[Dict[str, Any]] = []
    offer_counter = 0
    current_round = 1
    # per-agent invalid streak
    invalid_streak: Dict[str, int] = {"seller_agent": 0, "buyer_agent": 0}
    # track which agent's turn
    # seller always opens round 1
    seller_fake_idx = [0]
    buyer_fake_idx = [0]

    if seller_chain is None:
        seller_chain = _resolve_chain_for_role("seller", buyer_index)
    if buyer_chain is None:
        buyer_chain = _resolve_chain_for_role("buyer", buyer_index)

    # optional emit helper for this thread (if orchestrator provides envelope emit,
    # we call it with raw move/validator info)
    agents_used: Dict[str, Optional[str]] = {"seller_agent": None, "buyer_agent": None}
    # keep last valid offer per role for fallback
    history_offers_for_fallback: List[Dict[str, Any]] = []

    # helper to emit via orchestrator envelope if provided, else just internal
    def _emit_thread_event(ev_type: str, from_agent: str, to_agent: str, payload: Dict[str, Any], validator_info: Optional[Dict[str, Any]], delivered: Optional[bool], model: Optional[str]):
        if emit is not None:
            # emit expects EVENTS.md envelope fields? But for thread loop we just send minimal;
            # orchestrator wraps. Here we send a simplified dict that orchestrator can convert.
            # For standalone thread without orchestrator envelope, we just store.
            emit({
                "type": ev_type,
                "from_agent": from_agent,
                "to_agent": to_agent,
                "payload": payload,
                "validator": validator_info,
                "delivered": delivered,
                "model": model,
                "round": payload.get("round", current_round),
            })

    # main loop
    while current_round <= max_rounds:
        cur_floor = _dynamic_floor()
        is_seller_turn = (current_round % 2 == 1)
        role = "seller_agent" if is_seller_turn else f"buyer_agent:{buyer_id}"  # for events
        role_short = "seller_agent" if is_seller_turn else "buyer_agent"
        chain = seller_chain if is_seller_turn else buyer_chain
        fake_moves = fake_seller_moves if is_seller_turn else fake_buyer_moves
        fake_idx = seller_fake_idx if is_seller_turn else buyer_fake_idx

        # build system prompt
        if is_seller_turn:
            from agents.negotiation.prompts import seller_system_prompt
            sys_prompt = seller_system_prompt(
                seller_constraints, buyer_id, logistics_cost, batna_price_per_tonne_usd, history_for_prompt, current_round, max_rounds
            )
        else:
            from agents.negotiation.prompts import buyer_system_prompt
            sys_prompt = buyer_system_prompt(buyer, logistics_cost, history_for_prompt, current_round, max_rounds)

        # history messages for LLM (only need last few turns to keep context small)
        llm_history: List[Dict[str, str]] = []
        for h in history_for_prompt[-6:]:
            # h is an event-like dict with type/payload
            if h.get("type") == "offer":
                llm_history.append({"role": "assistant" if h.get("from_agent")==role else "user",
                                    "content": json.dumps(h.get("payload", {}))})
        # call LLM
        move, model_str, fail_reason = _get_llm_move(
            role_short, sys_prompt, llm_history, chain,
            buyer_index=buyer_index, temperature=0.6,
            fake_moves=fake_moves, fake_index=fake_idx,
        )

        # track agent model
        if model_str and agents_used[role_short] is None:
            agents_used[role_short] = model_str
        elif model_str:
            agents_used[role_short] = model_str  # last used

        # timeout / total failure → fallback
        if move is None:
            # accurate cause: only a real timeout (or no chain configured, e.g.
            # offline/test mode) reports "timeout"; a classified provider
            # failure (429/exhausted/etc.) reports "provider_error" with the
            # real reason in detail, never hidden behind a generic message — R2-4
            if fail_reason is None or fail_reason == "timeout":
                cause = "timeout"
                detail = ("provider call exceeded 20s; deterministic engine holds the last valid offer"
                          if fail_reason == "timeout" else
                          "provider call exceeded 20s or all chains failed; deterministic engine holds the last valid offer")
            else:
                cause = "provider_error"
                detail = f"{fail_reason}; deterministic engine holds the last valid offer"
            # emit fallback event
            _emit_thread_event(
                "fallback", "runtime", role,
                {"round": current_round, "agent": role_short, "cause": cause, "detail": detail},
                None, None, None,
            )
            move = _deterministic_fallback_offer(role_short, seller_constraints, buyer, cur_floor, logistics_cost, current_round, history_offers_for_fallback)
            model_str = None
            # validate fallback (should always pass)
            ok, reason, gate = _validate_move_legal(move, role_short, seller_constraints, buyer, cur_floor, logistics_cost, shared_state, delivered_offers)
            if not ok:
                # if fallback itself invalid, we cannot proceed — break as rejected
                logging.getLogger(__name__).warning("Fallback move invalid: %s", reason)
                break
            # emit as delivered
            offer_counter += 1
            offer_id = f"{buyer_id}-o{offer_counter}"
            payload = {
                "offer_id": offer_id, "round": current_round,
                "price_per_tonne_usd": move["price_per_tonne_usd"],
                "quantity_tonnes": move["quantity_tonnes"],
                "contract_months": move["contract_months"],
                "message": move["message"], "rationale": move.get("rationale", ""),
            }
            offer_store[offer_id] = {**payload, "_role": role_short, "_logistics_cost": logistics_cost}
            delivered_offers.append(offer_store[offer_id])
            history_offers_for_fallback.append({**payload, "_role": role_short})
            history_for_prompt.append({"type": "offer", "from_agent": role_short, "payload": payload})
            transcript.append({"speaker": role_short, "message": move["message"], "offer_id": offer_id, "validator": {"ok": True, "reason": reason}})
            _emit_thread_event("offer", role, f"buyer_agent:{buyer_id}" if is_seller_turn else "seller_agent",
                               payload, {"ok": True, "gate": gate, "reason": reason}, True, model_str)
            if shared_state is not None:
                shared_state.setdefault(buyer_id, []).append({**payload, "_logistics_cost": logistics_cost, "_role": role_short})
            invalid_streak[role_short] = 0
            current_round += 1
            continue

        # handle request_info specially — does not consume round but emits two events
        if move.get("action") == "request_info":
            topic = move.get("topic")
            ok, reason, gate = _validate_move_legal(move, role_short, seller_constraints, buyer, cur_floor, logistics_cost, shared_state, delivered_offers)
            if not ok:
                # bounced info_request
                _emit_thread_event("info_request", role, "orchestrator" if topic=="best_competing_offer" else "logistics_agent",
                                   {"round": current_round, "topic": topic, "rationale": move.get("rationale","")},
                                   {"ok": False, "gate": gate, "reason": reason}, False, model_str)
                transcript.append({"speaker": role_short, "message": move.get("message",""), "offer_id": None, "validator": {"ok": False, "reason": reason}})
                invalid_streak[role_short] += 1
                if invalid_streak[role_short] >= MAX_INVALID_RETRIES:
                    _emit_thread_event("fallback", "runtime", role,
                                       {"round": current_round, "agent": role_short, "cause": "invalid_moves", "detail": f"2 invalid moves in a row: {reason}"},
                                       None, None, None)
                    fb = _deterministic_fallback_offer(role_short, seller_constraints, buyer, cur_floor, logistics_cost, current_round, history_offers_for_fallback)
                    fb_ok, fb_reason, fb_gate = _validate_move_legal(fb, role_short, seller_constraints, buyer, cur_floor, logistics_cost, shared_state, delivered_offers)
                    if fb_ok:
                        offer_counter += 1
                        offer_id = f"{buyer_id}-o{offer_counter}"
                        payload = {
                            "offer_id": offer_id, "round": current_round,
                            "price_per_tonne_usd": fb["price_per_tonne_usd"],
                            "quantity_tonnes": fb["quantity_tonnes"],
                            "contract_months": fb["contract_months"],
                            "message": fb["message"], "rationale": fb.get("rationale",""),
                        }
                        offer_store[offer_id] = {**payload, "_role": role_short, "_logistics_cost": logistics_cost}
                        delivered_offers.append(offer_store[offer_id])
                        history_offers_for_fallback.append({**payload, "_role": role_short})
                        history_for_prompt.append({"type": "offer", "from_agent": role_short, "payload": payload})
                        transcript.append({"speaker": role_short, "message": fb["message"], "offer_id": offer_id, "validator": {"ok": True, "reason": fb_reason}})
                        _emit_thread_event("offer", role, f"buyer_agent:{buyer_id}" if is_seller_turn else "seller_agent",
                                           payload, {"ok": True, "gate": fb_gate, "reason": fb_reason}, True, None)
                        if shared_state is not None:
                            shared_state.setdefault(buyer_id, []).append({**payload, "_logistics_cost": logistics_cost, "_role": role_short})
                        current_round += 1
                    invalid_streak[role_short] = 0
                continue
            # valid info request: emit request + response
            _emit_thread_event("info_request", role, "orchestrator" if topic=="best_competing_offer" else "logistics_agent",
                               {"round": current_round, "topic": topic, "rationale": move.get("rationale","")},
                               {"ok": True, "gate": gate, "reason": reason}, True, model_str)
            # build response data
            if topic == "route_cost":
                data = {"cost_per_tonne_usd": logistics_cost, "origin_port": "dhamra", "destination_port": buyer.get("port_id","chittagong")}
                responder = "logistics_agent"
            else:
                # best_competing_offer
                best = None
                if shared_state:
                    for other_id, offers in shared_state.items():
                        if other_id == buyer_id:
                            continue
                        for o in offers:
                            try:
                                p = float(o["price_per_tonne_usd"]); q = float(o["quantity_tonnes"])
                                oc = float(o.get("_logistics_cost", logistics_cost))
                                net = (p - oc - DEFAULT_HANDLING_COST_PER_TONNE_USD - DEFAULT_PROCESSING_COST_PER_TONNE_USD) * q
                                if best is None or net > best["total_net_value_usd"]:
                                    best = {"deal_id": other_id, "price_per_tonne_usd": p, "quantity_tonnes": q, "contract_months": o.get("contract_months"), "total_net_value_usd": round(net,2)}
                            except Exception:
                                continue
                data = best if best else {"deal_id": None, "price_per_tonne_usd": None}
                responder = "orchestrator"
            _emit_thread_event("info_response", responder, role,
                               {"round": current_round, "topic": topic, "data": data},
                               None, None, None)
            history_for_prompt.append({"type": "info_response", "from_agent": responder, "payload": {"topic": topic, "data": data}})
            invalid_streak[role_short] = 0
            # do not increment round; same agent will propose again next iteration
            continue

        # normal move validation
        ok, reason, gate = _validate_move_legal(move, role_short, seller_constraints, buyer, cur_floor, logistics_cost, shared_state, delivered_offers)

        # special handling for accept_offer deal_legal extra check
        if ok and move.get("action") == "accept_offer":
            # find latest counterparty offer id
            counterparty = "buyer_agent" if is_seller_turn else "seller_agent"
            # latest delivered offer from counterparty
            latest_id = None
            for oid, o in reversed(list(offer_store.items())):
                if o.get("_role") == counterparty:
                    latest_id = oid
                    break
            deal_ok, deal_reason = _validate_deal_legal(move, role_short, seller_constraints, buyer, cur_floor, offer_store, latest_id, logistics_cost)
            if not deal_ok:
                ok = False
                reason = deal_reason
                gate = "deal_legal"
            else:
                gate = "deal_legal"
                reason = deal_reason

        if not ok:
            # bounced
            payload: Dict[str, Any] = {}
            ev_type = "offer" if move.get("action")=="make_offer" else move.get("action","offer")
            if move.get("action")=="make_offer":
                payload = {"offer_id": None, "round": current_round,
                           "price_per_tonne_usd": move.get("price_per_tonne_usd"),
                           "quantity_tonnes": move.get("quantity_tonnes"),
                           "contract_months": move.get("contract_months"),
                           "message": move.get("message",""), "rationale": move.get("rationale","")}
                to_agent = role  # bounce returns to author
            elif move.get("action")=="accept_offer":
                payload = {"offer_id": move.get("offer_id"), "round": current_round, "message": move.get("message",""), "rationale": move.get("rationale","")}
                to_agent = role
                ev_type = "accept"
            elif move.get("action")=="reject":
                payload = {"round": current_round, "reason": move.get("reason",""), "message": move.get("message",""), "rationale": move.get("rationale","")}
                to_agent = role
                ev_type = "reject"
            else:
                payload = {"round": current_round, "reason": reason}
                to_agent = role

            _emit_thread_event(ev_type, role, to_agent, payload, {"ok": False, "gate": gate, "reason": reason}, False, model_str)
            transcript.append({"speaker": role_short, "message": move.get("message",""), "offer_id": None, "validator": {"ok": False, "reason": reason}})
            invalid_streak[role_short] += 1
            if invalid_streak[role_short] >= MAX_INVALID_RETRIES:
                _emit_thread_event("fallback", "runtime", role,
                                   {"round": current_round, "agent": role_short, "cause": "invalid_moves", "detail": f"2 invalid moves in a row: {reason}"},
                                   None, None, None)
                fb = _deterministic_fallback_offer(role_short, seller_constraints, buyer, cur_floor, logistics_cost, current_round, history_offers_for_fallback)
                fb_ok, fb_reason, fb_gate = _validate_move_legal(fb, role_short, seller_constraints, buyer, cur_floor, logistics_cost, shared_state, delivered_offers)
                if fb_ok:
                    offer_counter += 1
                    offer_id = f"{buyer_id}-o{offer_counter}"
                    payload_fb = {
                        "offer_id": offer_id, "round": current_round,
                        "price_per_tonne_usd": fb["price_per_tonne_usd"],
                        "quantity_tonnes": fb["quantity_tonnes"],
                        "contract_months": fb["contract_months"],
                        "message": fb["message"], "rationale": fb.get("rationale",""),
                    }
                    offer_store[offer_id] = {**payload_fb, "_role": role_short, "_logistics_cost": logistics_cost}
                    delivered_offers.append(offer_store[offer_id])
                    history_offers_for_fallback.append({**payload_fb, "_role": role_short})
                    history_for_prompt.append({"type": "offer", "from_agent": role_short, "payload": payload_fb})
                    transcript.append({"speaker": role_short, "message": fb["message"], "offer_id": offer_id, "validator": {"ok": True, "reason": fb_reason}})
                    _emit_thread_event("offer", role, f"buyer_agent:{buyer_id}" if is_seller_turn else "seller_agent",
                                       payload_fb, {"ok": True, "gate": fb_gate, "reason": fb_reason}, True, None)
                    if shared_state is not None:
                        shared_state.setdefault(buyer_id, []).append({**payload_fb, "_logistics_cost": logistics_cost, "_role": role_short})
                    current_round += 1
                invalid_streak[role_short] = 0
            continue

        # delivered valid move
        invalid_streak[role_short] = 0
        if move.get("action") == "make_offer":
            offer_counter += 1
            offer_id = f"{buyer_id}-o{offer_counter}"
            payload = {
                "offer_id": offer_id, "round": current_round,
                "price_per_tonne_usd": float(move["price_per_tonne_usd"]),
                "quantity_tonnes": move["quantity_tonnes"],
                "contract_months": int(move["contract_months"]),
                "message": move["message"], "rationale": move.get("rationale",""),
            }
            offer_store[offer_id] = {**payload, "_role": role_short, "_logistics_cost": logistics_cost}
            delivered_offers.append(offer_store[offer_id])
            history_offers_for_fallback.append({**payload, "_role": role_short})
            history_for_prompt.append({"type": "offer", "from_agent": role_short, "payload": payload})
            transcript.append({"speaker": role_short, "message": move["message"], "offer_id": offer_id, "validator": {"ok": True, "reason": reason}})
            _emit_thread_event("offer", role, f"buyer_agent:{buyer_id}" if is_seller_turn else "seller_agent",
                               payload, {"ok": True, "gate": gate, "reason": reason}, True, model_str)
            if shared_state is not None:
                shared_state.setdefault(buyer_id, []).append({**payload, "_logistics_cost": logistics_cost, "_role": role_short})
            current_round += 1
            continue

        if move.get("action") == "accept_offer":
            offer_id = move["offer_id"]
            payload = {"offer_id": offer_id, "round": current_round, "message": move["message"], "rationale": move.get("rationale","")}
            _emit_thread_event("accept", role, f"buyer_agent:{buyer_id}" if is_seller_turn else "seller_agent",
                               payload, {"ok": True, "gate": gate, "reason": reason}, True, model_str)
            transcript.append({"speaker": role_short, "message": move["message"], "offer_id": offer_id, "validator": {"ok": True, "reason": reason}})
            # deal closed
            accepted_offer = offer_store[offer_id]
            price = float(accepted_offer["price_per_tonne_usd"])
            qty = accepted_offer["quantity_tonnes"]
            months = int(accepted_offer["contract_months"])
            margin = round(compute_margin_per_tonne(price, logistics_cost, handling_cost, processing_cost), 2)
            return {
                "status": "accepted",
                "price_per_tonne_usd": price,
                "quantity_tonnes": qty,
                "contract_term_months": months,
                "transcript": transcript,
                "rounds": current_round,
                "final_offer_id": offer_id,
                "agents": agents_used,
                "margin_per_tonne_usd": margin,
                "logistics_cost_per_tonne_usd": logistics_cost,
                "handling_cost_per_tonne_usd": handling_cost,
                "processing_cost_per_tonne_usd": processing_cost,
                "batna_price_per_tonne_usd": batna_price_per_tonne_usd,
                "validator_reason": reason,
            }

        if move.get("action") == "reject":
            payload = {"round": current_round, "reason": move.get("reason",""), "message": move["message"], "rationale": move.get("rationale","")}
            _emit_thread_event("reject", role, f"buyer_agent:{buyer_id}" if is_seller_turn else "seller_agent",
                               payload, {"ok": True, "gate": gate, "reason": reason}, True, model_str)
            transcript.append({"speaker": role_short, "message": move["message"], "offer_id": None, "validator": {"ok": True, "reason": reason}})
            _mark_thread_dead()
            return {
                "status": "rejected",
                "price_per_tonne_usd": None,
                "quantity_tonnes": 0,
                "contract_term_months": CONTRACT_TERM_MONTHS,
                "transcript": transcript,
                "rounds": current_round,
                "final_offer_id": None,
                "agents": agents_used,
                "margin_per_tonne_usd": None,
                "logistics_cost_per_tonne_usd": logistics_cost,
                "handling_cost_per_tonne_usd": handling_cost,
                "processing_cost_per_tonne_usd": processing_cost,
                "batna_price_per_tonne_usd": batna_price_per_tonne_usd,
                "validator_reason": move.get("reason","rejected"),
            }

    # max rounds reached without acceptance -> countered with best open counter
    if delivered_offers:
        # best is the last delivered make_offer
        best = delivered_offers[-1]
        price = float(best["price_per_tonne_usd"])
        qty = best["quantity_tonnes"]
        months = int(best["contract_months"])

        # R2-3: a thread must not close `countered` below the seller's own
        # move-legal floor at THIS moment (other threads may have raised it
        # since the offer was delivered) — reject with reason instead.
        final_floor = _dynamic_floor()
        if price + 1e-9 < final_floor:
            _mark_thread_dead()
            return {
                "status": "rejected",
                "price_per_tonne_usd": None,
                "quantity_tonnes": 0,
                "contract_term_months": CONTRACT_TERM_MONTHS,
                "transcript": transcript,
                "rounds": max_rounds,
                "final_offer_id": best["offer_id"],
                "agents": agents_used,
                "margin_per_tonne_usd": None,
                "logistics_cost_per_tonne_usd": logistics_cost,
                "handling_cost_per_tonne_usd": handling_cost,
                "processing_cost_per_tonne_usd": processing_cost,
                "batna_price_per_tonne_usd": batna_price_per_tonne_usd,
                "validator_reason": f"best counter {price} at max rounds is below seller's floor {final_floor}; rejected instead of countered",
            }

        margin = round(compute_margin_per_tonne(price, logistics_cost, handling_cost, processing_cost), 2)
        # if margin <=0 or qty==0, treat as rejected? Keep as countered but mark
        status = "countered"
        if qty == 0 or margin <= 0:
            status = "rejected"
            _mark_thread_dead()
            return {
                "status": status,
                "price_per_tonne_usd": None,
                "quantity_tonnes": 0,
                "contract_term_months": CONTRACT_TERM_MONTHS,
                "transcript": transcript,
                "rounds": max_rounds,
                "final_offer_id": best["offer_id"],
                "agents": agents_used,
                "margin_per_tonne_usd": None,
                "logistics_cost_per_tonne_usd": logistics_cost,
                "handling_cost_per_tonne_usd": handling_cost,
                "processing_cost_per_tonne_usd": processing_cost,
                "batna_price_per_tonne_usd": batna_price_per_tonne_usd,
                "validator_reason": "countered but margin non-positive or qty zero" if status=="rejected" else "max rounds reached",
            }
        return {
            "status": status,
            "price_per_tonne_usd": price,
            "quantity_tonnes": qty,
            "contract_term_months": months,
            "transcript": transcript,
            "rounds": max_rounds,
            "final_offer_id": best["offer_id"],
            "agents": agents_used,
            "margin_per_tonne_usd": margin,
            "logistics_cost_per_tonne_usd": logistics_cost,
            "handling_cost_per_tonne_usd": handling_cost,
            "processing_cost_per_tonne_usd": processing_cost,
            "batna_price_per_tonne_usd": batna_price_per_tonne_usd,
            "validator_reason": "max rounds reached, best counter pending",
        }
    else:
        # no offers at all -> rejected
        _mark_thread_dead()
        return {
            "status": "rejected",
            "price_per_tonne_usd": None,
            "quantity_tonnes": 0,
            "contract_term_months": CONTRACT_TERM_MONTHS,
            "transcript": transcript,
            "rounds": max_rounds,
            "final_offer_id": None,
            "agents": agents_used,
            "margin_per_tonne_usd": None,
            "logistics_cost_per_tonne_usd": logistics_cost,
            "handling_cost_per_tonne_usd": handling_cost,
            "processing_cost_per_tonne_usd": processing_cost,
            "batna_price_per_tonne_usd": batna_price_per_tonne_usd,
            "validator_reason": "no offers delivered",
        }

# ---------------------------------------------------------------------------
# Legacy deterministic engine (preserved)
# ---------------------------------------------------------------------------

def _legacy_negotiate(
    seller_constraints: Dict[str, Any],
    buyer: Dict[str, Any],
    logistics_cost_per_tonne_usd: float = DEFAULT_LOGISTICS_COST_PER_TONNE_USD,
    handling_cost_per_tonne_usd: float = DEFAULT_HANDLING_COST_PER_TONNE_USD,
    processing_cost_per_tonne_usd: float = DEFAULT_PROCESSING_COST_PER_TONNE_USD,
    use_llm: bool = True,
    batna_price_per_tonne_usd: Optional[float] = None,
) -> Dict[str, Any]:
    if not isinstance(seller_constraints, dict) or not isinstance(buyer, dict):
        raise ValueError("seller_constraints and buyer must be objects")
    for record, fields in ((seller_constraints, ('min_acceptable_price_per_tonne_usd',
            'preferred_price_per_tonne_usd', 'available_quantity_tonnes')),
            (buyer, ('max_acceptable_price_per_tonne_usd', 'annual_demand_tonnes'))):
        for field in fields:
            if field in record:
                number(record[field], field)
    for value, name in ((logistics_cost_per_tonne_usd, 'logistics cost'),
                        (handling_cost_per_tonne_usd, 'handling cost'),
                        (processing_cost_per_tonne_usd, 'processing cost')):
        number(value, name)
    if batna_price_per_tonne_usd is not None:
        number(batna_price_per_tonne_usd, 'BATNA price')
    try:
        seller_min = float(seller_constraints["min_acceptable_price_per_tonne_usd"])
        seller_preferred = float(seller_constraints.get("preferred_price_per_tonne_usd", seller_min))
        available = float(seller_constraints.get("available_quantity_tonnes", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid seller_constraints: {exc}") from exc
    try:
        buyer_id = str(buyer.get("buyer_id", "unknown_buyer"))
        buyer_max = float(buyer["max_acceptable_price_per_tonne_usd"])
        demand = float(buyer.get("annual_demand_tonnes", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid buyer: {exc}") from exc
    logistics_cost = float(logistics_cost_per_tonne_usd)
    handling_cost = float(handling_cost_per_tonne_usd)
    processing_cost = float(processing_cost_per_tonne_usd)
    quantity = min(available, demand)
    if isinstance(quantity, float) and quantity.is_integer():
        quantity = int(quantity)
    batna_binding = batna_price_per_tonne_usd is not None and batna_price_per_tonne_usd > seller_min
    effective_seller_min = (
        max(seller_min, batna_price_per_tonne_usd) if batna_price_per_tonne_usd is not None else seller_min
    )
    batna_note = (
        f"(We have a walk-away alternative worth ${batna_price_per_tonne_usd:.2f}/t elsewhere — "
        f"won't go below that.)"
        if batna_binding
        else None
    )
    if effective_seller_min > buyer_max:
        status = "rejected"
        price: Optional[float] = None
        margin: Optional[float] = None
        transcript = generate_transcript(
            seller_min=effective_seller_min,
            seller_preferred=seller_preferred,
            buyer_max=buyer_max,
            buyer_id=buyer_id,
            logistics_cost=logistics_cost,
            price=effective_seller_min,
            quantity=quantity,
            status=status,
            margin=margin,
            use_llm=use_llm,
            batna_note=batna_note,
        )
        return {
            "status": status,
            "price_per_tonne_usd": price,
            "quantity_tonnes": 0,
            "contract_term_months": CONTRACT_TERM_MONTHS,
            "transcript": transcript,
            "margin_per_tonne_usd": margin,
            "logistics_cost_per_tonne_usd": logistics_cost,
            "handling_cost_per_tonne_usd": handling_cost,
            "processing_cost_per_tonne_usd": processing_cost,
            "batna_price_per_tonne_usd": batna_price_per_tonne_usd,
            "validator_reason": (
                f"no overlap: effective floor {effective_seller_min} "
                f"({'BATNA-adjusted, ' if batna_binding else ''}seller_min {seller_min}) > buyer_max {buyer_max}"
            ),
        }
    if effective_seller_min <= seller_preferred <= buyer_max:
        proposed_price = seller_preferred
    else:
        proposed_price = round((effective_seller_min + buyer_max) / 2.0, 2)
    proposed_price = round(float(proposed_price), 2)
    is_valid, reason = validate_proposal(proposed_price, effective_seller_min, buyer_max)
    if not is_valid:
        transcript = generate_transcript(
            seller_min=effective_seller_min,
            seller_preferred=seller_preferred,
            buyer_max=buyer_max,
            buyer_id=buyer_id,
            logistics_cost=logistics_cost,
            price=proposed_price,
            quantity=quantity,
            status="rejected",
            margin=None,
            use_llm=use_llm,
            batna_note=batna_note,
        )
        return {
            "status": "rejected",
            "price_per_tonne_usd": None,
            "quantity_tonnes": 0,
            "contract_term_months": CONTRACT_TERM_MONTHS,
            "transcript": transcript,
            "margin_per_tonne_usd": None,
            "logistics_cost_per_tonne_usd": logistics_cost,
            "handling_cost_per_tonne_usd": handling_cost,
            "processing_cost_per_tonne_usd": processing_cost,
            "batna_price_per_tonne_usd": batna_price_per_tonne_usd,
            "validator_reason": reason,
        }
    margin = round(
        compute_margin_per_tonne(proposed_price, logistics_cost, handling_cost, processing_cost), 2
    )
    overlap = buyer_max - effective_seller_min
    had_to_compromise = seller_preferred > buyer_max or seller_preferred < effective_seller_min
    thin_spread = overlap < 2.0
    thin_margin = margin < 2.0
    if had_to_compromise or thin_spread or thin_margin:
        status = "countered"
    else:
        status = "accepted"
    transcript = generate_transcript(
        seller_min=effective_seller_min,
        seller_preferred=seller_preferred,
        buyer_max=buyer_max,
        buyer_id=buyer_id,
        logistics_cost=logistics_cost,
        price=proposed_price,
        quantity=quantity,
        status=status,
        margin=margin,
        use_llm=use_llm,
        batna_note=batna_note,
    )
    if not is_valid:
        raise ValueError(f"Invariant violated: price out of bounds ({reason})")
    q_valid, q_reason = validate_quantity(quantity, available, demand)
    if not q_valid and quantity != 0:
        raise ValueError(f"Quantity validation failed: {q_reason}")
    if quantity == 0 or margin <= 0:
        status = "rejected"
        transcript = generate_transcript(
            seller_min=effective_seller_min,
            seller_preferred=seller_preferred,
            buyer_max=buyer_max,
            buyer_id=buyer_id,
            logistics_cost=logistics_cost,
            price=proposed_price,
            quantity=quantity,
            status="rejected",
            margin=margin,
            use_llm=use_llm,
            batna_note=batna_note,
        )
        return {
            "status": status,
            "price_per_tonne_usd": None,
            "quantity_tonnes": 0,
            "contract_term_months": CONTRACT_TERM_MONTHS,
            "transcript": transcript,
            "margin_per_tonne_usd": None,
            "logistics_cost_per_tonne_usd": logistics_cost,
            "handling_cost_per_tonne_usd": handling_cost,
            "processing_cost_per_tonne_usd": processing_cost,
            "batna_price_per_tonne_usd": batna_price_per_tonne_usd,
            "validator_reason": "quantity is zero: no viable deal (available or demand is 0)"
            if quantity == 0 else "non-positive margin: no profitable deal at the proposed price",
        }
    return {
        "status": status,
        "price_per_tonne_usd": proposed_price,
        "quantity_tonnes": quantity,
        "contract_term_months": CONTRACT_TERM_MONTHS,
        "transcript": transcript,
        "margin_per_tonne_usd": margin,
        "logistics_cost_per_tonne_usd": logistics_cost,
        "handling_cost_per_tonne_usd": handling_cost,
        "processing_cost_per_tonne_usd": processing_cost,
        "batna_price_per_tonne_usd": batna_price_per_tonne_usd,
        "validator_reason": reason,
    }

# ---------------------------------------------------------------------------
# Public entry: dispatches legacy vs new loop
# ---------------------------------------------------------------------------

def negotiate(
    seller_constraints: Dict[str, Any],
    buyer: Dict[str, Any],
    logistics_cost_per_tonne_usd: float = DEFAULT_LOGISTICS_COST_PER_TONNE_USD,
    handling_cost_per_tonne_usd: float = DEFAULT_HANDLING_COST_PER_TONNE_USD,
    processing_cost_per_tonne_usd: float = DEFAULT_PROCESSING_COST_PER_TONNE_USD,
    use_llm: bool = True,
    batna_price_per_tonne_usd: Optional[float] = None,
    emit: Optional[Callable[[Dict[str, Any]], None]] = None,
    contract_months: Optional[int] = None,
    # new test hooks
    _fake_seller_moves: Optional[List[Dict[str, Any]]] = None,
    _fake_buyer_moves: Optional[List[Dict[str, Any]]] = None,
    buyer_index: Optional[int] = None,
    _shared_state: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    max_rounds: int = MAX_ROUNDS,
) -> Dict[str, Any]:
    """
    Negotiate a deal. Dispatches to legacy deterministic engine when called
    without new contract_months/emit fields (keeps existing tests green), and
    to the LLM-gated threaded loop otherwise.

    New input shape (AGENTS.md §2) adds:
      seller_constraints.contract_months_min/max, preferred_contract_months
      buyer.contract_months_min/max
      emit callable
      logistics_cost_per_tonne_usd (real route cost)
      batna_price_per_tonne_usd

    Output adds: rounds, final_offer_id, agents
    """
    # detect new-style call: explicit new-thread signals only.
    # Presence of contract_months in buyer/seller is NOT a trigger because
    # buyers.json now always carries those fields (added Step 0); using them
    # as a trigger would force every legacy call into the new loop.
    is_new_style = (
        emit is not None
        or _fake_seller_moves is not None
        or _fake_buyer_moves is not None
        or _shared_state is not None
        or buyer_index is not None
        or max_rounds != MAX_ROUNDS
    )
    # Also treat use_llm=True with new-style chain env as new-style if seller has chain?
    # But keep legacy for plain tests: they call negotiate(seller, buyer, use_llm=False) without contract ranges.

    if not is_new_style:
        # legacy path — preserves old tests and orchestrator's sequential BATNA pipeline
        result = _legacy_negotiate(
            seller_constraints, buyer, logistics_cost_per_tonne_usd,
            handling_cost_per_tonne_usd, processing_cost_per_tonne_usd,
            use_llm, batna_price_per_tonne_usd,
        )
        # add new fields for uniform shape so orchestrator can handle both
        result.setdefault("rounds", len(result.get("transcript", [])))
        result.setdefault("final_offer_id", None)
        result.setdefault("agents", {"seller_agent": None, "buyer_agent": None})
        return result

    # new LLM-gated path
    # If use_llm is False, we still want to run the loop but with deterministic fallback as the LLM (no API calls)
    # So force a fast deterministic path: treat as if LLM always falls back
    if not use_llm:
        # run thread loop with empty chains → every LLM call will timeout → deterministic fallback
        empty_chain: List[Tuple[str, str]] = []
        return _run_thread_negotiation(
            seller_constraints, buyer, logistics_cost_per_tonne_usd,
            handling_cost_per_tonne_usd, processing_cost_per_tonne_usd,
            batna_price_per_tonne_usd, emit, max_rounds,
            seller_chain=empty_chain, buyer_chain=empty_chain,
            shared_state=_shared_state,
            fake_seller_moves=_fake_seller_moves, fake_buyer_moves=_fake_buyer_moves,
            buyer_index=buyer_index,
        )

    # normal new path with real LLM chains
    return _run_thread_negotiation(
        seller_constraints, buyer, logistics_cost_per_tonne_usd,
        handling_cost_per_tonne_usd, processing_cost_per_tonne_usd,
        batna_price_per_tonne_usd, emit, max_rounds,
        shared_state=_shared_state,
        fake_seller_moves=_fake_seller_moves, fake_buyer_moves=_fake_buyer_moves,
        buyer_index=buyer_index,
    )


def run_negotiation(*args, **kwargs) -> Dict[str, Any]:
    return negotiate(*args, **kwargs)

__all__ = [
    "DEFAULT_LOGISTICS_COST_PER_TONNE_USD",
    "DEFAULT_HANDLING_COST_PER_TONNE_USD",
    "DEFAULT_PROCESSING_COST_PER_TONNE_USD",
    "CONTRACT_TERM_MONTHS",
    "MAX_ROUNDS",
    "validate_proposal",
    "validate_quantity",
    "compute_margin_per_tonne",
    "generate_transcript",
    "negotiate",
    "run_negotiation",
    "_parse_chain",
    "_validate_move_legal",
    "_validate_deal_legal",
]

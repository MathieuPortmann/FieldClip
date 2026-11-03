# -*- coding: utf-8 -*-
"""
File-name scheme: build a name from the answers, and read the answers back
from a name. Both directions come from the same template, so changing the
template in a profile never leaves the app unable to recognise its own files.

Template syntax (see docs/configuration.md):

    "{device}_{date}_{time}_{group}_{site}"
    "{device}_{date}_{time}_{site?}_Training"

Tokens are separated by the profile's separator. {field} is a field,
{field?} an optional one (dropped when empty), anything else a literal word.
Fields: device, date, time, site, group. {date} and {time} must be adjacent:
they are the anchor the parser looks for first.

Inside the app a timestamp is always the string "YYYY-MM-DD_HH-MM-SS"; only
the file name uses the profile's date_format / time_format.
"""
import re
from datetime import datetime
from pathlib import Path

FIELDS = ("device", "date", "time", "site", "group")
INTERNAL_TS = "%Y-%m-%d_%H-%M-%S"
_TOKEN_RE = re.compile(r"^\{(\w+)(\?)?\}$")


def sanitize(text: str) -> str:
    return re.sub(r'_+', '_', re.sub(r'[\\/:*?"<>|()\s]+', '_', text or '')).strip('_')


def parse_template(template: str, sep: str) -> list:
    """[(kind, name, optional)], kind 'field' or 'literal'. Raises ValueError."""
    if not sep:
        raise ValueError("separator is empty")
    tokens = []
    for raw in template.split(sep):
        m = _TOKEN_RE.match(raw)
        if m:
            if m.group(1) not in FIELDS:
                raise ValueError(f"unknown field {{{m.group(1)}}} "
                                 f"(known: {', '.join(FIELDS)})")
            tokens.append(("field", m.group(1), bool(m.group(2))))
        elif "{" in raw or "}" in raw:
            raise ValueError(f"malformed token '{raw}'")
        elif raw:
            tokens.append(("literal", raw, False))
        else:
            raise ValueError("empty token (doubled separator?)")
    names = [n for k, n, _ in tokens if k == "field"]
    if len(names) != len(set(names)):
        raise ValueError("a field appears twice")
    if "date" not in names or "time" not in names:
        raise ValueError("{date} and {time} are both required")
    i = names.index("date")
    if i + 1 >= len(names) or names[i + 1] != "time":
        raise ValueError("{time} must directly follow {date}")
    di = next(j for j, t in enumerate(tokens) if t[1] == "date" and t[0] == "field")
    if tokens[di + 1] != ("field", "time", tokens[di + 1][2]):
        raise ValueError("{time} must directly follow {date}")
    for j, (k, _n, _o) in enumerate(tokens):
        if k == "literal" and 0 < j < len(tokens) - 1 and \
                tokens[j - 1][0] == "field" and tokens[j + 1][0] == "field":
            raise ValueError("literal words are only allowed at the start or the end")
    return tokens


def _format_regex(fmt: str) -> re.Pattern:
    """Regex matching what strftime(fmt) produces for the usual directives."""
    sizes = {"Y": r"\d{4}", "y": r"\d{2}", "m": r"\d{2}", "d": r"\d{2}",
             "H": r"\d{2}", "M": r"\d{2}", "S": r"\d{2}", "j": r"\d{3}",
             "b": r"[A-Za-z]{3}", "B": r"[A-Za-z]+", "p": r"[AaPp][Mm]",
             "I": r"\d{2}", "%": "%"}
    out, i = "", 0
    while i < len(fmt):
        if fmt[i] == "%" and i + 1 < len(fmt):
            d = fmt[i + 1]
            if d not in sizes:
                raise ValueError(f"unsupported directive %{d}")
            out += sizes[d]
            i += 2
        else:
            out += re.escape(fmt[i])
            i += 1
    return re.compile("^" + out + "$")


def _norm(text: str, sep: str) -> str:
    return text.lower().replace("-", "_").replace(sep, "_")


class Naming:
    """Build / parse names for one profile (a settings dict)."""

    def __init__(self, settings: dict):
        n = settings["naming"]
        self.sep = n["separator"]
        self.date_fmt = n["date_format"]
        self.time_fmt = n["time_format"]
        self.tokens = parse_template(n["template"], self.sep)
        self.tr_tokens = parse_template(n["training_template"], self.sep)
        self._date_re = _format_regex(self.date_fmt)
        self._time_re = _format_regex(self.time_fmt)
        self._default_ts = (self.date_fmt == "%Y-%m-%d" and self.time_fmt == "%H-%M-%S"
                            and self.sep == "_")

        try:
            from .settings import value_names, value_aliases
        except ImportError:                     # run as a plain script
            from settings import value_names, value_aliases
        f = settings["fields"]
        self.values = {k: value_names(f[k]["values"]) for k in ("site", "group")}
        self.aliases = {k: value_aliases(f[k]["values"]) for k in ("site", "group")}

        d = settings["devices"]
        self.builtin_devices = list(d["builtin"])
        self.legacy_devices = dict(d.get("legacy_aliases") or {})
        self.dji_fallback = d["dji_fallback"]

        lay = settings["layout"]
        self.layout = [p for p in lay["path"].split("/") if p]
        self.main_dir = lay["main_dir"]
        self.training_dir = lay["training_dir"]

        # The training template is recognised by its literal words.
        lead, trail = [], []
        for k, name, _ in self.tr_tokens:
            if k != "literal":
                break
            lead.append(name)
        for k, name, _ in reversed(self.tr_tokens):
            if k != "literal":
                break
            trail.insert(0, name)
        self.tr_prefix = (self.sep.join(lead) + self.sep) if lead else ""
        self.tr_suffix = (self.sep + self.sep.join(trail)) if trail else ""

    # ── timestamps ──────────────────────────────────────────────────────────
    def _ts_parts(self, timestamp: str) -> tuple:
        if self._default_ts or not timestamp:
            d, _, t = (timestamp or "").partition("_")
            return d, t
        try:
            dt = datetime.strptime(timestamp, INTERNAL_TS)
        except ValueError:
            d, _, t = timestamp.partition("_")
            return d, t
        return dt.strftime(self.date_fmt), dt.strftime(self.time_fmt)

    def _ts_internal(self, d: str, t: str) -> str:
        if self._default_ts:
            return f"{d}_{t}"
        try:
            return datetime.strptime(f"{d}|{t}", f"{self.date_fmt}|{self.time_fmt}") \
                .strftime(INTERNAL_TS)
        except ValueError:
            return f"{d}_{t}"

    # ── build ───────────────────────────────────────────────────────────────
    def build(self, device, timestamp, is_training, group=None, site=None) -> str:
        """Base name (no extension) for one clip."""
        d, t = self._ts_parts(timestamp)
        vals = {"device": device or "", "date": d, "time": t,
                "site": sanitize(site or ""), "group": sanitize(group or "")}
        out = []
        for kind, name, optional in (self.tr_tokens if is_training else self.tokens):
            if kind == "literal":
                out.append(name)
            elif vals[name] or not optional:
                out.append(vals[name])
        return self.sep.join(out)

    def is_training_name(self, filename: str) -> bool:
        stem = Path(filename).stem
        if not (self.tr_prefix or self.tr_suffix):
            return False
        return stem.startswith(self.tr_prefix) and stem.endswith(self.tr_suffix)

    # ── parse ───────────────────────────────────────────────────────────────
    def _match_value(self, field: str, parts: list, whole: bool) -> tuple:
        """(value, n_parts) for a known value of `field` at the start of parts.

        whole=True: the value must span all of parts (the last field)."""
        if not parts:
            return "", 0
        known = self.values[field]
        if not known:                               # free text
            return (self.sep.join(parts), len(parts)) if whole else (parts[0], 1)
        cands = [(v, v) for v in known] + list(self.aliases[field].items())
        cands.sort(key=lambda c: len(c[0]), reverse=True)
        spans = [len(parts)] if whole else range(len(parts), 0, -1)
        for k in spans:
            text = _norm(self.sep.join(parts[:k]), self.sep)
            for written, name in cands:
                if text in (_norm(written, self.sep), _norm(sanitize(written), self.sep)):
                    return name, k
        return "", 0

    def _match_device(self, text: str, all_devices) -> str:
        devs = self.builtin_devices + [d for d in (all_devices or [])
                                       if d not in self.builtin_devices]
        for dev in devs:
            if text == dev or text.replace("-", "_") == dev.replace("-", "_"):
                return dev
        if text in self.legacy_devices:
            return self.legacy_devices[text] or self.dji_fallback
        return ""

    def _consume(self, fields: list, parts: list, result: dict, all_devices) -> None:
        """Fill `fields` (in order) from parts; unmatched fields stay empty."""
        for i, name in enumerate(fields):
            last = i == len(fields) - 1
            if name == "device":
                if last:
                    text, k = self.sep.join(parts), len(parts)
                else:
                    text, k = (parts[0], 1) if parts else ("", 0)
                dev = self._match_device(text, all_devices)
                if dev:
                    result["device"] = dev
                    result["guessed"].add("device")
                parts = parts[k:]
                continue
            value, k = self._match_value(name, parts, whole=last)
            if value:
                result[name] = value
                result["guessed"].add(name)
                parts = parts[k:]

    def parse(self, filename: str, all_devices=None) -> dict:
        """Answers read back from a name written by build().

        'matched' is False when no date/time anchor was found (a raw camera
        name); the caller then falls back to device detection."""
        stem = Path(filename).stem
        result = {"site": "", "device": "Unknown", "group": "", "is_training": False,
                  "timestamp": "", "guessed": set(), "matched": False}
        tokens = self.tokens
        if (self.tr_prefix or self.tr_suffix) and stem.startswith(self.tr_prefix) \
                and stem.endswith(self.tr_suffix) \
                and len(stem) > len(self.tr_prefix) + len(self.tr_suffix):
            result["is_training"] = True
            result["guessed"].add("is_training")
            stem = stem[len(self.tr_prefix):len(stem) - len(self.tr_suffix)]
            tokens = self.tr_tokens
        fields = [n for k, n, _ in tokens if k == "field"]
        di = fields.index("date")
        before, after = fields[:di], fields[di + 2:]

        parts = stem.split(self.sep)
        ts_idx = None
        for i in range(len(parts) - 1):
            if self._date_re.match(parts[i]) and self._time_re.match(parts[i + 1]):
                ts_idx = i
                break
        if ts_idx is None or (before and ts_idx == 0):
            return result

        result["matched"] = True
        result["timestamp"] = self._ts_internal(parts[ts_idx], parts[ts_idx + 1])
        result["guessed"].add("timestamp")
        self._consume(before, parts[:ts_idx], result, all_devices)
        self._consume(after, parts[ts_idx + 2:], result, all_devices)
        return result

    # ── destination folders ─────────────────────────────────────────────────
    def category(self, is_training: bool) -> str:
        return self.training_dir if is_training else self.main_dir

    def dest_parts(self, is_training: bool, record: dict) -> list:
        """Folder names under the destination root, e.g. ['Training', 'Site_A']."""
        vals = {"category": self.category(is_training),
                "site": sanitize(record.get("site") or ""),
                "group": sanitize(record.get("group") or ""),
                "device": record.get("device") or ""}
        ts = record.get("timestamp") or ""
        vals["date"], vals["time"] = self._ts_parts(ts) if ts else ("", "")
        out = []
        for comp in self.layout:
            try:
                name = comp.format(**vals)
            except (KeyError, IndexError, ValueError):
                name = comp
            if name:
                out.append(name)
        return out

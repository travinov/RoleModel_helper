from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path


STATIC_INSTRUCTION_TITLE = "Статичная инструкция"


def static_instruction_path() -> Path:
    configured = os.getenv("RM_STATIC_INSTRUCTION_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "Doc" / "static_instruction.txt"


def instruction_upload_dir() -> Path:
    return Path(os.getenv("RM_INSTRUCTION_UPLOAD_DIR", str(Path.home() / "rolemodel_instruction_uploads")))


def safe_instruction_upload_name(raw_name: str) -> str:
    name = Path(raw_name or "instruction.txt").name
    stem = re.sub(r"[^a-zA-Zа-яА-Я0-9._ -]+", "_", Path(name).stem).strip(" ._")
    suffix = Path(name).suffix.lower()
    if suffix not in {".txt", ".rtf"}:
        raise ValueError("Можно загрузить только файл .txt или .rtf")
    return f"{stem or 'instruction'}{suffix}"


def normalize_instruction_text(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def rtf_to_text(data: bytes) -> str:
    source = data.decode("latin-1", errors="ignore")
    out: list[str] = []
    stack: list[tuple[bool, int]] = []
    skip_group = False
    uc_skip = 1
    i = 0
    ignorable_destinations = {
        "fonttbl",
        "colortbl",
        "stylesheet",
        "info",
        "pict",
        "object",
        "expandedcolortbl",
    }

    while i < len(source):
        char = source[i]
        if char == "{":
            stack.append((skip_group, uc_skip))
            i += 1
            continue
        if char == "}":
            if stack:
                skip_group, uc_skip = stack.pop()
            i += 1
            continue
        if char == "\\":
            i += 1
            if i >= len(source):
                break
            marker = source[i]
            if marker in "\\{}":
                if not skip_group:
                    out.append(marker)
                i += 1
                continue
            if marker in "\r\n":
                if not skip_group:
                    out.append("\n")
                while i < len(source) and source[i] in "\r\n":
                    i += 1
                continue
            if marker == "*":
                skip_group = True
                i += 1
                continue
            if marker == "'":
                hex_value = source[i + 1 : i + 3]
                if len(hex_value) == 2 and re.fullmatch(r"[0-9a-fA-F]{2}", hex_value):
                    if not skip_group:
                        out.append(bytes([int(hex_value, 16)]).decode("cp1251", errors="ignore"))
                    i += 3
                    continue

            start = i
            while i < len(source) and source[i].isalpha():
                i += 1
            word = source[start:i]
            sign = 1
            if i < len(source) and source[i] in "+-":
                sign = -1 if source[i] == "-" else 1
                i += 1
            number_start = i
            while i < len(source) and source[i].isdigit():
                i += 1
            number = None
            if i > number_start:
                number = sign * int(source[number_start:i])
            if i < len(source) and source[i] == " ":
                i += 1

            if word in ignorable_destinations:
                skip_group = True
                continue
            if skip_group:
                continue
            if word == "uc" and number is not None:
                uc_skip = max(0, number)
            elif word == "u" and number is not None:
                codepoint = number if number >= 0 else 65536 + number
                if 0 <= codepoint <= 0x10FFFF:
                    out.append(chr(codepoint))
            elif word in {"par", "line"}:
                out.append("\n")
            elif word == "tab":
                out.append("\t")
            elif word == "emdash":
                out.append("-")
            elif word == "endash":
                out.append("-")
            continue
        if not skip_group and char not in "\r\n":
            out.append(char)
        i += 1

    return normalize_instruction_text("".join(out))


def decode_instruction_upload(safe_name: str, body: bytes) -> str:
    suffix = Path(safe_name).suffix.lower()
    if suffix == ".rtf":
        return rtf_to_text(body)
    if suffix == ".txt":
        return normalize_instruction_text(body.decode("utf-8-sig"))
    raise ValueError("Можно загрузить только файл .txt или .rtf")


def read_static_instruction(file_path: str | Path | None = None) -> str | None:
    path = Path(file_path) if file_path else static_instruction_path()
    if not path.exists() or not path.is_file():
        return None
    if path.suffix.lower() != ".txt":
        return None
    try:
        text = normalize_instruction_text(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return None
    return text or None


def save_instruction_upload(raw_name: str, body: bytes) -> dict:
    if not body:
        raise ValueError("Файл пустой")
    max_size = int(os.getenv("RM_INSTRUCTION_UPLOAD_MAX_BYTES", str(2 * 1024 * 1024)))
    if len(body) > max_size:
        raise ValueError(f"Файл больше допустимого размера {max_size} bytes")

    safe_name = safe_instruction_upload_name(raw_name)
    text = decode_instruction_upload(safe_name, body)
    if not text:
        raise ValueError("В файле не найден текст инструкции")

    upload_dir = instruction_upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_path = upload_dir / f"{timestamp}_{safe_name}"
    upload_path.write_bytes(body)

    target_path = static_instruction_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    tmp_path.write_text(text + "\n", encoding="utf-8")
    tmp_path.replace(target_path)

    return {
        "uploaded_file": str(upload_path),
        "static_instruction_path": str(target_path),
        "text_length": len(text),
        "lines_count": len(text.splitlines()),
    }

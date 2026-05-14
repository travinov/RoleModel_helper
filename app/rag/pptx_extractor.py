from __future__ import annotations

import posixpath
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

SLIDE_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def _extract_slide_texts(zip_file: ZipFile, slide_xml_path: str) -> list[str]:
    root = ET.fromstring(zip_file.read(slide_xml_path))
    texts: list[str] = []
    for node in root.findall(".//a:t", SLIDE_NS):
        if node.text and node.text.strip():
            texts.append(node.text.strip())
    return texts


def _resolve_image_targets(zip_file: ZipFile, slide_no: int) -> list[str]:
    rel_path = f"ppt/slides/_rels/slide{slide_no}.xml.rels"
    if rel_path not in zip_file.namelist():
        return []
    rel_root = ET.fromstring(zip_file.read(rel_path))
    targets: list[str] = []
    for child in rel_root:
        target = child.attrib.get("Target", "")
        rel_type = child.attrib.get("Type", "")
        if "image" not in rel_type:
            continue
        resolved = posixpath.normpath(posixpath.join("ppt/slides", target))
        targets.append(resolved)
    return targets


def _ocr_image(image_bytes: bytes, tesseract_cmd: str, tesseract_langs: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as temp_image:
        temp_image.write(image_bytes)
        temp_image.flush()
        try:
            result = subprocess.run(
                [tesseract_cmd, temp_image.name, "stdout", "-l", tesseract_langs],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return ""
        if result.returncode != 0:
            fallback = subprocess.run(
                [tesseract_cmd, temp_image.name, "stdout", "-l", "eng"],
                check=False,
                capture_output=True,
                text=True,
            )
            if fallback.returncode != 0:
                return ""
            return re.sub(r"\s+", " ", fallback.stdout).strip()
        return re.sub(r"\s+", " ", result.stdout).strip()


def _image_summary(slide_text: str, ocr_text: str, image_index: int) -> str:
    if ocr_text:
        return (
            f"Скриншот интерфейса #{image_index}. "
            f"Контекст слайда: {slide_text[:220]}. "
            f"OCR: {ocr_text[:500]}"
        ).strip()
    return f"Скриншот интерфейса #{image_index}, связанный с инструкцией: {slide_text[:320]}".strip()


def extract_pptx_document(file_path: str | Path, tesseract_cmd: str, tesseract_langs: str) -> dict:
    path = Path(file_path)
    slides: list[dict] = []
    with ZipFile(path) as zip_file:
        slide_paths = sorted(
            [name for name in zip_file.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")],
            key=lambda item: int(re.search(r"slide(\d+)\.xml", item).group(1)),
        )
        for slide_xml_path in slide_paths:
            slide_no = int(re.search(r"slide(\d+)\.xml", slide_xml_path).group(1))
            texts = _extract_slide_texts(zip_file, slide_xml_path)
            slide_text = "\n".join(texts)
            slide_text_normalized = re.sub(r"\s+", " ", slide_text).strip()
            image_targets = _resolve_image_targets(zip_file, slide_no)
            images: list[dict] = []
            for image_index, target in enumerate(image_targets, start=1):
                image_bytes = zip_file.read(target)
                ocr_text = _ocr_image(image_bytes, tesseract_cmd, tesseract_langs)
                images.append(
                    {
                        "image_index": image_index,
                        "target": target,
                        "ocr_text": ocr_text,
                        "summary_text": _image_summary(slide_text_normalized, ocr_text, image_index),
                    }
                )
            slides.append(
                {
                    "slide_no": slide_no,
                    "slide_text": slide_text_normalized,
                    "slide_text_raw": slide_text,
                    "images": images,
                }
            )
    return {
        "document_title": path.stem,
        "slides": slides,
        "document_metadata": {
            "file_path": str(path),
            "slides_count": len(slides),
            "source_scope": "RM00000705" if "RM00000705" in " ".join(s["slide_text"] for s in slides) else None,
        },
    }

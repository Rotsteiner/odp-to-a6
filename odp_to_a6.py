#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Erstellt aus einer LibreOffice-Impress-ODP druckbare Vorlesekarten (4 pro A4)."""

from __future__ import annotations

import argparse
import sys
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Paragraph


NS = {
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}


@dataclass(frozen=True)
class TextBlock:
    text: str
    heading: bool = False
    list_item: bool = False


def _collect_text(element: ET.Element) -> str:
    """Liest Text inklusive ODF-Leerzeichen, Tabs und Zeilenumbrüchen."""
    parts = [element.text or ""]
    for child in element:
        tag = child.tag
        if tag == f"{{{NS['text']}}}s":
            count = int(child.attrib.get(f"{{{NS['text']}}}c", "1"))
            parts.append(" " * max(1, count))
        elif tag == f"{{{NS['text']}}}tab":
            parts.append("    ")
        elif tag == f"{{{NS['text']}}}line-break":
            parts.append("\n")
        else:
            parts.append(_collect_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _iter_paragraphs(node: ET.Element, inside_list: bool = False):
    """Liefert Absätze in XML-Reihenfolge und erkennt Listen ohne getparent()."""
    for child in node:
        is_list_item = child.tag == f"{{{NS['text']}}}list-item"
        in_list = inside_list or is_list_item

        if child.tag in (f"{{{NS['text']}}}p", f"{{{NS['text']}}}h"):
            value = _collect_text(child).strip()
            if value:
                yield TextBlock(
                    text=value,
                    heading=(child.tag == f"{{{NS['text']}}}h"),
                    list_item=in_list,
                )
        else:
            yield from _iter_paragraphs(child, in_list)


def extract_slides(filename: Path) -> list[list[TextBlock]]:
    """Extrahiert Textabsätze und Überschriften aus jeder ODP-Folie."""
    try:
        with zipfile.ZipFile(filename) as odp:
            xml_data = odp.read("content.xml")
    except KeyError as exc:
        raise ValueError("In der ODP-Datei fehlt content.xml.") from exc

    root = ET.fromstring(xml_data)
    slides = []
    for page in root.findall(".//draw:page", NS):
        slides.append(list(_iter_paragraphs(page)))
    return slides


def paragraph_markup(block: TextBlock) -> str:
    text = escape(block.text).replace("\n", "<br/>")
    return f"• {text}" if block.list_item else text


def make_styles(font_size: float):
    leading = font_size * 1.22
    heading = ParagraphStyle(
        "card-heading",
        fontName="Helvetica-Bold",
        fontSize=font_size,
        leading=leading,
        textColor=colors.black,
        spaceAfter=font_size * 0.65,
        splitLongWords=1,
    )
    body = ParagraphStyle(
        "card-body",
        fontName="Helvetica",
        fontSize=font_size,
        leading=leading,
        textColor=colors.black,
        spaceAfter=font_size * 0.45,
        splitLongWords=1,
    )
    return heading, body


def render_card(canvas, x, y, width, height, blocks, slide_number,
                padding, font_max, font_min):
    """Zeichnet eine Karte und meldet sichtbar, falls Inhalt überläuft."""
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#777777"))
    canvas.setLineWidth(0.6)
    canvas.roundRect(x, y, width, height, 3 * mm, stroke=1, fill=0)

    inner_x = x + padding
    inner_y = y + padding
    inner_w = width - 2 * padding
    inner_h = height - 2 * padding

    # Kopfzeile mit Foliennummer
    canvas.setFont("Helvetica-Bold", 7)
    canvas.setFillColor(colors.HexColor("#555555"))
    canvas.drawRightString(x + width - padding, y + height - padding - 7,
                           f"Folie {slide_number}")

    content_top = y + height - padding - 17
    available_h = content_top - inner_y
    if not blocks:
        blocks = [TextBlock("(Kein Text auf dieser Folie)")]

    # Explizite ODF-Überschriften bleiben Überschriften. Falls keine existiert,
    # wird der erste Absatz als Kartenüberschrift hervorgehoben.
    has_heading = any(b.heading for b in blocks)
    normalized = [
        TextBlock(b.text, b.heading or (not has_heading and i == 0), b.list_item)
        for i, b in enumerate(blocks)
    ]

    chosen = None
    chosen_size = font_min
    for size in range(font_max, font_min - 1, -1):
        heading_style, body_style = make_styles(size)
        prepared = []
        total = 0
        for block in normalized:
            style = heading_style if block.heading else body_style
            para = Paragraph(paragraph_markup(block), style)
            _, para_h = para.wrap(inner_w, available_h)
            prepared.append((para, para_h, style.spaceAfter))
            total += para_h + style.spaceAfter
        if total <= available_h:
            chosen = prepared
            chosen_size = size
            break
        chosen = prepared

    canvas.setFillColor(colors.black)
    cursor = content_top
    overflow = False
    for para, para_h, space_after in chosen:
        if cursor - para_h < inner_y:
            overflow = True
            break
        para.drawOn(canvas, inner_x, cursor - para_h)
        cursor -= para_h + space_after

    if overflow:
        canvas.setFillColor(colors.HexColor("#A00000"))
        canvas.setFont("Helvetica-Bold", 6.5)
        canvas.drawString(inner_x, inner_y + 1,
                          f"ACHTUNG: Text passt nicht vollständig (min. {font_min} pt).")
    else:
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.setFont("Helvetica", 6)
        canvas.drawRightString(x + width - padding, y + padding - 1,
                               f"{chosen_size} pt")

    canvas.restoreState()


def create_pdf(slides, output: Path, margin_mm=8, gap_mm=4, padding_mm=5,
               font_max=12, font_min=6):
    page_w, page_h = A4
    margin = margin_mm * mm
    gap = gap_mm * mm
    padding = padding_mm * mm
    card_w = (page_w - 2 * margin - gap) / 2
    card_h = (page_h - 2 * margin - gap) / 2

    positions = [
        (margin, margin + card_h + gap),
        (margin + card_w + gap, margin + card_h + gap),
        (margin, margin),
        (margin + card_w + gap, margin),
    ]

    canvas = None
    from reportlab.pdfgen import canvas as pdfcanvas
    canvas = pdfcanvas.Canvas(str(output), pagesize=A4)
    canvas.setTitle("Vorlesekarten")
    canvas.setAuthor("ODP-Vorlesekarten")

    for index, blocks in enumerate(slides):
        slot = index % 4
        x, y = positions[slot]
        render_card(canvas, x, y, card_w, card_h, blocks, index + 1,
                    padding, font_max, font_min)
        if slot == 3 or index == len(slides) - 1:
            canvas.showPage()

    if not slides:
        canvas.showPage()
    canvas.save()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Wandelt eine ODP-Präsentation in Vorlesekarten-PDF um."
    )
    parser.add_argument("input", nargs="?", default="praesentation.odp",
                        help="Eingabe-ODP (Standard: praesentation.odp)")
    parser.add_argument("-o", "--output", default="vorlesekarten.pdf",
                        help="Ausgabe-PDF (Standard: vorlesekarten.pdf)")
    parser.add_argument("--font-max", type=int, default=12)
    parser.add_argument("--font-min", type=int, default=6)
    parser.add_argument("--margin-mm", type=float, default=8)
    parser.add_argument("--gap-mm", type=float, default=4)
    parser.add_argument("--padding-mm", type=float, default=5)
    args = parser.parse_args()
    if not (1 <= args.font_min <= args.font_max <= 72):
        parser.error("Schriftgrößen müssen 1 <= font-min <= font-max <= 72 erfüllen.")
    if min(args.margin_mm, args.gap_mm, args.padding_mm) < 0:
        parser.error("Abstände dürfen nicht negativ sein.")
    return args


def main():
    args = parse_args()
    source = Path(args.input)
    output = Path(args.output)

    if not source.is_file():
        print(f"Fehler: Datei nicht gefunden: {source}", file=sys.stderr)
        return 2

    try:
        print(f"Lese ODP-Präsentation: {source}")
        slides = extract_slides(source)
        print(f"{len(slides)} Folien gefunden.")
        create_pdf(
            slides, output,
            margin_mm=args.margin_mm,
            gap_mm=args.gap_mm,
            padding_mm=args.padding_mm,
            font_max=args.font_max,
            font_min=args.font_min,
        )
        print(f"PDF erstellt: {output.resolve()}")
        return 0
    except zipfile.BadZipFile:
        print("Fehler: Die Datei ist keine gültige ODP/ZIP-Datei.", file=sys.stderr)
        return 2
    except ET.ParseError as exc:
        print(f"Fehler beim Lesen der ODP-XML: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

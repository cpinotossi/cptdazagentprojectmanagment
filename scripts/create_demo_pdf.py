"""Render editable plain text as a simple demo PDF, not a general PDF editor."""

import argparse
import textwrap
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def create_pdf(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve() or destination.suffix.lower() != ".pdf":
        raise ValueError("Choose a separate .pdf destination")
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("Demo PDF source must not be empty")
    text.encode("cp1252")
    lines = [line for paragraph in text.splitlines() for line in (textwrap.wrap(paragraph, 85) or [""])]
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    })
    font_ref = writer._add_object(font)
    for start in range(0, len(lines), 40):
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
        })
        commands = ["BT", "/F1 11 Tf", "45 790 Td", "18 TL"]
        for index, line in enumerate(lines[start:start + 40]):
            if index:
                commands.append("T*")
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({escaped}) Tj")
        stream = DecodedStreamObject()
        stream.set_data(("\n".join(commands) + "\nET").encode("cp1252"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": text.splitlines()[0]})
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".pdf.tmp")
    with temporary.open("wb") as handle:
        writer.write(handle)
    reader = PdfReader(temporary)
    if len(reader.pages) != (len(lines) + 39) // 40:
        raise RuntimeError("Generated demo PDF page count is incorrect")
    temporary.replace(destination)
    print(f"Created {destination} ({len(reader.pages)} pages) from {source}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    create_pdf(args.source, args.destination)


if __name__ == "__main__":
    main()

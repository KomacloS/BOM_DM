import fitz
import re


def extract_bom_text(pdf_bytes: bytes) -> str:
    """Extract all text from a PDF byte string using PyMuPDF."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        texts = [page.get_text() for page in doc]
    return "\n".join(texts)


def parse_bom_lines(text: str) -> list[dict]:
    """Parse raw BOM text into a list of dictionaries.

    This simplistic parser assumes columns are separated by multiple spaces or
    tabs. Lines not matching the expected pattern are skipped.
    """
    items = []
    for line in text.splitlines():
        # Collapse multiple tabs/spaces into single tab for easier split
        parts = re.split(r"\s{2,}|\t+", line.strip())
        if len(parts) < 3:
            continue
        part_number = parts[0].strip()
        description = parts[1].strip()
        qty_str = parts[2].strip()
        if not part_number or not description:
            continue
        try:
            quantity = int(re.match(r"\d+", qty_str).group())
        except Exception:
            continue
        reference = parts[3].strip() if len(parts) > 3 and parts[3].strip() else None
        items.append(
            {
                "part_number": part_number,
                "description": description,
                "quantity": quantity,
                "reference": reference,
            }
        )
    return items

"""Text-only PDF extraction; run with the isolated pypdf interpreter."""
import sys
from pypdf import PdfReader

reader = PdfReader(sys.argv[1])
for number, page in enumerate(reader.pages, 1):
    text = page.extract_text() or ''
    if text.strip():
        print(f'\n--- PDF page {number} ---\n{text}')

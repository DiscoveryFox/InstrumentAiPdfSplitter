# Instrument AI PDF Splitter

A lightweight Python tool that uses OpenAI to analyze multi-page sheet-music PDFs, detect instrument parts (including voice/desk numbers), determine their page ranges, and split the source PDF into one file per instrument/voice.

- AI-assisted part detection: extracts instruments, voice numbers, and 1-indexed start/end pages as strict JSON.
- Smart uploads: avoids re-uploading identical files via SHA-256 hashing.
- Reliable splitting: clamps page ranges, sanitizes filenames, and writes outputs using pypdf.
- Flexible input: use AI analysis or provide your own instrument list (InstrumentPart or JSON).
- Configurable model: via constructor or OPENAI_MODEL env var; requires an OpenAI API key.

## Installation

Replace PACKAGE_NAME with your actual PyPI name once published (e.g., instrument-ai-pdf-splitter).

```bash
pip install PACKAGE_NAME
```

Requirements:
- Python 3.10+
- openai (>= 1.0.0)
- pypdf
- dataclasses (builtin)
- typing, pathlib, etc. (builtin)

## Quickstart

```python
import os
import json
from instrument_ai_pdf_splitter import InstrumentAiPdfSplitter

# Set your OpenAI API key via env or pass directly
api_key = os.getenv("OPENAI_API_KEY")

splitter = InstrumentAiPdfSplitter(api_key=api_key)

# 1) Analyze the PDF to get instrument parts and page ranges
data = splitter.analyse("scores/book.pdf")
print(json.dumps(data, indent=2))

# Example output (JSON):
# {
#   "instruments": [
#     {"name": "Trumpet in Bb", "voice": "1", "start_page": 3, "end_page": 5},
#     {"name": "Alto Sax", "voice": null, "start_page": 6, "end_page": 9}
#   ]
# }

# 2) Split the PDF into one file per instrument/voice
results = splitter.split_pdf("scores/book.pdf")
for r in results:
    print(f"{r['name']} {r['voice']} -> {r['output_path']} [{r['start_page']}-{r['end_page']}]")
```

Output files are saved into a sibling directory named "<stem>_parts" by default (e.g., book_parts).

## Manual instrument data (no AI call)

You can skip analysis and provide parts manually, either as InstrumentPart instances or JSON-like dicts.

```python
from instrument_ai_pdf_splitter import InstrumentAiPdfSplitter, InstrumentPart

splitter = InstrumentAiPdfSplitter(api_key="YOUR_OPENAI_API_KEY")

parts = [
    InstrumentPart(name="Trumpet in Bb", voice="1", start_page=3, end_page=5),
    {"name": "Alto Sax", "voice": None, "start_page": 6, "end_page": 9},  # JSON-like dict also works
]

results = splitter.split_pdf(
    pdf_path="scores/book.pdf",
    instruments_data=parts,
    out_dir="output/parts"  # optional custom directory
)

for r in results:
    print(r)
```

## Configuration

- API key: Provide via constructor or set OPENAI_API_KEY in your environment.
- Model: Pass `model` to the constructor or set `OPENAI_MODEL`; defaults to "gpt-4.1".

```python
splitter = InstrumentAiPdfSplitter(api_key="...", model="gpt-4.1")
```

Note: Model availability depends on your OpenAI account. Use a model that supports the Responses API with file inputs. You will get the best results with gpt-5.

## How it works

- Content-hash uploads: Files are uploaded once per SHA-256; duplicates are skipped.
- AI analysis: The PDF and a strict prompt are sent to OpenAI; output is parsed as JSON.
- Splitting:
  - Ensures pages are 1-indexed and within document bounds.
  - Swaps start/end if reversed.
  - Sanitizes output filenames (removes unsafe characters).
  - Writes per-part PDFs using pypdf.

## Public API

| Item | Signature | Description |
|------|-----------|-------------|
| InstrumentPart | name: str; voice: Optional[str]; start_page: int; end_page: int | Dataclass representing a single instrument part with optional voice and 1-indexed inclusive page range. |
| InstrumentAiPdfSplitter.__init__ | (api_key: str, *, model: str | None = None) -> None | Initialize the splitter with OpenAI credentials and default prompt. |
| InstrumentAiPdfSplitter.analyse | (pdf_path: str) -> dict | Analyze a PDF and return instrument data as JSON {instruments: [...]}. |
| InstrumentAiPdfSplitter.is_file_already_uploaded | (pdf_path: str) -> Tuple[bool, str] | Check if a file (by SHA-256) is already uploaded; returns (True, file_id) or (False,). |
| InstrumentAiPdfSplitter.split_pdf | (pdf_path: str, instruments_data: List[InstrumentPart] | Dict[str, Any] | None = None, out_dir: Optional[str] = None) -> List[Dict[str, Any]] | Split the PDF per instrument/voice. Returns metadata with output_path. |
| InstrumentAiPdfSplitter.file_hash | (path: str) -> str | Compute SHA-256 hex digest of a file’s contents. |

## Error handling

- FileNotFoundError: Path doesn’t exist.
- ValueError: Not a file or not a .pdf.
- json.JSONDecodeError: If AI output isn’t valid JSON (rare; retry or adjust model).
- OpenAI errors: Network/auth/model issues are propagated from the OpenAI SDK.

## Tips for best results

- Use clear, well-structured PDFs with visible instrument headers or page titles.
- If AI is uncertain, manually provide `instruments_data` for precise splitting.
- Verify the model supports file inputs in your region/account.
- Handle sensitive material carefully; PDFs are uploaded to OpenAI for analysis.

## Example project structure

```text
scores/
├── book.pdf
output/
└── parts/
    ├── 01 - Trumpet in Bb 1.pdf
    ├── 02 - Alto Sax.pdf
    └── ...
```

## Development

```bash
# Clone and install locally
git clone REPO_URL
cd REPO_DIR
pip install -e .

# Run a quick test (adjust paths)
python -c "from instrument_ai_pdf_splitter import InstrumentAiPdfSplitter; import os; s=InstrumentAiPdfSplitter(api_key=os.getenv('OPENAI_API_KEY')); print(s.file_hash('scores/book.pdf'))"
```

## Versioning and compatibility

- Tested with Python 3.10+.
- Requires openai>=1.0.0 and pypdf. Keep dependencies updated.

## FAQ

- Does it require internet?
  - Yes, for AI analysis. Splitting runs locally.
- Can I prevent re-uploads?
  - Yes. The tool checks a SHA-256 content hash against your uploaded files.
- Is the output deterministic?
  - The JSON structure is deterministic; the content depends on model interpretation.

## License

Specify your license here (e.g., MIT). If you choose MIT, include a LICENSE file with the standard text.

## Acknowledgments

- Built with pypdf and the OpenAI Python SDK.
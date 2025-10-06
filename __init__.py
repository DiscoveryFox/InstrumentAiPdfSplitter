import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Tuple
import json
import os
import re
from pathlib import Path

from pypdf import PdfReader, PdfWriter
import openai


@dataclass
class InstrumentPart:
    """
    Dataclass representing a single instrument part with an optional voice/desk number and a 1-indexed inclusive page range.

    Attributes:
        name: Instrument name (e.g., 'Trumpet', 'Alto Sax', 'Clarinet in Bb').
        voice: Optional voice/desk identifier (e.g., '1', '2'); None if not applicable.
        start_page: First page where this part appears (1-indexed).
        end_page: Last page where this part appears (1-indexed).
    """
    name: str
    voice: Optional[str]
    start_page: int  # 1-indexed
    end_page: int    # 1-indexed


class InstrumentAiPdfSplitter:
    """
    Analyze a multi-page PDF of sheet music using OpenAI to detect instrument parts and their
    starting pages, then split the PDF into one file per instrument.

    Constructor accepts OpenAI credentials to keep usage flexible in different environments.
    """
    def __init__(
        self,
        api_key: str,
        *,
        model: str | None = None,
    ) -> None:
        """
        Initialize the PDF splitter that uses OpenAI to analyze multi-instrument scores.

        Args:
            api_key: OpenAI API key.
            model: Model name. Defaults to env var OPENAI_MODEL or 'gpt-4.1'.

        Sets up the OpenAI client and the analysis prompt.
        """

        self.api_key: str = api_key
        self.model: str = model or os.getenv("OPENAI_MODEL") or "gpt-4.1"
        self._client: openai.OpenAI = openai.OpenAI(
            api_key=self.api_key,
        )

        self.prompt: str = (
            "You are a music score analyzer. You are given a PDF of a multi-instrument score book. "
            "Identify every instrument part and both the FIRST and LAST page where that part appears. If a part includes "
            "a desk/voice number like '1.' or '2.' capture it as voice. Output strictly as JSON with this schema:\n"
            "{\n  \"instruments\": [\n    {\n      \"name\": string,        // e.g., 'Trumpet', 'Alto Sax', 'Clarinet in Bb'\n"
            "      \"voice\": string|null,   // e.g., '1', '2', '1.'; if absent, null\n"
            "      \"start_page\": number,   // 1-indexed page where that instrument's part begins\n"
            "      \"end_page\": number      // 1-indexed page where that instrument's part ends\n"
            "    }\n  ]\n}\n"
            "Rules: Include both first and last page per instrument/voice. Do not include duplicates. "
            "If you are unsure, best-effort guess from headers, titles, prominent instrument labels, or visual cues. "
            "Return JSON only."
        )

    def analyse(self, pdf_path: str):
        """Analyze a multi-page sheet-music PDF with OpenAI and return instrument parts.

        Validates the path, uploads the file once per content hash, calls the model with a structured prompt, and parses the JSON output.

        Args:
            pdf_path: Path to a .pdf file.

        Returns:
            dict: JSON object with key 'instruments' listing items {name, voice|null, start_page, end_page}, with pages 1-indexed.

        Raises:
            FileNotFoundError: If the path does not exist.
            ValueError: If the path is not a file or not a PDF.
            json.JSONDecodeError: If the model output is not valid JSON.
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"File not found: {pdf_path}")
        if not os.path.isfile(pdf_path):
            raise ValueError(f"Not a file: {pdf_path}")
        if not pdf_path.endswith(".pdf"):
            raise ValueError(f"Not a PDF file: {pdf_path}")

        if not self.is_file_already_uploaded(pdf_path):
            tmp_dir = tempfile.gettempdir()
            tmp_path = os.path.join(tmp_dir, f"{self.file_hash(pdf_path)}.pdf")
            shutil.copyfile(pdf_path, tmp_path)

            with open(tmp_path, "rb") as f:
                uploaded_file = self._client.files.create(
                    file=f,
                    purpose="assistants",
                )

            os.remove(tmp_path)
            file_id: str = uploaded_file.id
        else:
            file_id = self.is_file_already_uploaded(pdf_path)[1]

        # noinspection PyTypeChecker
        response = self._client.responses.create(
            model=self.model,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_file", "file_id": file_id},
                    {"type": "input_text", "text": self.prompt}
                ]
            }]
        )
        data = json.loads(response.output_text)

        return data

    def is_file_already_uploaded(self, pdf_path: str) -> Tuple[bool, str] | Tuple[bool]:
        """
        Check whether a file with the same SHA-256 hash is already uploaded to OpenAI.

                Args:
                    pdf_path: Local PDF path.

                Returns:
                    Tuple[bool, str] | Tuple[bool]: (True, file_id) if a matching upload exists; otherwise (False,).
        """
        files = self._client.files.list()
        metadata = [(file.id, file.filename.split(".pdf")[0]) for file in files]
        supplied_hash = self.file_hash(pdf_path)
        for file_id, file_hash in metadata:
            if supplied_hash == file_hash:
                return True, file_id
        return False,

    def split_pdf(self, pdf_path: str,
                  instruments_data: List[InstrumentPart] | Dict[str, Any] | None = None,
                  out_dir: Optional[str] = None) -> List[
        Dict[str, Any]]:
        """
        Split the source PDF into one file per instrument/voice.

        Uses provided instrument data or calls analyse() to obtain it, clamps page ranges to the document, and writes files to '<stem>_parts' or the given out_dir.

        Args:
            pdf_path: Path to the source PDF.
            instruments_data: List of InstrumentPart or dict with 'instruments'; if None, analyse() is invoked.
            out_dir: Output directory; defaults to a '<stem>_parts' sibling of the source.

        Returns:
            List[Dict[str, Any]]: Metadata per part: name, voice, start_page, end_page, output_path.

        Raises:
            FileNotFoundError: If the path does not exist.
            ValueError: If the path is not a file or not a PDF.
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"File not found: {pdf_path}")
        if not os.path.isfile(pdf_path):
            raise ValueError(f"Not a file: {pdf_path}")
        if not pdf_path.lower().endswith(".pdf"):
            raise ValueError(f"Not a PDF file: {pdf_path}")

        if instruments_data is None:
            analysed = self.analyse(pdf_path)
            parts_input = analysed.get("instruments", [])
        else:
            if isinstance(instruments_data, dict):
                parts_input = instruments_data.get("instruments", [])
            else:
                parts_input = instruments_data

        reader = PdfReader(pdf_path)
        total_pages = len(reader.pages)

        base = Path(pdf_path)
        if out_dir is None:
            out_dir = base.parent / f"{base.stem}_parts"
        else:
            out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        def sanitize(text: str) -> str:
            text = re.sub(r"[^\w\s.\-]+", "", text, flags=re.UNICODE)
            return re.sub(r"\s+", " ", text).strip()

        results: List[Dict[str, Any]] = []

        for idx, part in enumerate(parts_input, start=1):
            if isinstance(part, InstrumentPart):
                name = part.name
                voice = part.voice
                start_page = int(part.start_page)
                end_page = int(part.end_page)
            else:
                name = part.get("name")
                voice = part.get("voice")
                start_page = int(part.get("start_page"))
                end_page = int(part.get("end_page", start_page))

            if not name or start_page is None:
                continue

            if end_page is None:
                end_page = start_page
            if start_page > end_page:
                start_page, end_page = end_page, start_page

            start_page = max(1, min(start_page, total_pages))
            end_page = max(1, min(end_page, total_pages))

            writer = PdfWriter()
            for p in range(start_page - 1, end_page):
                writer.add_page(reader.pages[p])

            voice_suffix = f" {str(voice).strip()}" if voice not in (None, "", "null", "None") else ""
            safe_name = sanitize(f"{name}{voice_suffix}")
            out_path = out_dir / f"{idx:02d} - {safe_name}.pdf"

            with open(out_path, "wb") as f:
                writer.write(f)

            results.append({
                "name": name,
                "voice": voice,
                "start_page": start_page,
                "end_page": end_page,
                "output_path": str(out_path),
            })

        return results


    @staticmethod
    def file_hash(path):
        """Return the SHA-256 hex digest of a file's contents.

        Args:
            path: Filesystem path to the file.

        Returns:
            str: Hexadecimal digest of the file contents."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()


__all__ = ["InstrumentAiPdfSplitter", "InstrumentPart"]

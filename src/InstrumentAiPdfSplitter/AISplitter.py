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
    end_page: int  # 1-indexed


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
        self.model: str = model or os.getenv("OPENAI_MODEL") or "gpt-5"
        self._client: openai.OpenAI = openai.OpenAI(
            api_key=self.api_key,
        )

        self.prompt: str = (
            "You are a music score analyzer. You are given a PDF of a multi-instrument score book. "
            "Your task is to identify every instrument part and both the FIRST and LAST page where that part appears. "
            "If a part includes a desk or voice number such as '1.' or '2.', capture it as the 'voice'. "
            "Output strictly as JSON following this schema:\n"
            "{\n"
            '  "instruments": [\n'
            "    {\n"
            "      \"name\": string,        // e.g., 'Trumpet', 'Alto Sax', 'Clarinet in Bb'\n"
            "      \"voice\": string|null,   // e.g., '1', '2', '1.'; if absent, null\n"
            '      "start_page": number,   // 1-indexed page where that instrument\'s part begins\n'
            '      "end_page": number      // 1-indexed page where that instrument\'s part ends\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Rules:\n"
            "- Include both the first and last page for each unique instrument/voice combination.\n"
            "- Avoid duplicates.\n"
            "- Use clear visual or textual cues such as headers, instrument labels, and section titles.\n"
            "- A page is *more likely* to be the START page for an instrument if the title of a piece also appears on that page.\n"
            "- If uncertain, make a best-effort guess based on layout, typography, or recurring labeling patterns.\n"
            "Return JSON only — no explanations or extra text."
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

        if not self.is_file_already_uploaded(pdf_path)[0]:
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
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_file", "file_id": file_id},
                        {"type": "input_text", "text": self.prompt},
                    ],
                }
            ],
            reasoning={"effort": "high"},
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
        return (False,)

    def split_pdf(
        self,
        pdf_path: str,
        instruments_data: List[InstrumentPart] | Dict[str, Any] | None = None,
        out_dir: Optional[str] = None,
        *,
        return_files: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Split the source PDF into one file per instrument/voice.

        Uses provided instrument data or calls analyse() to obtain it, clamps page ranges to the document.
        By default, writes files to '<stem>_parts' (or the given out_dir). If return_files=True, nothing is
        written to disk and the split PDFs are returned as in-memory bytes instead.

        Args:
            pdf_path: Path to the source PDF.
            instruments_data: List of InstrumentPart or dict with 'instruments'; if None, analyse() is invoked.
            out_dir: Output directory; defaults to a '<stem>_parts' sibling of the source. Ignored when return_files=True.
            return_files: If True, do not write to disk; return each part as bytes along with a suggested filename.

        Returns:
            List[Dict[str, Any]]: For on-disk mode: name, voice, start_page, end_page, output_path.
            For in-memory mode (return_files=True): name, voice, start_page, end_page, filename, content (bytes).

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
        if not return_files:
            if out_dir is None:
                out_dir = base.parent / f"{base.stem}_parts"
            else:
                out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
        else:
            out_dir = None

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

            voice_suffix = (
                f" {str(voice).strip()}"
                if voice not in (None, "", "null", "None")
                else ""
            )
            safe_name = sanitize(f"{name}{voice_suffix}")
            filename = f"{idx:02d} - {safe_name}.pdf"

            if return_files:
                import io

                buf = io.BytesIO()
                writer.write(buf)
                content = buf.getvalue()
                results.append(
                    {
                        "name": name,
                        "voice": voice,
                        "start_page": start_page,
                        "end_page": end_page,
                        "filename": filename,
                        "content": content,
                    }
                )
            else:
                out_path = out_dir / filename
                with open(out_path, "wb") as f:
                    writer.write(f)
                results.append(
                    {
                        "name": name,
                        "voice": voice,
                        "start_page": start_page,
                        "end_page": end_page,
                        "output_path": str(out_path),
                    }
                )

        return results

    def analyse_and_split(
        self,
        pdf_path: str,
        out_dir: Optional[str] = None,
        *,
        return_files: bool = False,
    ) -> List[Dict[str, Any]]:
        """Convenience method: analyse() then split_pdf() for multi-voice PDFs.

        Args:
            pdf_path: Path to the source PDF.
            out_dir: Optional output directory for split files.
            return_files: If True, return the split PDFs as bytes instead of writing to disk.
        Returns:
            List[Dict[str, Any]]: Same structure as split_pdf().
        """
        analysed = self.analyse(pdf_path)
        return self.split_pdf(
            pdf_path,
            instruments_data=analysed,
            out_dir=out_dir,
            return_files=return_files,
        )

    def analyse_single_part(self, pdf_path: str) -> Dict[str, Any]:
        """Analyse a single-part PDF and extract instrument name and optional voice.

        This assumes the PDF contains exactly one instrument part. It returns a
        simple JSON object with the detected instrument name and voice (if any),
        along with start/end pages inferred from the document length.
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"File not found: {pdf_path}")
        if not os.path.isfile(pdf_path):
            raise ValueError(f"Not a file: {pdf_path}")
        if not pdf_path.lower().endswith(".pdf"):
            raise ValueError(f"Not a PDF file: {pdf_path}")

        # Determine page count locally for reliable start/end inference
        reader = PdfReader(pdf_path)
        total_pages = len(reader.pages)

        # Upload or reuse existing upload by content hash
        if not self.is_file_already_uploaded(pdf_path)[0]:
            tmp_dir = tempfile.gettempdir()
            tmp_path = os.path.join(tmp_dir, f"{self.file_hash(pdf_path)}.pdf")
            shutil.copyfile(pdf_path, tmp_path)
            with open(tmp_path, "rb") as f:
                uploaded_file = self._client.files.create(file=f, purpose="assistants")
            os.remove(tmp_path)
            file_id: str = uploaded_file.id
        else:
            file_id = self.is_file_already_uploaded(pdf_path)[1]

        single_part_prompt = (
            "You are a music score analyzer. You are given a PDF that contains a single instrument part. "
            "Identify the instrument name and any voice/desk number (e.g., '1', '2', '1.'), if present. "
            "Return strict JSON with this schema:\n"
            "{\n"
            "  \"name\": string,        // e.g., 'Trumpet in Bb', 'Alto Sax'\n"
            "  \"voice\": string|null   // e.g., '1', '2'; null if absent\n"
            "}\n"
            "Return JSON only — no explanations or extra text."
        )

        # noinspection PyTypeChecker
        response = self._client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_file", "file_id": file_id},
                        {"type": "input_text", "text": single_part_prompt},
                    ],
                }
            ],
            reasoning={"effort": "high"},
        )
        meta = json.loads(response.output_text)

        # Normalize and augment with inferred page range
        name = meta.get("name") if isinstance(meta, dict) else None
        voice = meta.get("voice") if isinstance(meta, dict) else None
        result = {
            "name": name,
            "voice": voice,
            "start_page": 1,
            "end_page": total_pages,
            "pages": total_pages,
        }
        return result

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

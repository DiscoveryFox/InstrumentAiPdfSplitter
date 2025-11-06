import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Tuple, Union
from werkzeug.datastructures import FileStorage
import json
import os
import re
from pathlib import Path
import urllib.request
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import statistics
import time

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


class FileSizeExceededError(Exception):
    """Raised when a file exceeds the maximum allowed size."""
    pass


class InstrumentAiPdfSplitter:
    """
    Analyze a multi-page PDF of sheet music using OpenAI to detect instrument parts and their
    starting pages, then split the PDF into one file per instrument.

    Constructor accepts OpenAI credentials to keep usage flexible in different environments.
    """
    
    MAX_FILE_SIZE_MB = 32
    MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

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
            model: Model name. Defaults to env var OPENAI_MODEL or 'gpt-5'.

        Sets up the OpenAI client and the analysis prompt.
        """

        self.api_key: str = api_key
        self.model: str = model or os.getenv("OPENAI_MODEL") or "gpt-5"
        self._client: openai.OpenAI = openai.OpenAI(
            api_key=self.api_key,
        )

        self.prompt: str = (
            "You are a music score analyzer that will be given a PDF of a multi-instrument score book. "
            "Your job is to identify every instrument part and the FIRST and LAST 1-indexed page where that part appears, "
            "and to output STRICTLY and ONLY valid JSON following this schema:\n"
            "{\n"
            '  \"instruments\": [\n'
            "    {\n"
            "      \"name\": string,        // e.g., 'Trumpet', 'Alto Sax', 'Clarinet in Bb', 'Conductor'\n"
            "      \"voice\": string|null,   // e.g., '1', '2', 'I', 'II', '1.'; if absent, null\n"
            "      \"start_page\": number,   // 1-indexed page where that instrument's part begins\n"
            "      \"end_page\": number      // 1-indexed page where that instrument's part ends\n"
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Important extraction & anti-hallucination rules (follow these exactly):\n"
            "1) EVIDENCE REQUIRED: Only include an instrument if you can point to at least one explicit, local textual or visual cue on one or more pages. "
            "Acceptable cues include: printed instrument headers (e.g., 'Clarinet', 'Klarinette', 'Cl.'), staff labels at the start/top of the page, section headers, "
            "page footers that name parts, or a page that clearly shows the instrument name together with musical notation. Do NOT invent instruments from context or assume unseen parts exist.\n"
            "2) START/END DEFINITION: The start_page is the first page where the instrument's part header or clear staff label appears. The end_page is the last page where that instrument's staff or label appears. If a part appears only on one page, start_page == end_page.\n"
            "3) CONTINUATIONS: If an instrument header repeats on continuation pages without a new header (for example only small 'Clarinet' at top of each system), treat the first occurrence as start and the final repeated occurrence as end. Do not register repeated headers on the same continuous part as multiple parts.\n"
            "4) VOICE/DESK NUMBERS: If a label includes desk/voice numbers (examples: '1.', '2', 'I', 'II', '1st', '2nd'), extract the numeric/roman indicator into the 'voice' field (normalize roman numerals to same string format, e.g., 'I' stays 'I'). If no voice is present, set voice to null.\n"
            "5) NAME NORMALIZATION: Always output instrument names in ENGLISH. Map common foreign names and abbreviations to English: e.g., 'Klarinette', 'Klar.' -> 'Clarinet'; 'Violino', 'Vln' -> 'Violin'; 'Trompete', 'Tpt' -> 'Trumpet'; 'Posaune' -> 'Trombone'; 'Fagott' -> 'Bassoon'; 'Horn' or 'Cor' -> 'Horn'; 'Partitur', 'Direktion', 'Direktionsstimme' -> 'Conductor' (name exactly 'Conductor'). If an instrument is transposing and labeled like 'Clarinet in Bb' or 'Clarinet in A', include the full 'Clarinet in Bb' as the name.\n"
            "6) ABBREVIATIONS: Recognize these common abbreviations and expand them to full English names when present: "
            "Fl, Flute; Ob, Oboe; Cl, Clarinet; B.Cl/Cl.Bb -> Clarinet in Bb; Bsn, Bassoon; Hn, Horn; Tpt/Tromp -> Trumpet; Tbn -> Trombone; Vln -> Violin; Vla -> Viola; Vc/Cello -> Cello; Cb -> Double Bass; Perc, Percussion; Timp/Timpani -> Timpani; Hrp -> Harp; Pf/Piano -> Piano; Org/Organ -> Organ.\n"
            "7) AVOID INFERRED ENTRIES: If only a composer's instrument list (e.g., front matter) names instruments but there is no per-page evidence of their parts, include them only if you also locate at least one page showing that part's staff or header.\n"
            "8) MINIMIZE FALSE POSITIVES: If you are uncertain whether a visible label belongs to an instrument part (e.g., a publisher note mentioning an instrument), prefer exclusion. Only include best-effort guesses when the page layout clearly indicates a part (staff lines with clef + a header or a dedicated part title).\n"
            "9) DEDUPLICATION: Treat same instrument + same voice as a single entry. If an instrument appears in two separate blocks (e.g., 'Trumpet 1' appears pages 2–10 and again pages 90–100 as a different edition), treat them as separate entries only if the header explicitly indicates a new, separate part (e.g., different movement title or a new 'Trumpet 1' section header). Otherwise merge into one start/end that spans first to last occurrence.\n"
            "10) TYPO/FOREIGN HANDLING: Recognize and normalize common foreign spellings (German, Italian, French) to English names. If the label is ambiguous between two instruments, prefer the one that matches standard score abbreviations and set voice to null.\n"
            "11) PAGE NUMBERING: Use the PDF's logical page order (the first page of the file is page 1). If the PDF includes Roman-numbered front matter, still count from the very first PDF page as page 1.\n"
            "12) ALWAYS validate: Before returning JSON, confirm that each listed instrument has at least one page index where either the instrument name or a staff labeled for it appears. Remove any instrument lacking this proof.\n"
            "13) OUTPUT constraint: Return ONLY the JSON object described above and nothing else (no commentary or extra fields). Ensure valid JSON types (string, null, number). Do not include any confidence or explanation fields — stick exactly to the schema.\n\n"
            "Heuristics for difficult cases (apply only when direct cues are scarce):\n"
            "- If an instrument name appears in a header next to a piece title on a page that also contains notation, treat that page as a START page.\n"
            "- If only abbreviated headers appear, expand them using the abbreviation map above.\n"
            "- If Roman numerals or ordinal words ('1st', '2nd') are used for desk numbers, capture them verbatim in 'voice'.\n\n"
            "If any rule conflicts, follow the explicit rules in the order presented above (EVIDENCE REQUIRED is primary)."
        )

    def _ensure_path(self, pdf_input: Union[str, FileStorage]) -> Tuple[str, bool]:
        """
        Ensure we have a filesystem path for the PDF.

        Returns (path, is_temp) where is_temp=True indicates the path is a temporary file
        created from a FileStorage and should be removed by the caller when done.
        
        Raises:
            FileSizeExceededError: If file size exceeds MAX_FILE_SIZE_BYTES.
        """
        if isinstance(pdf_input, str):
            # Check if it's a URL
            if pdf_input.startswith(('http://', 'https://')):
                # Download the file from URL
                tmp_dir = tempfile.gettempdir()
                tmp_path = os.path.join(tmp_dir, f"url_download_{hashlib.sha256(pdf_input.encode()).hexdigest()}.pdf")
                
                # Download with size check
                with urllib.request.urlopen(pdf_input) as response:
                    # Check content length header if available
                    content_length = response.getheader('Content-Length')
                    if content_length and int(content_length) > self.MAX_FILE_SIZE_BYTES:
                        raise FileSizeExceededError(
                            f"File size ({int(content_length) / (1024*1024):.2f} MB) exceeds "
                            f"maximum allowed size of {self.MAX_FILE_SIZE_MB} MB"
                        )
                    
                    # Download in chunks and check size
                    data = b""
                    chunk_size = 8192
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        data += chunk
                        if len(data) > self.MAX_FILE_SIZE_BYTES:
                            raise FileSizeExceededError(
                                f"File size exceeds maximum allowed size of {self.MAX_FILE_SIZE_MB} MB"
                            )
                
                with open(tmp_path, "wb") as f:
                    f.write(data)
                return tmp_path, True
            else:
                # Local file path - check size
                if os.path.exists(pdf_input):
                    file_size = os.path.getsize(pdf_input)
                    if file_size > self.MAX_FILE_SIZE_BYTES:
                        raise FileSizeExceededError(
                            f"File size ({file_size / (1024*1024):.2f} MB) exceeds "
                            f"maximum allowed size of {self.MAX_FILE_SIZE_MB} MB"
                        )
                return pdf_input, False

        # pdf_input is a FileStorage:
        # Read bytes, compute hash, write deterministic temp file named <hash>.pdf
        pdf_input.stream.seek(0)
        data = pdf_input.read()
        
        # Check file size
        if len(data) > self.MAX_FILE_SIZE_BYTES:
            raise FileSizeExceededError(
                f"File size ({len(data) / (1024*1024):.2f} MB) exceeds "
                f"maximum allowed size of {self.MAX_FILE_SIZE_MB} MB"
            )
        
        h = hashlib.sha256()
        h.update(data)
        digest = h.hexdigest()

        tmp_dir = tempfile.gettempdir()
        tmp_path = os.path.join(tmp_dir, f"{digest}.pdf")
        # Write only if not already present (avoid race overwrite)
        if not os.path.exists(tmp_path):
            with open(tmp_path, "wb") as f:
                f.write(data)
        return tmp_path, True

    def analyse(self, pdf_path: Union[str, FileStorage, None] = None, file_url: Optional[str] = None):
        """Analyze a multi-page sheet-music PDF with OpenAI and return instrument parts.

        Must provide either pdf_path OR file_url, but not both.
        
        Args:
            pdf_path: Filesystem path, URL string, or FileStorage object. Mutually exclusive with file_url.
            file_url: Direct file URL to pass to OpenAI responses API. Mutually exclusive with pdf_path.
        
        Raises:
            ValueError: If both or neither pdf_path and file_url are provided.
            FileSizeExceededError: If file size exceeds 32MB (when using pdf_path).
        """
        print("[DEBUG analyse] Starting analyse function")
        print(f"[DEBUG analyse] pdf_path: {pdf_path}")
        print(f"[DEBUG analyse] file_url: {file_url}")
        
        if (pdf_path is None and file_url is None) or (pdf_path is not None and file_url is not None):
            raise ValueError("Must provide either pdf_path or file_url, but not both")
        
        # Use file_url directly if provided
        if file_url:
            print("[DEBUG analyse] Using file_url path")
            print(f"[DEBUG analyse] Preparing content with file_url: {file_url}")
            content = [
                {"type": "input_file", "file_url": file_url},
                {"type": "input_text", "text": self.prompt},
            ]
            print("[DEBUG analyse] Content prepared for OpenAI API")
        else:
            print("[DEBUG analyse] Using pdf_path (file or FileStorage)")
            # Process pdf_path and upload to OpenAI
            print("[DEBUG analyse] Resolving path via _ensure_path()")
            path, is_temp = self._ensure_path(pdf_path)
            print(f"[DEBUG analyse] Path resolved: {path}, is_temp: {is_temp}")
            try:
                print("[DEBUG analyse] Validating file path")
                if not os.path.exists(path):
                    raise FileNotFoundError(f"File not found: {path}")
                if not os.path.isfile(path):
                    raise ValueError(f"Not a file: {path}")
                if not path.lower().endswith(".pdf"):
                    raise ValueError(f"Not a PDF file: {path}")
                print("[DEBUG analyse] File validation passed")
                
                file_size = os.path.getsize(path)
                print(f"[DEBUG analyse] File size: {file_size / (1024*1024):.2f} MB")

                print("[DEBUG analyse] Checking if file already uploaded to OpenAI")
                already = self.is_file_already_uploaded(path)
                if already and already[0]:
                    file_id = already[1]
                    print(f"[DEBUG analyse] File already uploaded to OpenAI, reusing file_id: {file_id}")
                else:
                    print("[DEBUG analyse] File not yet uploaded, preparing upload")
                    tmp_dir = tempfile.gettempdir()
                    file_hash = self.file_hash(path)
                    print(f"[DEBUG analyse] File hash: {file_hash}")
                    upload_tmp = os.path.join(tmp_dir, f"{file_hash}.pdf")
                    # If source and upload destination are the same file, don't copy (avoid SameFileError)
                    if os.path.abspath(path) != os.path.abspath(upload_tmp):
                        print(f"[DEBUG analyse] Copying file to temp location: {upload_tmp}")
                        shutil.copyfile(path, upload_tmp)
                        upload_from = upload_tmp
                        remove_after = True
                    else:
                        print("[DEBUG analyse] File already in temp location, using directly")
                        upload_from = path
                        remove_after = False

                    print(f"[DEBUG analyse] Uploading file to OpenAI: {upload_from}")
                    with open(upload_from, "rb") as f:
                        uploaded_file = self._client.files.create(
                            file=f,
                            purpose="assistants",
                        )
                    print(f"[DEBUG analyse] Upload complete, file_id: {uploaded_file.id}")

                    if remove_after:
                        try:
                            print("[DEBUG analyse] Removing temporary upload file")
                            os.remove(upload_tmp)
                        except Exception as e:
                            print(f"[DEBUG analyse] Failed to remove temp file: {e}")

                    file_id: str = uploaded_file.id
                
                print(f"[DEBUG analyse] Preparing content with file_id: {file_id}")
                content = [
                    {"type": "input_file", "file_id": file_id},
                    {"type": "input_text", "text": self.prompt},
                ]
                print("[DEBUG analyse] Content prepared for OpenAI API")
            finally:
                if is_temp:
                    try:
                        print(f"[DEBUG analyse] Removing temporary path file: {path}")
                        os.remove(path)
                    except Exception as e:
                        print(f"[DEBUG analyse] Failed to remove temp path: {e}")
        
        print(f"[DEBUG analyse] Making OpenAI API call with model: {self.model}")
        print(f"[DEBUG analyse] Using high reasoning effort")
        # noinspection PyTypeChecker
        response = self._client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "user",
                    "content": content,
                }
            ],
            reasoning={"effort": "high"},
        )
        print("[DEBUG analyse] OpenAI API response received")
        print(f"[DEBUG analyse] Response output_text length: {len(response.output_text)} chars")
        
        print("[DEBUG analyse] Parsing JSON response")
        data = json.loads(response.output_text)
        print(f"[DEBUG analyse] Parsed data keys: {list(data.keys())}")
        if 'instruments' in data:
            print(f"[DEBUG analyse] Found {len(data['instruments'])} instruments in response")
            for i, inst in enumerate(data['instruments'], 1):
                print(f"[DEBUG analyse]   Instrument {i}: {inst.get('name')} (voice: {inst.get('voice')}, pages: {inst.get('start_page')}-{inst.get('end_page')})")
        else:
            print("[DEBUG analyse] WARNING: No 'instruments' key found in response")
        
        print("[DEBUG analyse] Analysis complete, returning data")
        return data

    def is_file_already_uploaded(self, pdf_path: Union[str, FileStorage]) -> Tuple[bool, str] | Tuple[bool]:
        """
        Check whether a file with the same SHA-256 hash is already uploaded to OpenAI.

        Accepts a filesystem path or a FileStorage.
        """
        path, is_temp = self._ensure_path(pdf_path) if not isinstance(pdf_path, str) else (pdf_path, False)
        try:
            files = self._client.files.list()
            metadata = [(file.id, file.filename.split(".pdf")[0]) for file in files]
            supplied_hash = self.file_hash(path)
            for file_id, file_hash in metadata:
                if supplied_hash == file_hash:
                    return True, file_id
            return (False,)
        finally:
            if is_temp:
                try:
                    os.remove(path)
                except Exception:
                    pass

    def split_pdf(
        self,
        pdf_path: Union[str, FileStorage, None] = None,
        instruments_data: List[InstrumentPart] | Dict[str, Any] | None = None,
        out_dir: Optional[str] = None,
        *,
        return_files: bool = False,
        file_url: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Split the source PDF into one file per instrument/voice.

        Must provide either pdf_path OR file_url, but not both.
        
        Args:
            pdf_path: Filesystem path, URL, or FileStorage object. Mutually exclusive with file_url.
            instruments_data: Optional pre-analyzed instrument data.
            out_dir: Output directory for split files.
            return_files: If True, return file contents in memory instead of writing to disk.
            file_url: Direct file URL for analysis only (if instruments_data is None). Mutually exclusive with pdf_path.
        
        Raises:
            ValueError: If both or neither pdf_path and file_url are provided.
            FileSizeExceededError: If file size exceeds 32MB (when using pdf_path).
        """
        print("[DEBUG split_pdf] Starting split_pdf function")
        print(f"[DEBUG split_pdf] pdf_path: {pdf_path}")
        print(f"[DEBUG split_pdf] file_url: {file_url}")
        print(f"[DEBUG split_pdf] return_files: {return_files}")
        print(f"[DEBUG split_pdf] instruments_data provided: {instruments_data is not None}")
        
        if (pdf_path is None and file_url is None) or (pdf_path is not None and file_url is not None):
            raise ValueError("Must provide either pdf_path or file_url, but not both")
        
        # If file_url is provided, we need instruments_data to be provided as well
        # because we can't split without the actual PDF file
        if file_url:
            print("[DEBUG split_pdf] file_url provided, checking instruments_data")
            if instruments_data is None:
                # Analyze using file_url
                print("[DEBUG split_pdf] No instruments_data, analyzing file_url")
                analysed = self.analyse(file_url=file_url)
                instruments_data = analysed
            # We can't actually split without the PDF file
            raise ValueError("Cannot split PDF using only file_url. Please provide pdf_path to split the file.")
        
        print("[DEBUG split_pdf] Ensuring path from pdf_path")
        path, is_temp = self._ensure_path(pdf_path)
        print(f"[DEBUG split_pdf] Path resolved: {path}, is_temp: {is_temp}")
        try:
            print("[DEBUG split_pdf] Validating file path")
            if not os.path.exists(path):
                raise FileNotFoundError(f"File not found: {path}")
            if not os.path.isfile(path):
                raise ValueError(f"Not a file: {path}")
            if not path.lower().endswith(".pdf"):
                raise ValueError(f"Not a PDF file: {path}")
            print("[DEBUG split_pdf] File path validated successfully")

            if instruments_data is None:
                print("[DEBUG split_pdf] No instruments_data provided, calling analyse()")
                analysed = self.analyse(pdf_path=pdf_path)
                parts_input = analysed.get("instruments", [])
                print(f"[DEBUG split_pdf] Analysis complete, found {len(parts_input)} instruments")
            else:
                print("[DEBUG split_pdf] Using provided instruments_data")
                if isinstance(instruments_data, dict):
                    parts_input = instruments_data.get("instruments", [])
                else:
                    parts_input = instruments_data
                print(f"[DEBUG split_pdf] Extracted {len(parts_input)} instruments from data")

            print("[DEBUG split_pdf] Reading PDF file")
            reader = PdfReader(path)
            total_pages = len(reader.pages)
            print(f"[DEBUG split_pdf] PDF has {total_pages} total pages")

            base = Path(path)
            if not return_files:
                if out_dir is None:
                    out_dir = base.parent / f"{base.stem}_parts"
                else:
                    out_dir = Path(out_dir)
                print(f"[DEBUG split_pdf] Creating output directory: {out_dir}")
                out_dir.mkdir(parents=True, exist_ok=True)
                print("[DEBUG split_pdf] Output directory created/verified")
            else:
                print("[DEBUG split_pdf] return_files=True, files will be returned in memory")
                out_dir = None

            def sanitize(text: str) -> str:
                text = re.sub(r"[^\w\s.\-]+", "", text, flags=re.UNICODE)
                return re.sub(r"\s+", " ", text).strip()

            results: List[Dict[str, Any]] = []
            print(f"[DEBUG split_pdf] Starting to process {len(parts_input)} instrument parts")

            for idx, part in enumerate(parts_input, start=1):
                print(f"\n[DEBUG split_pdf] Processing part {idx}/{len(parts_input)}")
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
                
                print(f"[DEBUG split_pdf] Part data - name: {name}, voice: {voice}, start: {start_page}, end: {end_page}")

                if not name or start_page is None:
                    print(f"[DEBUG split_pdf] Skipping part - missing name or start_page")
                    continue

                if end_page is None:
                    end_page = start_page
                if start_page > end_page:
                    print(f"[DEBUG split_pdf] Swapping start/end pages: {start_page} <-> {end_page}")
                    start_page, end_page = end_page, start_page

                start_page = max(1, min(start_page, total_pages))
                end_page = max(1, min(end_page, total_pages))
                print(f"[DEBUG split_pdf] Clamped pages to valid range: {start_page}-{end_page}")

                print(f"[DEBUG split_pdf] Creating PDF writer for pages {start_page}-{end_page}")
                writer = PdfWriter()
                for p in range(start_page - 1, end_page):
                    writer.add_page(reader.pages[p])
                print(f"[DEBUG split_pdf] Added {end_page - start_page + 1} pages to writer")

                voice_suffix = (
                    f" {str(voice).strip()}"
                    if voice not in (None, "", "null", "None")
                    else ""
                )
                safe_name = sanitize(f"{name}{voice_suffix}")
                filename = f"{idx:02d} - {safe_name}.pdf"
                print(f"[DEBUG split_pdf] Generated filename: {filename}")

                if return_files:
                    print("[DEBUG split_pdf] Writing PDF to memory buffer")
                    import io

                    buf = io.BytesIO()
                    writer.write(buf)
                    content = buf.getvalue()
                    print(f"[DEBUG split_pdf] PDF written to buffer, size: {len(content)} bytes")
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
                    print(f"[DEBUG split_pdf] Writing PDF to file: {out_path}")
                    with open(out_path, "wb") as f:
                        writer.write(f)
                    file_size = os.path.getsize(out_path)
                    print(f"[DEBUG split_pdf] File written successfully, size: {file_size} bytes")
                    results.append(
                        {
                            "name": name,
                            "voice": voice,
                            "start_page": start_page,
                            "end_page": end_page,
                            "output_path": str(out_path),
                        }
                    )
            
            print(f"\n[DEBUG split_pdf] Split complete! Generated {len(results)} output files")
            return results
        finally:
            if is_temp:
                try:
                    os.remove(path)
                except Exception:
                    pass

    def analyse_and_split(
        self,
        pdf_path: Union[str, FileStorage, None] = None,
        out_dir: Optional[str] = None,
        *,
        return_files: bool = False,
        file_url: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Convenience method: analyse() then split_pdf() for multi-voice PDFs.

        Must provide either pdf_path OR file_url, but not both.
        Note: splitting requires pdf_path since we need to read the actual PDF pages.
        
        Args:
            pdf_path: Filesystem path, URL, or FileStorage object. Mutually exclusive with file_url.
            out_dir: Output directory for split files.
            return_files: If True, return file contents in memory.
            file_url: Direct file URL (not supported for this method). Mutually exclusive with pdf_path.
        
        Raises:
            ValueError: If both or neither pdf_path and file_url are provided, or if file_url is provided.
            FileSizeExceededError: If file size exceeds 32MB.
        """
        if (pdf_path is None and file_url is None) or (pdf_path is not None and file_url is not None):
            raise ValueError("Must provide either pdf_path or file_url, but not both")
        
        if file_url:
            raise ValueError("analyse_and_split requires pdf_path for splitting. Use analyse() if you only have file_url.")
        
        analysed = self.analyse(pdf_path=pdf_path)
        return self.split_pdf(
            pdf_path=pdf_path,
            instruments_data=analysed,
            out_dir=out_dir,
            return_files=return_files,
        )

    def analyse_single_part(self, pdf_path: Union[str, FileStorage, None] = None, file_url: Optional[str] = None) -> Dict[str, Any]:
        """Analyse a single-part PDF and extract instrument name and optional voice.

        Must provide either pdf_path OR file_url, but not both.
        
        Args:
            pdf_path: Filesystem path, URL, or FileStorage object. Mutually exclusive with file_url.
            file_url: Direct file URL to pass to OpenAI responses API. Mutually exclusive with pdf_path.
        
        Raises:
            ValueError: If both or neither pdf_path and file_url are provided.
            FileSizeExceededError: If file size exceeds 32MB (when using pdf_path).
        """
        if (pdf_path is None and file_url is None) or (pdf_path is not None and file_url is not None):
            raise ValueError("Must provide either pdf_path or file_url, but not both")
        
        single_part_prompt = (
            "You are a music score analyzer. You are given a PDF that contains a single instrument part. "
            "Identify the instrument name and any voice/desk number (e.g., '1', '2', '1.'), if present. "
            "Return strict JSON with this schema:\n"
            "{\n"
            "  \"name\": string,        // e.g., 'Trumpet in Bb', 'Alto Sax'\n"
            "  \"voice\": string|null   // e.g., '1', '2'; null if absent\n"
            "}\n"
            "IMPORTANT: Always return the instrument name in English (e.g., 'Clarinet' not 'Klarinette', 'Trumpet' not 'Trompete').\n"
            "Return JSON only — no explanations or extra text."
        )
        
        # Use file_url directly if provided
        if file_url:
            total_pages = None  # Can't determine without the file
            content = [
                {"type": "input_file", "file_url": file_url},
                {"type": "input_text", "text": single_part_prompt},
            ]
        else:
            # Process pdf_path and upload to OpenAI
            path, is_temp = self._ensure_path(pdf_path)
            try:
                if not os.path.exists(path):
                    raise FileNotFoundError(f"File not found: {path}")
                if not os.path.isfile(path):
                    raise ValueError(f"Not a file: {path}")
                if not path.lower().endswith(".pdf"):
                    raise ValueError(f"Not a PDF file: {path}")

                # Determine page count locally for reliable start/end inference
                reader = PdfReader(path)
                total_pages = len(reader.pages)

                already = self.is_file_already_uploaded(path)
                if already and already[0]:
                    file_id = already[1]
                else:
                    tmp_dir = tempfile.gettempdir()
                    upload_tmp = os.path.join(tmp_dir, f"{self.file_hash(path)}.pdf")
                    if os.path.abspath(path) != os.path.abspath(upload_tmp):
                        shutil.copyfile(path, upload_tmp)
                        upload_from = upload_tmp
                        remove_after = True
                    else:
                        upload_from = path
                        remove_after = False

                    with open(upload_from, "rb") as f:
                        uploaded_file = self._client.files.create(file=f, purpose="assistants")

                    if remove_after:
                        try:
                            os.remove(upload_tmp)
                        except Exception:
                            pass

                    file_id: str = uploaded_file.id
                    
                content = [
                    {"type": "input_file", "file_id": file_id},
                    {"type": "input_text", "text": single_part_prompt},
                ]
            finally:
                if is_temp:
                    try:
                        os.remove(path)
                    except Exception:
                        pass
            
        # noinspection PyTypeChecker
        response = self._client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "user",
                    "content": content,
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

    # ---------------------- Consensus helpers and APIs ----------------------
    def _normalize_name(self, name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        return re.sub(r"\s+", " ", str(name)).strip().lower()

    def _normalize_voice(self, voice: Optional[str]) -> Optional[str]:
        if voice in (None, "", "null", "None"):
            return None
        v = str(voice).strip()
        # drop trailing dots like '1.' -> '1'
        v = re.sub(r"\.$", "", v)
        return v if v else None

    def _call_openai_once(self, content: List[Dict[str, Any]]) -> Dict[str, Any]:
        # Single OpenAI responses.create call with basic retry on rate/timeout.
        last_err = None
        for attempt in range(3):
            try:
                response = self._client.responses.create(
                    model=self.model,
                    input=[{"role": "user", "content": content}],
                    reasoning={"effort": "high"},
                )
                return json.loads(response.output_text)
            except Exception as e:
                last_err = e
                # basic backoff
                time.sleep(1.5 * (attempt + 1))
        raise last_err

    def _aggregate_instruments(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        # Combine multiple analyse() results into a consensus set.
        buckets: Dict[Tuple[str, Optional[str]], Dict[str, Any]] = {}
        successes = 0
        for res in results:
            instruments = res.get("instruments", []) if isinstance(res, dict) else []
            if instruments:
                successes += 1
            for inst in instruments:
                name = inst.get("name")
                voice = inst.get("voice")
                sp = inst.get("start_page")
                ep = inst.get("end_page", sp)
                key = (self._normalize_name(name), self._normalize_voice(voice))
                if key not in buckets:
                    buckets[key] = {
                        "orig_names": Counter(),
                        "orig_voices": Counter(),
                        "starts": [],
                        "ends": [],
                        "count": 0,
                    }
                b = buckets[key]
                b["orig_names"][name or ""] += 1
                b["orig_voices"][voice or None] += 1
                if isinstance(sp, int):
                    b["starts"].append(int(sp))
                if isinstance(ep, int):
                    b["ends"].append(int(ep))
                b["count"] += 1

        out: List[Dict[str, Any]] = []
        if successes == 0:
            return {"instruments": out}

        threshold = (successes + 1) // 2  # ceil(successes/2)

        for key, agg in buckets.items():
            if agg["count"] < threshold:
                continue
            # majority/or mode selection with median fallback
            def pick_page(vals: List[int]) -> Optional[int]:
                if not vals:
                    return None
                cnt = Counter(vals)
                most = cnt.most_common()
                if len(most) == 0:
                    return None
                if len(most) == 1 or (len(most) > 1 and most[0][1] > most[1][1]):
                    return most[0][0]
                # tie -> median
                return int(round(statistics.median(vals)))

            start_page = pick_page(agg["starts"]) or (agg["starts"][0] if agg["starts"] else 1)
            end_page = pick_page(agg["ends"]) or start_page

            # choose the most common original casing for name/voice
            name_choice = (agg["orig_names"].most_common(1)[0][0] if agg["orig_names"] else None) or "Unknown"
            voice_choice = agg["orig_voices"].most_common(1)[0][0] if agg["orig_voices"] else None

            out.append({
                "name": name_choice,
                "voice": voice_choice,
                "start_page": int(start_page),
                "end_page": int(end_page),
            })
        return {"instruments": out}

    def analyse_consensus(
        self,
        pdf_path: Union[str, FileStorage, None] = None,
        *,
        file_url: Optional[str] = None,
        replicates: int = 3,
        progress_cb: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Run N parallel OpenAI analyses on the same input and aggregate results.
        Returns a dict with key 'instruments'.
        """
        if (pdf_path is None and file_url is None) or (pdf_path is not None and file_url is not None):
            raise ValueError("Must provide either pdf_path or file_url, but not both")
        if replicates <= 0:
            replicates = 1

        # Build shared content (upload once if needed)
        content: List[Dict[str, Any]]
        temp_cleanup_path: Optional[str] = None
        if file_url:
            content = [
                {"type": "input_file", "file_url": file_url},
                {"type": "input_text", "text": self.prompt},
            ]
        else:
            path, is_temp = self._ensure_path(pdf_path)
            try:
                if not os.path.exists(path) or not os.path.isfile(path) or not path.lower().endswith(".pdf"):
                    raise ValueError(f"Invalid PDF file: {path}")
                already = self.is_file_already_uploaded(path)
                if already and already[0]:
                    file_id = already[1]
                else:
                    # copy into temp named by hash to avoid SameFileError
                    tmp_dir = tempfile.gettempdir()
                    up_tmp = os.path.join(tmp_dir, f"{self.file_hash(path)}.pdf")
                    if os.path.abspath(path) != os.path.abspath(up_tmp):
                        shutil.copyfile(path, up_tmp)
                        upload_from = up_tmp
                        temp_cleanup_path = up_tmp
                    else:
                        upload_from = path
                    with open(upload_from, "rb") as f:
                        uploaded_file = self._client.files.create(file=f, purpose="assistants")
                    file_id = uploaded_file.id
                content = [
                    {"type": "input_file", "file_id": file_id},
                    {"type": "input_text", "text": self.prompt},
                ]
            finally:
                if is_temp:
                    try:
                        os.remove(path)
                    except Exception:
                        pass

        # Fire N requests in parallel, track progress
        print(f"[DEBUG analyse_consensus] Launching {replicates} parallel OpenAI analyses")
        done = 0
        results: List[Dict[str, Any]] = []
        errors: List[str] = []
        if progress_cb:
            try:
                progress_cb(done, replicates)
            except Exception:
                pass
        with ThreadPoolExecutor(max_workers=replicates) as ex:
            futs = [ex.submit(self._call_openai_once, content) for _ in range(replicates)]
            for fut in as_completed(futs):
                try:
                    res = fut.result()
                    if isinstance(res, dict):
                        results.append(res)
                        print(f"[DEBUG analyse_consensus] Run {done+1}/{replicates} completed successfully")
                except Exception as e:
                    errors.append(str(e))
                    print(f"[DEBUG analyse_consensus] Run {done+1}/{replicates} failed: {str(e)[:100]}")
                finally:
                    done += 1
                    if progress_cb:
                        try:
                            progress_cb(done, replicates)
                        except Exception:
                            pass
        if temp_cleanup_path:
            try:
                os.remove(temp_cleanup_path)
            except Exception:
                pass

        if not results:
            print(f"[DEBUG analyse_consensus] All {replicates} analyses failed")
            raise RuntimeError(f"All OpenAI analyses failed: {errors[:3]}")
        
        print(f"[DEBUG analyse_consensus] {len(results)}/{replicates} analyses succeeded, aggregating...")
        aggregated = self._aggregate_instruments(results)
        print(f"[DEBUG analyse_consensus] Aggregation complete: {len(aggregated.get('instruments', []))} instrument(s) after consensus")
        return aggregated

    def analyse_single_part_consensus(
        self,
        pdf_path: Union[str, FileStorage, None] = None,
        *,
        file_url: Optional[str] = None,
        replicates: int = 3,
        progress_cb: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Run N parallel detections for a single-part PDF and return majority instrument/voice.
        """
        if (pdf_path is None and file_url is None) or (pdf_path is not None and file_url is not None):
            raise ValueError("Must provide either pdf_path or file_url, but not both")
        if replicates <= 0:
            replicates = 1

        single_part_prompt = (
            "You are a music score analyzer. You are given a PDF that contains a single instrument part. "
            "Identify the instrument name and any voice/desk number (e.g., '1', '2', '1.'), if present. "
            "Return strict JSON with this schema:\n"
            "{\n"
            "  \"name\": string,\n"
            "  \"voice\": string|null\n"
            "}\n"
            "IMPORTANT: Always return the instrument name in English (e.g., 'Clarinet' not 'Klarinette', 'Trumpet' not 'Trompete').\n"
            "Return JSON only — no explanations or extra text."
        )

        # Build shared content
        content: List[Dict[str, Any]]
        total_pages: Optional[int] = None
        temp_cleanup_path: Optional[str] = None
        if file_url:
            content = [
                {"type": "input_file", "file_url": file_url},
                {"type": "input_text", "text": single_part_prompt},
            ]
        else:
            path, is_temp = self._ensure_path(pdf_path)
            try:
                if not os.path.exists(path) or not os.path.isfile(path) or not path.lower().endswith(".pdf"):
                    raise ValueError(f"Invalid PDF file: {path}")
                try:
                    reader = PdfReader(path)
                    total_pages = len(reader.pages)
                except Exception:
                    total_pages = None
                already = self.is_file_already_uploaded(path)
                if already and already[0]:
                    file_id = already[1]
                else:
                    tmp_dir = tempfile.gettempdir()
                    up_tmp = os.path.join(tmp_dir, f"{self.file_hash(path)}.pdf")
                    if os.path.abspath(path) != os.path.abspath(up_tmp):
                        shutil.copyfile(path, up_tmp)
                        upload_from = up_tmp
                        temp_cleanup_path = up_tmp
                    else:
                        upload_from = path
                    with open(upload_from, "rb") as f:
                        uploaded_file = self._client.files.create(file=f, purpose="assistants")
                    file_id = uploaded_file.id
                content = [
                    {"type": "input_file", "file_id": file_id},
                    {"type": "input_text", "text": single_part_prompt},
                ]
            finally:
                if is_temp:
                    try:
                        os.remove(path)
                    except Exception:
                        pass

        # Parallel calls
        print(f"[DEBUG analyse_single_part_consensus] Launching {replicates} parallel OpenAI detections")
        done = 0
        outputs: List[Dict[str, Any]] = []
        errors: List[str] = []
        if progress_cb:
            try:
                progress_cb(done, replicates)
            except Exception:
                pass
        with ThreadPoolExecutor(max_workers=replicates) as ex:
            futs = [ex.submit(self._call_openai_once, content) for _ in range(replicates)]
            for fut in as_completed(futs):
                try:
                    res = fut.result()
                    if isinstance(res, dict):
                        outputs.append(res)
                        print(f"[DEBUG analyse_single_part_consensus] Run {done+1}/{replicates} completed successfully")
                except Exception as e:
                    errors.append(str(e))
                    print(f"[DEBUG analyse_single_part_consensus] Run {done+1}/{replicates} failed: {str(e)[:100]}")
                finally:
                    done += 1
                    if progress_cb:
                        try:
                            progress_cb(done, replicates)
                        except Exception:
                            pass
        if temp_cleanup_path:
            try:
                os.remove(temp_cleanup_path)
            except Exception:
                pass

        if not outputs:
            print(f"[DEBUG analyse_single_part_consensus] All {replicates} detections failed")
            raise RuntimeError(f"All OpenAI detections failed: {errors[:3]}")
        
        print(f"[DEBUG analyse_single_part_consensus] {len(outputs)}/{replicates} detections succeeded, computing majority...")
        # Majority vote name/voice
        name_counts = Counter()
        voice_counts = Counter()
        for o in outputs:
            n = o.get("name") if isinstance(o, dict) else None
            v = o.get("voice") if isinstance(o, dict) else None
            if n:
                name_counts[n] += 1
            # treat None distinctly
            voice_counts[v] += 1
        name = name_counts.most_common(1)[0][0] if name_counts else "Unknown"
        voice = voice_counts.most_common(1)[0][0] if voice_counts else None
        print(f"[DEBUG analyse_single_part_consensus] Majority result: {name} (voice: {voice})")

        return {
            "name": name,
            "voice": voice,
            "start_page": 1,
            "end_page": total_pages if total_pages else 1,
            "pages": total_pages,
        }

    @staticmethod
    def normalize_orientation(
        pdf_path: Union[str, FileStorage],
        output_path: Optional[str] = None,
        threshold_percent: float = 60.0,
    ) -> str:
        """Normalize all pages in a PDF to either landscape or portrait orientation.
        
        Analyzes the orientation of all pages. If more than threshold_percent of pages
        are in landscape mode, converts all pages to landscape. Otherwise, converts
        all pages to portrait.
        
        Args:
            pdf_path: Filesystem path or FileStorage object for the input PDF.
            output_path: Optional output path. If None, creates a temp file.
            threshold_percent: Percentage threshold for landscape determination (default: 60.0).
        
        Returns:
            str: Path to the normalized PDF file.
        """
        print(f"[DEBUG normalize_orientation] Starting orientation normalization")
        print(f"[DEBUG normalize_orientation] threshold_percent: {threshold_percent}%")
        
        # Ensure we have a filesystem path
        if isinstance(pdf_path, str):
            path = pdf_path
            is_temp_input = False
        else:
            # FileStorage - save to temp
            pdf_path.stream.seek(0)
            data = pdf_path.read()
            h = hashlib.sha256()
            h.update(data)
            digest = h.hexdigest()
            tmp_dir = tempfile.gettempdir()
            path = os.path.join(tmp_dir, f"{digest}_input.pdf")
            with open(path, "wb") as f:
                f.write(data)
            is_temp_input = True
        
        try:
            # Read the PDF
            print(f"[DEBUG normalize_orientation] Reading PDF from: {path}")
            reader = PdfReader(path)
            total_pages = len(reader.pages)
            print(f"[DEBUG normalize_orientation] Total pages: {total_pages}")
            
            # Analyze orientations
            landscape_count = 0
            portrait_count = 0
            
            for i, page in enumerate(reader.pages):
                # Get page dimensions
                box = page.mediabox
                width = float(box.width)
                height = float(box.height)
                
                # Check orientation
                if width > height:
                    landscape_count += 1
                    orientation = "landscape"
                else:
                    portrait_count += 1
                    orientation = "portrait"
                
                if i < 5:  # Log first 5 pages
                    print(f"[DEBUG normalize_orientation] Page {i+1}: {width}x{height} ({orientation})")
            
            landscape_percent = (landscape_count / total_pages) * 100 if total_pages > 0 else 0
            print(f"[DEBUG normalize_orientation] Landscape: {landscape_count}/{total_pages} ({landscape_percent:.1f}%)")
            print(f"[DEBUG normalize_orientation] Portrait: {portrait_count}/{total_pages} ({100-landscape_percent:.1f}%)")
            
            # Determine target orientation
            target_landscape = landscape_percent > threshold_percent
            target_orientation = "landscape" if target_landscape else "portrait"
            print(f"[DEBUG normalize_orientation] Target orientation: {target_orientation}")
            
            # Create output PDF
            writer = PdfWriter()
            
            for i, page in enumerate(reader.pages):
                box = page.mediabox
                width = float(box.width)
                height = float(box.height)
                is_landscape = width > height
                
                # Rotate if needed
                if target_landscape and not is_landscape:
                    # Need to rotate to landscape (portrait -> landscape)
                    page.rotate(90)
                    if i < 3:
                        print(f"[DEBUG normalize_orientation] Page {i+1}: rotating to landscape")
                elif not target_landscape and is_landscape:
                    # Need to rotate to portrait (landscape -> portrait)
                    page.rotate(90)
                    if i < 3:
                        print(f"[DEBUG normalize_orientation] Page {i+1}: rotating to portrait")
                
                writer.add_page(page)
            
            # Write output
            if output_path is None:
                tmp_dir = tempfile.gettempdir()
                h = hashlib.sha256()
                h.update(path.encode('utf-8'))
                h.update(str(time.time()).encode('utf-8'))
                output_path = os.path.join(tmp_dir, f"normalized_{h.hexdigest()[:16]}.pdf")
            
            print(f"[DEBUG normalize_orientation] Writing normalized PDF to: {output_path}")
            with open(output_path, "wb") as f:
                writer.write(f)
            
            file_size = os.path.getsize(output_path)
            print(f"[DEBUG normalize_orientation] Normalization complete, output size: {file_size / 1024:.1f} KB")
            
            return output_path
        
        finally:
            if is_temp_input:
                try:
                    os.remove(path)
                except Exception as e:
                    print(f"[DEBUG normalize_orientation] Failed to remove temp input: {e}")

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

# File Upload System Changes

## Overview
The file upload system has been updated to:
1. Validate file size (32MB limit) and throw `FileSizeExceededError` if exceeded
2. Support `file_url` parameter as an alternative to uploading files
3. Enforce mutually exclusive usage of `pdf_path` and `file_url`

## Key Features

### 1. File Size Validation (32MB Limit)
- **New Exception**: `FileSizeExceededError` - raised when file exceeds 32MB
- **Constants**: 
  - `MAX_FILE_SIZE_MB = 32`
  - `MAX_FILE_SIZE_BYTES = 32 * 1024 * 1024`
- **Validation Points**:
  - Local files: checked via `os.path.getsize()`
  - URL downloads: checked via Content-Length header and during chunked download
  - FileStorage: checked after reading data

### 2. File URL Support
- All methods now accept optional `file_url` parameter
- When provided, passes directly to OpenAI responses API as: `{"type": "input_file", "file_url": url}`
- No file upload or processing occurs when using `file_url`

### 3. Mutually Exclusive Parameters
- Methods accept **either** `pdf_path` **or** `file_url`, but **not both**
- Raises `ValueError` if:
  - Both parameters are provided
  - Neither parameter is provided

## Method Behavior

### `analyse(pdf_path=None, file_url=None)`
✅ **Supports both**: Can use either `pdf_path` or `file_url`
- With `pdf_path`: Validates size, uploads file, analyzes
- With `file_url`: Passes URL directly to API for analysis

**Example:**
```python
# Using local file
result = splitter.analyse(pdf_path="score.pdf")

# Using file URL
result = splitter.analyse(file_url="https://example.com/score.pdf")

# Error: both provided
result = splitter.analyse(pdf_path="score.pdf", file_url="https://...")  # ValueError
```

### `analyse_single_part(pdf_path=None, file_url=None)`
✅ **Supports both**: Can use either `pdf_path` or `file_url`
- With `pdf_path`: Validates size, uploads file, determines page count, analyzes
- With `file_url`: Passes URL directly to API (page count will be None)

**Example:**
```python
# Using local file
result = splitter.analyse_single_part(pdf_path="part.pdf")

# Using file URL  
result = splitter.analyse_single_part(file_url="https://example.com/part.pdf")
```

### `split_pdf(pdf_path=None, file_url=None, ...)`
⚠️ **Requires pdf_path**: Cannot split using only `file_url`
- Needs actual PDF file to read pages for splitting
- If `file_url` is provided, raises `ValueError` explaining that `pdf_path` is required

**Example:**
```python
# Correct usage
result = splitter.split_pdf(pdf_path="score.pdf")

# Error: can't split without the file
result = splitter.split_pdf(file_url="https://...")  # ValueError
```

### `analyse_and_split(pdf_path=None, file_url=None, ...)`
⚠️ **Requires pdf_path**: Cannot split using only `file_url`
- Needs actual PDF file to split pages
- If `file_url` is provided, raises `ValueError`

**Example:**
```python
# Correct usage
result = splitter.analyse_and_split(pdf_path="score.pdf")

# Error: can't split without the file
result = splitter.analyse_and_split(file_url="https://...")  # ValueError
```

## Error Handling

### FileSizeExceededError
```python
try:
    splitter.analyse(pdf_path="large_file.pdf")
except FileSizeExceededError as e:
    print(f"File too large: {e}")
    # Output: File size (45.32 MB) exceeds maximum allowed size of 32 MB
```

### ValueError (Mutually Exclusive Parameters)
```python
try:
    splitter.analyse(pdf_path="file.pdf", file_url="https://...")
except ValueError as e:
    print(f"Invalid parameters: {e}")
    # Output: Must provide either pdf_path or file_url, but not both
```

## Migration Guide

### Before
```python
# Only supported pdf_path
splitter.analyse(pdf_path="file.pdf")
splitter.analyse_single_part(pdf_path="part.pdf")
```

### After
```python
# Still works - no breaking changes
splitter.analyse(pdf_path="file.pdf")
splitter.analyse_single_part(pdf_path="part.pdf")

# New: file_url support
splitter.analyse(file_url="https://files.openai.com/...")
splitter.analyse_single_part(file_url="https://files.openai.com/...")

# New: automatic size validation
try:
    splitter.analyse(pdf_path="huge_file.pdf")  # Raises if > 32MB
except FileSizeExceededError:
    print("File too large")
```

## Implementation Details

### URL Download Support
- URLs starting with `http://` or `https://` are automatically downloaded
- Size validation occurs during download (chunked reading)
- Downloaded files stored in temp directory

### OpenAI API Integration
- `file_url` passed as: `{"type": "input_file", "file_url": url}`
- `file_id` passed as: `{"type": "input_file", "file_id": id}`
- Content structure remains consistent with OpenAI responses API

## Testing

```python
from InstrumentAiPdfSplitter import InstrumentAiPdfSplitter, FileSizeExceededError

splitter = InstrumentAiPdfSplitter(api_key="...")

# Test 1: File size validation
try:
    splitter.analyse(pdf_path="large_file.pdf")
except FileSizeExceededError as e:
    print(f"✓ Size validation works: {e}")

# Test 2: file_url usage
result = splitter.analyse(file_url="https://example.com/score.pdf")
print(f"✓ file_url analysis: {result}")

# Test 3: Mutually exclusive check
try:
    splitter.analyse(pdf_path="a.pdf", file_url="https://b.pdf")
except ValueError as e:
    print(f"✓ Mutual exclusion enforced: {e}")

# Test 4: Split requires file
try:
    splitter.split_pdf(file_url="https://example.com/score.pdf")
except ValueError as e:
    print(f"✓ Split validation works: {e}")
```

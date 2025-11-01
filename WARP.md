# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

InstrumentAiPdfSplitter is a Python package that uses OpenAI's API to analyze multi-page sheet music PDFs, detect instrument parts with voice/desk numbers, and split them into separate files. The package is built around a single main class `InstrumentAiPdfSplitter` that handles AI analysis and PDF manipulation.

## Common Development Commands

### Environment Setup
```bash
# Install dependencies using uv (preferred package manager)
uv sync

# Or install in development mode with pip
pip install -e .

# Install development dependencies
uv sync --group dev
```

### Testing
```bash
# Run tests using pytest
uv run pytest

# Run tests with environment variables loaded
uv run pytest --envfile=.env

# Run a specific test if tests are added later
uv run pytest tests/test_specific.py
```

### Code Quality
```bash
# Format and lint code using ruff (configured in pyproject.toml)
uv run ruff check .

# Auto-fix linting issues
uv run ruff check --fix .

# Format code
uv run ruff format .
```

### Package Building
```bash
# Build the package for distribution
uv build

# Install locally for testing
uv pip install -e .
```

### Development Testing
```bash
# Quick test of core functionality (requires OPENAI_API_KEY)
uv run python -c "from InstrumentAiPdfSplitter import InstrumentAiPdfSplitter; import os; s=InstrumentAiPdfSplitter(api_key=os.getenv('OPENAI_API_KEY')); print('Package loaded successfully')"
```

## Architecture Overview

### Core Components

**Main Class: `InstrumentAiPdfSplitter`**
- Located in `src/InstrumentAiPdfSplitter/AISplitter.py`
- Handles OpenAI API interactions, file upload management, and PDF splitting
- Uses content-based hashing (SHA-256) to avoid duplicate file uploads
- Supports both AI-assisted analysis and manual instrument specification

**Data Model: `InstrumentPart`**
- Dataclass representing an instrument part with name, optional voice, and page range
- All page numbers are 1-indexed and inclusive
- Supports both `InstrumentPart` objects and dict-based input

### Key Methods

1. **`analyse(pdf_path)`** - AI analysis of multi-instrument scores
2. **`analyse_single_part(pdf_path)`** - Analysis of single instrument parts
3. **`split_pdf(pdf_path, instruments_data, out_dir, return_files)`** - PDF splitting functionality
4. **`analyse_and_split(pdf_path)`** - Convenience method combining analysis and splitting

### File Upload Optimization
The package implements smart file upload management:
- Files are hashed using SHA-256 before upload
- Duplicate files (same content) are not re-uploaded
- Upload metadata is tracked via OpenAI file IDs

### AI Integration
- Uses OpenAI's Responses API with structured JSON output
- Supports file input for PDF analysis  
- Configurable model selection (defaults to "gpt-5")
- High reasoning effort setting for better accuracy

## Environment Configuration

### Required Environment Variables
```bash
# OpenAI API key (required)
OPENAI_API_KEY=your_api_key_here

# Optional: Override default model
OPENAI_MODEL=gpt-4o
```

### Python Requirements
- Python 3.10+ (specified in pyproject.toml)
- Core dependencies: openai>=1.40.0, pypdf>=5.0.0
- Testing: pytest>=8.0.0, pytest-dotenv>=0.5.2
- Development: ruff>=0.14.0

## Project Structure

```
src/InstrumentAiPdfSplitter/
├── __init__.py          # Package exports
└── AISplitter.py        # Main implementation

pyproject.toml           # Project configuration and dependencies
README.md               # Comprehensive usage documentation
uv.lock                 # Dependency lock file (uv package manager)
```

## Development Guidelines

### Package Management
This project uses `uv` as the primary package manager. The `uv.lock` file ensures reproducible builds. When adding dependencies, use:
```bash
uv add package_name
uv add --group dev package_name  # for development dependencies
```

### Code Style
- Uses `ruff` for both linting and formatting
- Configuration is specified in `pyproject.toml`
- Follow existing patterns for AI prompt construction and JSON handling

### Error Handling
The package implements comprehensive error handling for:
- File validation (existence, type, PDF format)
- OpenAI API errors (network, authentication, model availability)
- JSON parsing errors from AI responses
- PDF processing errors

### Testing Strategy
Currently no test suite exists, but when adding tests:
- Use pytest with dotenv plugin for environment variable loading
- Mock OpenAI API calls to avoid costs during testing
- Test both successful analysis and error conditions
- Verify PDF splitting accuracy with sample files

### API Key Security
- Never commit API keys to version control
- Use environment variables or secure configuration
- The package supports both constructor-level and environment variable configuration

## Common Issues and Solutions

### Model Availability
- The default model "gpt-5" may not be available to all OpenAI accounts
- Override with `OPENAI_MODEL` environment variable or constructor parameter
- Ensure chosen model supports the Responses API and file inputs

### File Upload Limits
- OpenAI has file size and type restrictions
- The package handles temporary file creation for uploads
- Large PDFs may need chunking or compression before analysis

### PDF Processing
- Uses pypdf for reliable PDF manipulation
- Page ranges are automatically clamped to document boundaries
- Filename sanitization prevents filesystem issues
# Testing InstrumentAiPdfSplitter from PyPI

This directory contains a test script to verify the package can be installed and imported from PyPI.

## Running the Test

To run the PyPI installation test:

```bash
python test_pypi_install.py
```

## What the Test Does

The test script performs the following checks:

1. **Uninstalls local version**: Ensures no local development version interferes with the test
2. **Installs from PyPI**: Downloads and installs the package from PyPI (with fallback to direct wheel download)
3. **Tests import**: Verifies that `InstrumentAiPdfSplitter` and `InstrumentPart` can be imported
4. **Tests error handling**: Creates an instance with an invalid API key and verifies proper error handling

## Expected Output

A successful test run will show:

```
================================================================================
Testing InstrumentAiPdfSplitter from PyPI
================================================================================

1. Uninstalling any existing local version...
2. Installing from PyPI...
3. Testing import from PyPI package...
4. Testing error handling with wrong API key...

================================================================================
All tests completed successfully!
================================================================================

Test Summary:
  ✓ Successfully installed instrumentaipdfsplitter from PyPI
  ✓ Successfully imported InstrumentAiPdfSplitter and InstrumentPart
  ✓ Successfully created instance with invalid API key
  ✓ Confirmed error handling works with invalid API key
================================================================================
```

## Requirements

- Python 3.10+
- Internet connection (to download from PyPI)
- pip

## Exit Codes

- `0`: All tests passed
- `1`: One or more tests failed

## Troubleshooting

If the test fails to install from PyPI due to network issues, the script will automatically attempt to download the wheel file directly from PyPI's CDN.

If you encounter other issues, please check:
- Your internet connection
- That you're using Python 3.10 or higher
- That pip is properly installed and configured

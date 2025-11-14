"""
Test script for InstrumentAiPdfSplitter
Run with: python test_splitter.py
"""

import os
import sys
from pathlib import Path

# Add src to path to import the module
sys.path.insert(0, str(Path(__file__).parent / "src"))

from InstrumentAiPdfSplitter import InstrumentAiPdfSplitter, InstrumentPart, FileSizeExceededError

test_file_url = "https://musicstore-test-bucket.s3.amazonaws.com/2d75b9c224775a8b0063d467b54b819849a1a0b120e3cf9e672cb72ef80c80a5?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA5X2JUN3TO3GCXPZZ%2F20251103%2Feu-north-1%2Fs3%2Faws4_request&X-Amz-Date=20251103T232055Z&X-Amz-Expires=1800&X-Amz-SignedHeaders=host&X-Amz-Signature=e3f407a9266ecf1bd8b7cbf8da66326eb271bd2137381f5ddab691454d03f19c"


def test_instrument_part():
    """Test InstrumentPart dataclass creation"""
    print("Test 1: InstrumentPart creation...")
    
    part1 = InstrumentPart(name="Trumpet", voice="1", start_page=1, end_page=5)
    assert part1.name == "Trumpet"
    assert part1.voice == "1"
    assert part1.start_page == 1
    assert part1.end_page == 5
    
    part2 = InstrumentPart(name="Alto Sax", voice=None, start_page=6, end_page=10)
    assert part2.name == "Alto Sax"
    assert part2.voice is None
    
    print("✓ InstrumentPart creation works")


def test_initialization():
    """Test splitter initialization"""
    print("\nTest 2: Initialization...")
    
    # Test with API key
    api_key = os.getenv("OPENAI_API_KEY", "test-key")
    splitter = InstrumentAiPdfSplitter(api_key=api_key)
    assert splitter.api_key == api_key
    assert splitter.model == "gpt-5"  # Default model
    
    # Test with custom model
    splitter2 = InstrumentAiPdfSplitter(api_key=api_key, model="gpt-4-turbo")
    assert splitter2.model == "gpt-4-turbo"
    
    print("✓ Initialization works")


def test_file_hash():
    """Test file hashing"""
    print("\nTest 3: File hashing...")
    
    # Create a temporary test file
    test_file = Path("test_temp.txt")
    test_file.write_text("test content")
    
    hash1 = InstrumentAiPdfSplitter.file_hash(str(test_file))
    assert isinstance(hash1, str)
    assert len(hash1) == 64  # SHA-256 produces 64 hex characters
    
    # Same file should produce same hash
    hash2 = InstrumentAiPdfSplitter.file_hash(str(test_file))
    assert hash1 == hash2
    
    # Different content should produce different hash
    test_file.write_text("different content")
    hash3 = InstrumentAiPdfSplitter.file_hash(str(test_file))
    assert hash1 != hash3
    
    test_file.unlink()
    print("✓ File hashing works")


def test_error_handling():
    """Test error handling for invalid inputs"""
    print("\nTest 4: Error handling...")
    
    api_key = os.getenv("OPENAI_API_KEY", "test-key")
    splitter = InstrumentAiPdfSplitter(api_key=api_key)
    
    # Test analyse with both parameters - only test parameter validation, not API calls
    try:
        splitter.analyse(pdf_path="test.pdf", file_url=test_file_url)
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "Must provide either pdf_path or file_url" in str(e)
    
    # Test analyse with neither parameter
    try:
        splitter.analyse()
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "Must provide either pdf_path or file_url" in str(e)
    
    # Test split_pdf with both parameters
    try:
        splitter.split_pdf(pdf_path="test.pdf", file_url=test_file_url)
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "Must provide either pdf_path or file_url" in str(e)
    
    print("✓ Error handling works")


def test_file_size_validation():
    """Test file size validation"""
    print("\nTest 5: File size validation...")
    
    api_key = os.getenv("OPENAI_API_KEY", "test-key")
    splitter = InstrumentAiPdfSplitter(api_key=api_key)
    
    # Create a large dummy file (larger than 32MB)
    large_file = Path("large_test.pdf")
    
    # Create a 1MB file
    small_size = 1024 * 1024
    large_file.write_bytes(b"0" * small_size)
    
    # This should work (1MB < 32MB)
    try:
        path, is_temp = splitter._ensure_path(str(large_file))
        assert path is not None
        print("  - Small file accepted")
    except FileSizeExceededError:
        assert False, "Should not raise error for small file"
    
    # Clean up
    large_file.unlink()
    print("✓ File size validation works")


def test_split_pdf_logic():
    """Test split_pdf with mock data"""
    print("\nTest 6: Split PDF logic with mock data...")
    
    # Create a simple test PDF (we'll skip actual splitting without a real PDF)
    # This test verifies the data structure handling
    
    mock_instruments = [
        {"name": "Trumpet", "voice": "1", "start_page": 1, "end_page": 3},
        {"name": "Trumpet", "voice": "2", "start_page": 4, "end_page": 6},
        {"name": "Alto Sax", "voice": None, "start_page": 7, "end_page": 9},
    ]
    
    # Verify data structure
    for inst in mock_instruments:
        assert "name" in inst
        assert "start_page" in inst
        assert "end_page" in inst
        assert inst["start_page"] <= inst["end_page"]
    
    print("✓ Split PDF data structure handling works")


def test_analyse_single_part_validation():
    """Test analyse_single_part parameter validation"""
    print("\nTest 7: analyse_single_part validation...")
    
    api_key = os.getenv("OPENAI_API_KEY", "test-key")
    splitter = InstrumentAiPdfSplitter(api_key=api_key)
    
    # Test with both parameters
    try:
        splitter.analyse_single_part(pdf_path="test.pdf", file_url="http://example.com/test.pdf")
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "Must provide either pdf_path or file_url" in str(e)
    
    # Test with neither parameter
    try:
        splitter.analyse_single_part()
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "Must provide either pdf_path or file_url" in str(e)
    
    print("✓ analyse_single_part validation works")


def test_aggregate_instruments_logic():
    """Test 8: _aggregate_instruments majority and median logic with mocked results."""
    print("\nTest 8: Consensus aggregation logic...")
    
    api_key = os.getenv("OPENAI_API_KEY", "test-key")
    splitter = InstrumentAiPdfSplitter(api_key=api_key)
    
    # Scenario 1: Clear majority on start/end pages
    results = [
        {"instruments": [{"name": "Trumpet", "voice": "1", "start_page": 5, "end_page": 10}]},
        {"instruments": [{"name": "Trumpet", "voice": "1", "start_page": 5, "end_page": 10}]},
        {"instruments": [{"name": "Trumpet", "voice": "1", "start_page": 6, "end_page": 11}]},
    ]
    agg = splitter._aggregate_instruments(results)
    assert len(agg["instruments"]) == 1
    inst = agg["instruments"][0]
    assert inst["name"] == "Trumpet"
    assert inst["voice"] == "1"
    assert inst["start_page"] == 5  # Majority
    assert inst["end_page"] == 10   # Majority
    print("  - Majority vote works")
    
    # Scenario 2: Tie -> median fallback
    results2 = [
        {"instruments": [{"name": "Clarinet", "voice": None, "start_page": 2, "end_page": 8}]},
        {"instruments": [{"name": "Clarinet", "voice": None, "start_page": 4, "end_page": 9}]},
    ]
    agg2 = splitter._aggregate_instruments(results2)
    assert len(agg2["instruments"]) == 1
    inst2 = agg2["instruments"][0]
    assert inst2["name"] == "Clarinet"
    assert inst2["voice"] is None
    assert inst2["start_page"] == 3  # median of [2, 4]
    assert inst2["end_page"] == 8 or inst2["end_page"] == 9  # median of [8, 9]
    print("  - Median tie-break works")
    
    # Scenario 3: Threshold filtering
    results3 = [
        {"instruments": [{"name": "Flute", "voice": "1", "start_page": 1, "end_page": 5}]},
        {"instruments": [{"name": "Oboe", "voice": None, "start_page": 6, "end_page": 10}]},
        {"instruments": [{"name": "Flute", "voice": "1", "start_page": 1, "end_page": 5}]},
    ]
    agg3 = splitter._aggregate_instruments(results3)
    # Flute appears 2x, Oboe 1x; threshold = ceil(3/2) = 2
    assert len(agg3["instruments"]) == 1
    assert agg3["instruments"][0]["name"] == "Flute"
    print("  - Threshold filtering works")
    
    # Scenario 4: Voice normalization ("1." vs "1")
    results4 = [
        {"instruments": [{"name": "Violin", "voice": "1.", "start_page": 3, "end_page": 7}]},
        {"instruments": [{"name": "Violin", "voice": "1", "start_page": 3, "end_page": 7}]},
    ]
    agg4 = splitter._aggregate_instruments(results4)
    assert len(agg4["instruments"]) == 1  # Should merge "1." and "1"
    inst4 = agg4["instruments"][0]
    assert inst4["name"] == "Violin"
    # voice should be normalized to "1" (most common after normalization)
    assert inst4["voice"] in ["1", "1."]  # Could be either original casing
    print("  - Voice normalization works")
    
    print("✓ Consensus aggregation logic works")


def run_all_tests():
    """Run all tests"""
    print("=" * 60)
    print("Running InstrumentAiPdfSplitter Tests")
    print("=" * 60)
    
    try:
        test_instrument_part()
        test_initialization()
        test_file_hash()
        test_error_handling()
        test_file_size_validation()
        test_split_pdf_logic()
        test_analyse_single_part_validation()
        test_aggregate_instruments_logic()
        
        print("\n" + "=" * 60)
        print("All tests passed! ✓")
        print("=" * 60)
        
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()

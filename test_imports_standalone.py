#!/usr/bin/env python3
"""
Standalone test script for testing JSON and CSV import functionality
without requiring Django's test framework or database setup.
"""

import json
import io
import sys
import os

# Add the current directory to Python path so we can import our modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the functions we want to test
from data_repository.json_import import (
    extract_json_metadata,
    json_to_csv,
    JSONImportError,
)
from data_repository.csv_import import (
    extract_csv_metadata,
    csv_to_time_series_json,
    CSVImportError,
)


def test_json_extract_metadata_object():
    """Test JSON metadata extraction for object structure"""
    print("Testing JSON metadata extraction (object)...")
    json_data = {"name": "Test", "value": 123, "timestamp": "2023-01-01T12:00:00Z"}
    json_file = io.StringIO(json.dumps(json_data))

    try:
        metadata = extract_json_metadata(json_file)
        assert metadata["structure_type"] == "object"
        assert "name" in metadata["keys"]
        assert "timestamp" in metadata["time_fields"]
        assert "value" in metadata["value_fields"]
        assert metadata["row_count"] == 1
        print("✓ PASSED")
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        return False


def test_json_extract_metadata_array():
    """Test JSON metadata extraction for array structure"""
    print("Testing JSON metadata extraction (array)...")
    json_data = [{"id": 1, "temp": 25.5}, {"id": 2, "temp": 26.1}]
    json_file = io.StringIO(json.dumps(json_data))

    try:
        metadata = extract_json_metadata(json_file)
        assert metadata["structure_type"] == "array"
        assert "id" in metadata["keys"]
        assert "temp" in metadata["value_fields"]
        assert metadata["row_count"] == 2
        print("✓ PASSED")
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        return False


def test_json_to_csv_conversion():
    """Test JSON to CSV conversion"""
    print("Testing JSON to CSV conversion...")
    json_data = [
        {"time": "2023-01-01", "value": 10},
        {"time": "2023-01-02", "value": 20},
    ]
    json_file = io.StringIO(json.dumps(json_data))

    try:
        csv_output = json_to_csv(json_file, time_field="time", value_fields=["value"])
        expected_parts = ["time,value", "2023-01-01,10", "2023-01-02,20"]

        for part in expected_parts:
            assert part in csv_output, f"Expected '{part}' in CSV output"

        print("✓ PASSED")
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        return False


def test_csv_extract_metadata():
    """Test CSV metadata extraction"""
    print("Testing CSV metadata extraction...")
    csv_data = "timestamp,temperature,humidity\n2023-01-01T12:00:00Z,25.5,60\n2023-01-01T13:00:00Z,26.0,62"
    csv_file = io.StringIO(csv_data)

    try:
        metadata = extract_csv_metadata(csv_file)
        assert metadata["headers"] == ["timestamp", "temperature", "humidity"]
        assert "timestamp" in metadata["time_columns"]
        assert "temperature" in metadata["value_columns"]
        assert metadata["row_count"] > 0
        print("✓ PASSED")
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        return False


def test_csv_to_json_conversion():
    """Test CSV to time series JSON conversion"""
    print("Testing CSV to time series JSON conversion...")
    csv_data = "date,value1,value2\n2023-01-01,10,20\n2023-01-02,12,22"
    csv_file = io.StringIO(csv_data)

    try:
        json_output_str = csv_to_time_series_json(
            csv_file, time_column="date", value_columns=["value1", "value2"]
        )
        json_output = json.loads(json_output_str)

        assert "value1" in json_output["variables"]
        assert "value2" in json_output["variables"]
        assert len(json_output["data"]["value1"]["times"]) == 2
        assert json_output["data"]["value1"]["values"][0] == 10.0
        print("✓ PASSED")
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        return False


def test_error_handling():
    """Test error handling"""
    print("Testing error handling...")

    try:
        # Test JSONImportError
        try:
            raise JSONImportError("Test error", json_data={"key": "value"})
        except JSONImportError as e:
            assert "Test error" in str(e)

        # Test CSVImportError
        try:
            raise CSVImportError("CSV test error", row=["a", "b"], line_num=1)
        except CSVImportError as e:
            assert "CSV test error" in str(e)

        print("✓ PASSED")
        return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        return False


def run_all_tests():
    """Run all tests and report results"""
    print("=" * 60)
    print("TESTING DATASET IMPORT FUNCTIONALITY")
    print("=" * 60)

    tests = [
        test_json_extract_metadata_object,
        test_json_extract_metadata_array,
        test_json_to_csv_conversion,
        test_csv_extract_metadata,
        test_csv_to_json_conversion,
        test_error_handling,
    ]

    passed = 0
    total = len(tests)

    for test in tests:
        if test():
            passed += 1
        print()

    print("=" * 60)
    print(f"RESULTS: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 ALL TESTS PASSED! Dataset import functionality is working correctly.")
    else:
        print(f"⚠️  {total - passed} test(s) failed. Please check the implementation.")

    print("=" * 60)
    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)

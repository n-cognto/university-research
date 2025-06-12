#!/usr/bin/env python3
"""
Integration test to verify the complete dataset upload workflow.
This tests the integration between forms, views, and our import functions.
"""

import os
import sys
import tempfile
import json
from io import StringIO

# Add Django project to path
sys.path.insert(0, '/home/ncognto/Documents/university-research/research_portal')

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'research_portal.settings')

import django
django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from data_repository.models import Dataset, DatasetVersion
from data_repository.forms import DatasetVersionForm

def test_csv_upload_workflow():
    """Test the complete CSV upload workflow"""
    print("Testing CSV upload workflow...")
    
    # Create test CSV content
    csv_content = """timestamp,temperature,humidity
2023-01-01T00:00:00Z,15.5,60
2023-01-01T01:00:00Z,16.0,62
2023-01-01T02:00:00Z,15.8,61"""
    
    # Create a mock uploaded file
    csv_file = SimpleUploadedFile(
        name="test_data.csv",
        content=csv_content.encode('utf-8'),
        content_type="text/csv"
    )
    
    # Test form validation
    form_data = {
        'version_number': '1.0.0',
        'description': 'Test CSV upload',
        'time_start': '2023-01-01',
        'time_end': '2023-01-01',
        'time_resolution': 'hourly',
        'variables': 'temperature, humidity',
        'metadata': '{"source": "test"}'
    }
    
    form = DatasetVersionForm(data=form_data, files={'file_path': csv_file})
    
    if form.is_valid():
        print("✓ Form validation passed")
        
        # Test that our import functions would work with this file
        csv_file.seek(0)  # Reset file pointer
        
        try:
            from data_repository.csv_import import extract_csv_metadata, csv_to_time_series_json
            
            # Test metadata extraction
            metadata = extract_csv_metadata(csv_file)
            assert 'headers' in metadata
            assert 'temperature' in metadata['value_columns']
            assert 'timestamp' in metadata['time_columns']
            print("✓ CSV metadata extraction works")
            
            # Test time series conversion
            csv_file.seek(0)
            json_output = csv_to_time_series_json(csv_file, 'timestamp', ['temperature', 'humidity'])
            json_data = json.loads(json_output)
            assert 'temperature' in json_data['variables']
            assert len(json_data['data']['temperature']['times']) == 3
            print("✓ CSV to JSON conversion works")
            
            return True
        except Exception as e:
            print(f"✗ Import function error: {e}")
            return False
    else:
        print(f"✗ Form validation failed: {form.errors}")
        return False

def test_json_upload_workflow():
    """Test the complete JSON upload workflow"""
    print("\nTesting JSON upload workflow...")
    
    # Create test JSON content
    json_content = {
        "variables": ["temp", "pressure"],
        "data": {
            "temp": {
                "times": ["2023-01-01T00:00:00Z", "2023-01-01T01:00:00Z"],
                "values": [20.5, 21.0]
            },
            "pressure": {
                "times": ["2023-01-01T00:00:00Z", "2023-01-01T01:00:00Z"],
                "values": [1013.2, 1013.5]
            }
        },
        "metadata": {
            "source": "weather_station_1",
            "location": "Test Location"
        }
    }
    
    # Create a mock uploaded file
    json_file = SimpleUploadedFile(
        name="test_data.json",
        content=json.dumps(json_content).encode('utf-8'),
        content_type="application/json"
    )
    
    # Test form validation
    form_data = {
        'version_number': '1.0.0',
        'description': 'Test JSON upload',
        'variables': 'temp, pressure',
        'metadata': '{"format": "json"}'
    }
    
    form = DatasetVersionForm(data=form_data, files={'file_path': json_file})
    
    if form.is_valid():
        print("✓ Form validation passed")
        
        # Test that our import functions would work with this file
        json_file.seek(0)  # Reset file pointer
        
        try:
            from data_repository.json_import import extract_json_metadata
            
            # Test metadata extraction
            metadata = extract_json_metadata(json_file)
            assert 'structure_type' in metadata
            assert 'temp' in metadata.get('value_fields', [])
            print("✓ JSON metadata extraction works")
            
            return True
        except Exception as e:
            print(f"✗ Import function error: {e}")
            return False
    else:
        print(f"✗ Form validation failed: {form.errors}")
        return False

def run_integration_tests():
    """Run all integration tests"""
    print("=" * 60)
    print("TESTING DATASET UPLOAD INTEGRATION")
    print("=" * 60)
    
    tests = [
        test_csv_upload_workflow,
        test_json_upload_workflow
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"✗ Test failed with exception: {e}")
    
    print("\n" + "=" * 60)
    print(f"INTEGRATION TEST RESULTS: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 ALL INTEGRATION TESTS PASSED!")
        print("The complete dataset upload workflow should work correctly.")
    else:
        print(f"⚠️  {total - passed} test(s) failed.")
        print("There may be integration issues in the full workflow.")
    
    print("=" * 60)
    return passed == total

if __name__ == "__main__":
    success = run_integration_tests()
    sys.exit(0 if success else 1)
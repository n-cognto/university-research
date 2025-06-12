import json
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
import io

from .json_import import extract_json_metadata, json_to_csv, JSONImportError
from .csv_import import extract_csv_metadata, csv_to_time_series_json, CSVImportError


class JSONImportTests(TestCase):
    def test_extract_json_metadata_object(self):
        json_data = {"name": "Test", "value": 123, "timestamp": "2023-01-01T12:00:00Z"}
        json_file = io.StringIO(json.dumps(json_data))
        metadata = extract_json_metadata(json_file)
        self.assertEqual(metadata['structure_type'], 'object')
        self.assertIn('name', metadata['keys'])
        self.assertIn('timestamp', metadata['time_fields'])
        self.assertIn('value', metadata['value_fields'])
        self.assertEqual(metadata['row_count'], 1)

    def test_extract_json_metadata_array(self):
        json_data = [{"id": 1, "temp": 25.5}, {"id": 2, "temp": 26.1}]
        json_file = io.StringIO(json.dumps(json_data))
        metadata = extract_json_metadata(json_file)
        self.assertEqual(metadata['structure_type'], 'array')
        self.assertIn('id', metadata['keys'])
        self.assertIn('temp', metadata['value_fields'])
        self.assertEqual(metadata['row_count'], 2)

    def test_extract_json_metadata_nested(self):
        json_data = {"data": {"series1": {"times": [1,2,3], "values": [10,20,30]}}}
        json_file = io.StringIO(json.dumps(json_data))
        metadata = extract_json_metadata(json_file)
        self.assertEqual(metadata['structure_type'], 'object')
        self.assertIn('data.series1.times', metadata['time_fields'])
        self.assertIn('series1', metadata['value_fields'])

    def test_json_to_csv_array(self):
        json_data = [{"time": "2023-01-01", "value": 10}, {"time": "2023-01-02", "value": 20}]
        json_file = io.StringIO(json.dumps(json_data))
        csv_output = json_to_csv(json_file, time_field="time", value_fields=["value"])
        expected_csv = "time,value\\r\\n2023-01-01,10\\r\\n2023-01-02,20\\r\\n"
        self.assertEqual(csv_output, expected_csv)

    def test_json_to_csv_object_timeseries(self):
        json_data = {
            "data": {
                "temp": {"times": ["2023-01-01T00:00:00Z", "2023-01-01T01:00:00Z"], "values": [15, 16]},
                "humidity": {"times": ["2023-01-01T00:00:00Z", "2023-01-01T01:00:00Z"], "values": [60, 62]}
            }
        }
        json_file = io.StringIO(json.dumps(json_data))
        csv_output = json_to_csv(json_file)
        # Order of columns can vary based on dict iteration order in older Python, so we check headers and content
        self.assertIn("timestamp,humidity,temp", csv_output) # or timestamp,temp,humidity
        self.assertIn("2023-01-01T00:00:00Z,60,15", csv_output) # or 2023-01-01T00:00:00Z,15,60
        self.assertIn("2023-01-01T01:00:00Z,62,16", csv_output) # or 2023-01-01T01:00:00Z,16,62

    def test_json_import_error_exception(self):
        with self.assertRaisesRegex(JSONImportError, "Test error message"):
            raise JSONImportError("Test error message", json_data={"key": "value"}, path="some.path", field="some_field")

class CSVImportTests(TestCase):
    def test_extract_csv_metadata(self):
        csv_data = "timestamp,temperature,humidity\\n2023-01-01T12:00:00Z,25.5,60\\n2023-01-01T13:00:00Z,26.0,62"
        csv_file = io.StringIO(csv_data)
        metadata = extract_csv_metadata(csv_file)
        self.assertEqual(metadata['headers'], ['timestamp', 'temperature', 'humidity'])
        self.assertIn('timestamp', metadata['time_columns'])
        self.assertIn('temperature', metadata['value_columns'])
        self.assertEqual(metadata['row_count'], 3) # Includes header row for this counter

    def test_csv_to_time_series_json(self):
        csv_data = "date,value1,value2\\n2023-01-01,10,20\\n2023-01-02,12,22"
        csv_file = io.StringIO(csv_data)
        json_output_str = csv_to_time_series_json(csv_file, time_column='date', value_columns=['value1', 'value2'])
        json_output = json.loads(json_output_str)
        
        self.assertIn('value1', json_output['variables'])
        self.assertIn('value2', json_output['variables'])
        self.assertEqual(len(json_output['data']['value1']['times']), 2)
        self.assertEqual(json_output['data']['value1']['values'][0], 10.0)

    def test_csv_to_time_series_json_iso_datetime(self):
        csv_data = "datetime,temp\\n2023-01-01T10:00:00Z,22.5\\n2023-01-01T11:00:00Z,23.0"
        csv_file = io.StringIO(csv_data)
        json_output_str = csv_to_time_series_json(csv_file, time_column='datetime', value_columns=['temp'])
        json_output = json.loads(json_output_str)

        self.assertIsInstance(json_output['data']['temp']['times'][0], int) # Check if timestamp is converted to ms
        self.assertEqual(json_output['data']['temp']['values'][0], 22.5)

    def test_csv_import_error_exception(self):
        with self.assertRaisesRegex(CSVImportError, "Test CSV error"):
            raise CSVImportError("Test CSV error", row=["a", "b"], line_num=1, field="test_field")

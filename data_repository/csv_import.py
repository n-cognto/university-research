import csv
import json
import io
import logging
import chardet
from datetime import datetime
from typing import Dict, List, Any, Tuple, Optional

logger = logging.getLogger(__name__)


class CSVImportError(Exception):
    """Custom exception for CSV import errors with detailed context"""

    def __init__(self, message, row=None, line_num=None, field=None):
        self.message = message
        self.row = row
        self.line_num = line_num
        self.field = field
        super().__init__(message)


def detect_encoding(file_content: bytes) -> str:
    """Detect the encoding of a file."""
    result = chardet.detect(file_content)
    return result["encoding"] or "utf-8"


def extract_csv_metadata(file_obj, sample_size=10000):
    """
    Extract metadata from a CSV file including:
    - Headers
    - Data types
    - Time columns
    - Value columns (potential variables)
    - Sample data

    Args:
        file_obj: The file object to read (handles both text and binary modes)
        sample_size: Number of bytes to sample for detection

    Returns:
        Dict with extracted metadata
    """
    metadata = {
        "headers": [],
        "time_columns": [],
        "value_columns": [],
        "data_types": {},
        "sample_rows": [],
        "row_count": 0,
        "format": "csv",
    }

    # Remember current position
    current_position = file_obj.tell()

    try:
        # Read sample for detection
        sample = file_obj.read(sample_size)
        file_obj.seek(current_position)  # Reset file pointer

        # Handle both text and binary file objects
        if isinstance(sample, bytes):
            encoding = detect_encoding(sample)
            sample_str = sample.decode(encoding, errors="replace")
        else:
            sample_str = sample

        # Create a file-like object from the string
        sample_file = io.StringIO(sample_str)

        # Try to determine the dialect
        try:
            dialect = csv.Sniffer().sniff(sample_str)
        except csv.Error:
            dialect = "excel"  # Default to standard CSV dialect

        # Read headers and sample rows
        csv_reader = csv.reader(sample_file, dialect=dialect)
        headers = next(csv_reader, [])
        metadata["headers"] = headers

        # Read sample rows
        sample_rows = []
        for i, row in enumerate(csv_reader):
            if i >= 5:  # Limit to 5 sample rows
                break
            if len(row) == len(headers):
                sample_rows.append(row)

        metadata["sample_rows"] = sample_rows

        # For row counting, we need to handle the file properly
        file_obj.seek(current_position)

        # Handle binary vs text mode for row counting
        if hasattr(file_obj, "mode") and "b" in file_obj.mode:
            # Binary mode - decode first
            content = file_obj.read().decode(encoding, errors="replace")
            text_file = io.StringIO(content)
            row_count = sum(1 for _ in csv.reader(text_file, dialect=dialect))
        else:
            # Text mode or file-like object
            try:
                # Try to read as text directly
                content = file_obj.read()
                if isinstance(content, bytes):
                    content = content.decode(encoding, errors="replace")
                text_file = io.StringIO(content)
                row_count = sum(1 for _ in csv.reader(text_file, dialect=dialect))
            except (UnicodeDecodeError, AttributeError):
                # Fall back to line-by-line counting
                file_obj.seek(current_position)
                row_count = sum(1 for _ in file_obj)

        metadata["row_count"] = row_count

        # Reset file pointer again
        file_obj.seek(current_position)

        # Try to identify data types and time columns
        data_types = {}
        time_columns = []
        value_columns = []

        for i, header in enumerate(headers):
            if not sample_rows:
                # Can't determine types without sample data
                data_types[header] = "unknown"
                continue

            # Check values in this column
            values = [row[i] for row in sample_rows if i < len(row)]

            # Skip empty columns
            if not any(values) or all(
                not val.strip() for val in values if isinstance(val, str)
            ):
                data_types[header] = "empty"
                continue

            # Check if it's a time column
            time_indicators = [
                "time",
                "date",
                "year",
                "month",
                "day",
                "hour",
                "timestamp",
            ]

            is_time_column = any(
                indicator in header.lower() for indicator in time_indicators
            )

            # Check if values look like dates
            date_formats = [
                "%Y-%m-%d",
                "%Y/%m/%d",
                "%d-%m-%Y",
                "%d/%m/%Y",
                "%Y-%m-%dT%H:%M:%S",
            ]
            for value in values:
                if isinstance(value, str) and value.strip():
                    try:
                        for fmt in date_formats:
                            try:
                                datetime.strptime(value.strip(), fmt)
                                is_time_column = True
                                break
                            except ValueError:
                                continue
                    except:
                        pass

            if is_time_column:
                time_columns.append(header)
                data_types[header] = "time"
                continue

            # Try to determine if it's numeric
            is_numeric = True
            for value in values:
                if isinstance(value, str) and value.strip():
                    try:
                        float(value)
                    except ValueError:
                        is_numeric = False
                        break

            if is_numeric:
                value_columns.append(header)
                data_types[header] = "numeric"
            else:
                data_types[header] = "string"

        metadata["data_types"] = data_types
        metadata["time_columns"] = time_columns
        metadata["value_columns"] = value_columns

    except Exception as e:
        logger.error(f"Error extracting CSV metadata: {str(e)}")
        metadata["error"] = str(e)

    finally:
        # Reset file pointer to where it was
        file_obj.seek(current_position)

    return metadata


def csv_to_time_series_json(file_obj, time_column=None, value_columns=None):
    """
    Convert a CSV file to a time series JSON format suitable for visualization.

    Args:
        file_obj: The CSV file object (handles both text and binary modes)
        time_column: The name of the column containing timestamps
        value_columns: List of columns to extract as variables

    Returns:
        JSON string with time series data
    """
    # Remember current position
    current_position = file_obj.tell()

    try:
        # Extract metadata to get column information if not provided
        metadata = extract_csv_metadata(file_obj)

        if not time_column and metadata["time_columns"]:
            time_column = metadata["time_columns"][0]

        if not value_columns and metadata["value_columns"]:
            value_columns = metadata["value_columns"]

        if not time_column or not value_columns:
            return json.dumps({"error": "Could not determine time or value columns"})

        # Reset file to start
        file_obj.seek(current_position)

        # Handle both binary and text file objects
        if hasattr(file_obj, "mode") and "b" in file_obj.mode:
            # Binary mode - decode first
            content = file_obj.read()
            if isinstance(content, bytes):
                encoding = detect_encoding(content)
                content = content.decode(encoding, errors="replace")
            text_file = io.StringIO(content)
            reader = csv.DictReader(text_file)
        else:
            # Text mode or file-like object
            content = file_obj.read()
            if isinstance(content, bytes):
                encoding = detect_encoding(content)
                content = content.decode(encoding, errors="replace")
                text_file = io.StringIO(content)
                reader = csv.DictReader(text_file)
            else:
                file_obj.seek(current_position)
                reader = csv.DictReader(file_obj)

        # Prepare result structure
        result = {"variables": value_columns, "data": {}}

        # Initialize data structure
        for column in value_columns:
            result["data"][column] = {"times": [], "values": []}

        # Process rows
        for row in reader:
            if time_column not in row:
                continue

            time_value = row[time_column]

            # Skip empty time values
            if not time_value:
                continue

            # Try to parse as ISO date or timestamp
            try:
                dt = datetime.fromisoformat(time_value.replace("Z", "+00:00"))
                time_value = int(
                    dt.timestamp() * 1000
                )  # Convert to milliseconds for charting
            except ValueError:
                # If not ISO format, leave as is
                pass

            # Add data for each value column
            for column in value_columns:
                if column in row:
                    try:
                        # Try to convert to float
                        value = float(row[column])
                        result["data"][column]["times"].append(time_value)
                        result["data"][column]["values"].append(value)
                    except (ValueError, TypeError):
                        # Skip non-numeric values
                        pass

        return json.dumps(result)

    except Exception as e:
        logger.error(f"Error converting CSV to time series: {str(e)}")
        return json.dumps({"error": str(e)})

    finally:
        # Reset file pointer to where it was
        file_obj.seek(current_position)

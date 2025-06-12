"""
Enhanced File Processor for Data Repository

This module provides comprehensive file processing capabilities with improved
validation, error handling, and metadata extraction for CSV and JSON files.
"""
import os
import json
import csv
import logging
import chardet
import pandas as pd
from typing import Dict, List, Any, Tuple, Optional, Union
from datetime import datetime
from django.core.files.uploadedfile import UploadedFile
from django.core.exceptions import ValidationError
from .csv_import import extract_csv_metadata, CSVImportError
from .json_import import extract_json_metadata, JSONImportError

logger = logging.getLogger(__name__)

class FileProcessorError(Exception):
    """Custom exception for file processing errors"""
    def __init__(self, message: str, error_code: str = None, details: Dict = None):
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        super().__init__(message)

class EnhancedFileProcessor:
    """Enhanced file processor with comprehensive validation and metadata extraction"""
    
    SUPPORTED_FORMATS = {
        'csv': ['.csv', '.txt'],
        'json': ['.json', '.geojson'],
        'excel': ['.xlsx', '.xls'],
        'netcdf': ['.nc', '.netcdf'],
        'hdf5': ['.h5', '.hdf5'],
        'parquet': ['.parquet']
    }
    
    MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB
    MAX_PREVIEW_ROWS = 1000
    
    def __init__(self):
        self.validation_rules = self._load_validation_rules()
    
    def process_file(self, file_obj: UploadedFile, 
                    expected_format: str = None,
                    validation_schema: Dict = None,
                    extract_preview: bool = True) -> Dict[str, Any]:
        """
        Comprehensive file processing with validation and metadata extraction
        
        Args:
            file_obj: Uploaded file object
            expected_format: Expected file format ('csv', 'json', etc.)
            validation_schema: Schema for data validation
            extract_preview: Whether to extract data preview
            
        Returns:
            Dict containing processed file information
        """
        result = {
            'success': True,
            'file_info': {},
            'metadata': {},
            'preview': None,
            'validation_results': {},
            'errors': [],
            'warnings': []
        }
        
        try:
            # Step 1: Basic file validation
            self._validate_file_basic(file_obj, result)
            
            # Step 2: Format detection and validation
            detected_format = self._detect_format(file_obj)
            if expected_format and detected_format != expected_format:
                result['warnings'].append(
                    f"Detected format '{detected_format}' differs from expected '{expected_format}'"
                )
            
            result['file_info']['detected_format'] = detected_format
            result['file_info']['file_size'] = file_obj.size
            result['file_info']['file_name'] = file_obj.name
            
            # Step 3: Format-specific processing
            if detected_format == 'csv':
                self._process_csv_file(file_obj, result, extract_preview)
            elif detected_format == 'json':
                self._process_json_file(file_obj, result, extract_preview)
            elif detected_format == 'excel':
                self._process_excel_file(file_obj, result, extract_preview)
            else:
                result['warnings'].append(f"Limited support for format: {detected_format}")
            
            # Step 4: Data validation against schema
            if validation_schema:
                self._validate_against_schema(result, validation_schema)
            
            # Step 5: Quality assessment
            self._assess_data_quality(result)
            
        except Exception as e:
            result['success'] = False
            result['errors'].append(str(e))
            logger.exception(f"Error processing file {file_obj.name}")
        
        return result
    
    def _validate_file_basic(self, file_obj: UploadedFile, result: Dict):
        """Basic file validation"""
        if not file_obj:
            raise FileProcessorError("No file provided")
        
        if file_obj.size == 0:
            raise FileProcessorError("File is empty")
        
        if file_obj.size > self.MAX_FILE_SIZE:
            raise FileProcessorError(
                f"File too large. Maximum size: {self.MAX_FILE_SIZE / 1024 / 1024:.1f}MB"
            )
        
        # Check for suspicious file names
        suspicious_chars = ['<', '>', '|', '&', '$', '`']
        if any(char in file_obj.name for char in suspicious_chars):
            result['warnings'].append("File name contains suspicious characters")
    
    def _detect_format(self, file_obj: UploadedFile) -> str:
        """Enhanced format detection"""
        # Get file extension
        file_name = file_obj.name.lower()
        ext = os.path.splitext(file_name)[1]
        
        # Check against known extensions
        for format_name, extensions in self.SUPPORTED_FORMATS.items():
            if ext in extensions:
                # Verify with content analysis for ambiguous cases
                if ext in ['.txt']:
                    return self._analyze_content_format(file_obj)
                return format_name
        
        # Content-based detection for unknown extensions
        return self._analyze_content_format(file_obj)
    
    def _analyze_content_format(self, file_obj: UploadedFile) -> str:
        """Analyze file content to determine format"""
        current_pos = file_obj.tell()
        file_obj.seek(0)
        
        try:
            # Read sample
            sample = file_obj.read(8192)
            file_obj.seek(current_pos)
            
            # Try to decode
            if isinstance(sample, bytes):
                encoding = chardet.detect(sample)['encoding'] or 'utf-8'
                try:
                    sample_str = sample.decode(encoding)
                except UnicodeDecodeError:
                    return 'binary'
            else:
                sample_str = sample
            
            # Check for JSON
            sample_str = sample_str.strip()
            if (sample_str.startswith('{') or sample_str.startswith('[')):
                try:
                    json.loads(sample_str)
                    return 'json'
                except json.JSONDecodeError:
                    pass
            
            # Check for CSV
            if ',' in sample_str and '\n' in sample_str:
                try:
                    csv.Sniffer().sniff(sample_str)
                    return 'csv'
                except csv.Error:
                    pass
            
            return 'text'
            
        except Exception:
            return 'unknown'
        finally:
            file_obj.seek(current_pos)
    
    def _process_csv_file(self, file_obj: UploadedFile, result: Dict, extract_preview: bool):
        """Enhanced CSV processing"""
        try:
            # Use existing CSV metadata extractor
            metadata = extract_csv_metadata(file_obj)
            result['metadata'].update(metadata)
            
            # Enhanced validation
            self._validate_csv_structure(metadata, result)
            
            # Extract preview if requested
            if extract_preview and metadata.get('sample_rows'):
                result['preview'] = {
                    'headers': metadata.get('headers', []),
                    'rows': metadata.get('sample_rows', [])[:10],
                    'total_rows': metadata.get('row_count', 0)
                }
            
            # Detect data patterns
            self._detect_csv_patterns(metadata, result)
            
        except Exception as e:
            result['errors'].append(f"CSV processing error: {str(e)}")
    
    def _process_json_file(self, file_obj: UploadedFile, result: Dict, extract_preview: bool):
        """Enhanced JSON processing"""
        try:
            # Use existing JSON metadata extractor
            metadata = extract_json_metadata(file_obj)
            result['metadata'].update(metadata)
            
            # Enhanced validation
            self._validate_json_structure(metadata, result)
            
            # Extract preview if requested
            if extract_preview:
                self._extract_json_preview(file_obj, result)
            
            # Detect data patterns
            self._detect_json_patterns(metadata, result)
            
        except Exception as e:
            result['errors'].append(f"JSON processing error: {str(e)}")
    
    def _process_excel_file(self, file_obj: UploadedFile, result: Dict, extract_preview: bool):
        """Enhanced Excel processing"""
        try:
            # Read Excel file
            df = pd.read_excel(file_obj, nrows=self.MAX_PREVIEW_ROWS if extract_preview else None)
            
            # Extract metadata
            result['metadata'].update({
                'format': 'excel',
                'headers': list(df.columns),
                'row_count': len(df),
                'column_count': len(df.columns),
                'data_types': {col: str(dtype) for col, dtype in df.dtypes.items()}
            })
            
            # Extract preview
            if extract_preview:
                result['preview'] = {
                    'headers': list(df.columns),
                    'rows': df.head(10).values.tolist(),
                    'total_rows': len(df)
                }
            
            # Check for multiple sheets
            file_obj.seek(0)
            xls = pd.ExcelFile(file_obj)
            if len(xls.sheet_names) > 1:
                result['metadata']['sheet_names'] = xls.sheet_names
                result['warnings'].append(f"Multiple sheets detected: {xls.sheet_names}")
            
        except Exception as e:
            result['errors'].append(f"Excel processing error: {str(e)}")
    
    def _validate_csv_structure(self, metadata: Dict, result: Dict):
        """Validate CSV structure"""
        if not metadata.get('headers'):
            result['errors'].append("No headers detected in CSV file")
        
        if metadata.get('row_count', 0) == 0:
            result['errors'].append("No data rows found in CSV file")
        
        # Check for empty columns
        empty_cols = [col for col, dtype in metadata.get('data_types', {}).items() 
                     if dtype == 'empty']
        if empty_cols:
            result['warnings'].append(f"Empty columns detected: {empty_cols}")
    
    def _validate_json_structure(self, metadata: Dict, result: Dict):
        """Validate JSON structure"""
        if metadata.get('structure_type') not in ['object', 'array']:
            result['errors'].append("Invalid JSON structure")
        
        if not metadata.get('keys') and metadata.get('structure_type') == 'object':
            result['warnings'].append("JSON object has no keys")
    
    def _detect_csv_patterns(self, metadata: Dict, result: Dict):
        """Detect patterns in CSV data"""
        patterns = []
        
        # Time series detection
        if metadata.get('time_columns') and metadata.get('value_columns'):
            patterns.append('time_series')
        
        # Geospatial detection
        headers = [h.lower() for h in metadata.get('headers', [])]
        if any(geo in headers for geo in ['lat', 'latitude', 'lon', 'longitude']):
            patterns.append('geospatial')
        
        result['metadata']['detected_patterns'] = patterns
    
    def _detect_json_patterns(self, metadata: Dict, result: Dict):
        """Detect patterns in JSON data"""
        patterns = []
        
        # Time series detection
        if metadata.get('time_fields') and metadata.get('value_fields'):
            patterns.append('time_series')
        
        # GeoJSON detection
        if 'type' in metadata.get('keys', []):
            patterns.append('geojson')
        
        result['metadata']['detected_patterns'] = patterns
    
    def _extract_json_preview(self, file_obj: UploadedFile, result: Dict):
        """Extract JSON preview data"""
        current_pos = file_obj.tell()
        file_obj.seek(0)
        
        try:
            content = file_obj.read(50000)  # Read first 50KB for preview
            if isinstance(content, bytes):
                content = content.decode('utf-8', errors='replace')
            
            data = json.loads(content)
            
            if isinstance(data, list):
                result['preview'] = {
                    'type': 'array',
                    'sample_items': data[:10],
                    'total_items': len(data)
                }
            elif isinstance(data, dict):
                result['preview'] = {
                    'type': 'object',
                    'keys': list(data.keys())[:20],
                    'sample_data': {k: v for k, v in list(data.items())[:5]}
                }
        except Exception as e:
            result['warnings'].append(f"Could not extract JSON preview: {str(e)}")
        finally:
            file_obj.seek(current_pos)
    
    def _validate_against_schema(self, result: Dict, schema: Dict):
        """Validate data against provided schema"""
        validation_results = {
            'schema_valid': True,
            'field_validation': {},
            'schema_errors': []
        }
        
        # Implementation would depend on schema format
        # This is a placeholder for schema validation logic
        
        result['validation_results'] = validation_results
    
    def _assess_data_quality(self, result: Dict):
        """Assess overall data quality"""
        quality_score = 100
        quality_issues = []
        
        # Deduct points for errors and warnings
        error_count = len(result.get('errors', []))
        warning_count = len(result.get('warnings', []))
        
        quality_score -= error_count * 20
        quality_score -= warning_count * 5
        
        # Check data completeness
        metadata = result.get('metadata', {})
        if metadata.get('row_count', 0) < 10:
            quality_score -= 10
            quality_issues.append('Very small dataset')
        
        # Check for missing values (CSV)
        if metadata.get('format') == 'csv':
            empty_cols = [col for col, dtype in metadata.get('data_types', {}).items() 
                         if dtype == 'empty']
            if empty_cols:
                quality_score -= len(empty_cols) * 5
                quality_issues.append(f'Empty columns: {len(empty_cols)}')
        
        result['quality_assessment'] = {
            'score': max(0, quality_score),
            'grade': self._get_quality_grade(quality_score),
            'issues': quality_issues
        }
    
    def _get_quality_grade(self, score: int) -> str:
        """Convert quality score to grade"""
        if score >= 90:
            return 'A'
        elif score >= 80:
            return 'B'
        elif score >= 70:
            return 'C'
        elif score >= 60:
            return 'D'
        else:
            return 'F'
    
    def _load_validation_rules(self) -> Dict:
        """Load validation rules from configuration"""
        return {
            'csv': {
                'max_columns': 1000,
                'max_rows': 1000000,
                'required_headers': True
            },
            'json': {
                'max_depth': 10,
                'max_keys': 10000
            }
        }

# Utility functions
def validate_file_upload(file_obj: UploadedFile, 
                        allowed_formats: List[str] = None,
                        max_size: int = None) -> Tuple[bool, List[str]]:
    """Quick file validation for upload forms"""
    errors = []
    
    if not file_obj:
        errors.append("No file provided")
        return False, errors
    
    # Size check
    max_size = max_size or EnhancedFileProcessor.MAX_FILE_SIZE
    if file_obj.size > max_size:
        errors.append(f"File too large (max: {max_size // 1024 // 1024}MB)")
    
    # Format check
    if allowed_formats:
        ext = os.path.splitext(file_obj.name.lower())[1]
        allowed_exts = []
        processor = EnhancedFileProcessor()
        
        for fmt in allowed_formats:
            allowed_exts.extend(processor.SUPPORTED_FORMATS.get(fmt, []))
        
        if ext not in allowed_exts:
            errors.append(f"Unsupported file format. Allowed: {', '.join(allowed_exts)}")
    
    return len(errors) == 0, errors

def get_file_preview(file_obj: UploadedFile, max_rows: int = 10) -> Dict:
    """Quick file preview for UI"""
    processor = EnhancedFileProcessor()
    result = processor.process_file(file_obj, extract_preview=True)
    
    preview_data = result.get('preview', {})
    preview_data['file_info'] = result.get('file_info', {})
    
    return preview_data
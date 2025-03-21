import os
import csv
import json
import pytz
import logging
import io
import pandas as pd
import xlsxwriter
from datetime import datetime, timedelta
from pathlib import Path
from dateutil import parser
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.utils import timezone
from django.core.files.storage import FileSystemStorage
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import FormView, TemplateView
from django.db.models import Avg, Max, Min, Sum, OuterRef, Subquery
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.contrib.gis.db.models.functions import Distance
from django.db import models
import operator
from functools import reduce
from .utils import check_for_alerts, create_alert_from_detection, send_alert_notifications
from rest_framework import viewsets, filters
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from .utils import fetch_environmental_data, calculate_statistics
from .models import DataExport, WeatherAlert, WeatherStation, ClimateData, Country, WeatherDataType
from .forms import CSVUploadForm, FlashDriveImportForm
from .serializers import (
    WeatherAlertSerializer,
    WeatherStationSerializer,
    ClimateDataSerializer,
    GeoJSONClimateDataSerializer,
    StackedDataSerializer,
    StackInfoSerializer
)
from .permissions import IsAdminOrReadOnly
from django.urls import reverse_lazy, reverse


# views.py (improved debug_stations)
def debug_stations(request):
    """Debug view to check GeoJSON output"""
    stations = WeatherStation.objects.all().select_related('country')
    features = [station.to_representation() for station in stations]
    
    return JsonResponse({
        'type': 'FeatureCollection',
        'count': len(features),
        'features': features,
        'debug_info': {
            'fields_available': [field.name for field in WeatherStation._meta.fields],
            'data_types': list(WeatherDataType.objects.values_list('name', flat=True))
        }
    })

# views.py (improved MapView)
class MapView(TemplateView):
    template_name = 'maps/map.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get data for initial map rendering with optimized queries
        stations = WeatherStation.objects.select_related('country').all()
        active_stations = stations.filter(is_active=True)
        
        # Get recent climate data with related station info
        recent_data = ClimateData.objects.select_related('station').filter(
            timestamp__gte=timezone.now() - timedelta(days=7)
        ).order_by('-timestamp')[:100]
        
        # Get active alerts with related station and country info
        active_alerts = WeatherAlert.objects.select_related('station', 'country', 'data_type').filter(
            status='active'
        ).order_by('-created_at')[:5]
        
        context.update({
            'stations_count': stations.count(),
            'active_stations': active_stations.count(),
            'stations_list': stations,
            'recent_data_count': recent_data.count(),
            'has_alerts': active_alerts.exists(),
            'alerts_count': active_alerts.count(),
            'data_types': WeatherDataType.objects.all(),
            'api_base_url': '/api',
            'map_title': 'Weather Stations Map',
            'map_description': 'Interactive map of all weather stations and their data',
            'geojson_url': '/api/weather-stations/',
        })
        
        return context

# views.py (fixed WeatherStationViewSet)
class WeatherStationViewSet(viewsets.ModelViewSet):
    queryset = WeatherStation.objects.all()
    serializer_class = WeatherStationSerializer
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_active', 'country']
    search_fields = ['name', 'description', 'country__name']
    ordering_fields = ['name', 'date_installed']
    
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        
        # Manually create the GeoJSON feature collection
        features = []
        for station in queryset:
            feature = {
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [station.longitude, station.latitude]
                },
                'properties': {
                    'id': station.id,
                    'name': station.name,
                    'description': station.description,
                    'is_active': station.is_active,
                    'altitude': station.altitude,
                    'date_installed': station.date_installed.isoformat() if station.date_installed else None,
                    'country': station.country.name if station.country else None
                }
            }
            features.append(feature)
        
        feature_collection = {
            'type': 'FeatureCollection',
            'features': features
        }
        
        return Response(feature_collection)
    
    @action(detail=False, methods=['get'])
    def nearby(self, request):
        """Find weather stations near a specific point"""
        lat = request.query_params.get('lat', None)
        lng = request.query_params.get('lng', None)
        radius = request.query_params.get('radius', 10)  # Default 10 km
        
        if lat is None or lng is None:
            return Response({"error": "Latitude and longitude are required"}, status=400)
        
        try:
            point = Point(float(lng), float(lat), srid=4326)
            radius = float(radius)
        except ValueError:
            return Response({"error": "Invalid coordinates or radius"}, status=400)
        
        # Find stations within radius km
        stations = WeatherStation.objects.filter(
            location__distance_lte=(point, D(km=radius))
        ).annotate(
            distance=Distance('location', point)
        ).order_by('distance')
        
        serializer = self.get_serializer(stations, many=True)
        return Response({
            'type': 'FeatureCollection',
            'features': serializer.data
        })
    
    @action(detail=True, methods=['get'])
    def data(self, request, pk=None):
        """Get climate data for a specific station"""
        station = self.get_object()
        days = int(request.query_params.get('days', 7))
        data_types = request.query_params.getlist('data_types', [])
        
        start_date = timezone.now() - timedelta(days=days)
        query = ClimateData.objects.filter(
            station=station,
            timestamp__gte=start_date
        ).order_by('-timestamp')
        
        # Filter by data types if specified
        if data_types:
            conditions = []
            for data_type in data_types:
                if hasattr(ClimateData, data_type):
                    conditions.append(~models.Q(**{data_type: None}))
            if conditions:
                query = query.filter(reduce(operator.or_, conditions))
        
        page = self.paginate_queryset(query)
        if page is not None:
            serializer = ClimateDataSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = ClimateDataSerializer(query, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def export(self, request, pk=None):
        """Export climate data for a specific station"""
        station = self.get_object()
        days = int(request.query_params.get('days', 7))
        format_type = request.query_params.get('format', 'csv').lower()
        
        if format_type not in ['csv', 'json', 'geojson']:
            return Response({"error": "Format must be one of: csv, json, geojson"}, status=400)
        
        start_date = timezone.now() - timedelta(days=days)
        climate_data = ClimateData.objects.filter(
            station=station,
            timestamp__gte=start_date
        ).order_by('-timestamp')
        
        # Record the export
        if request.user.is_authenticated:
            DataExport.objects.create(
                user=request.user,
                stations=[station],
                export_format=format_type,
                date_from=start_date,
                date_to=timezone.now()
            )
        
        if format_type == 'csv':
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="{station.name}_{timezone.now().strftime("%Y%m%d")}.csv"'
            
            writer = csv.writer(response)
            writer.writerow([
                'Station', 'Timestamp', 'Temperature', 'Humidity', 'Precipitation', 
                'Air Quality Index', 'Wind Speed', 'Wind Direction', 'Barometric Pressure',
                'Cloud Cover', 'Soil Moisture', 'Water Level', 'Data Quality', 'UV Index'
            ])
            
            for data in climate_data:
                writer.writerow([
                    station.name,
                    data.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                    data.temperature,
                    data.humidity,
                    data.precipitation,
                    data.air_quality_index,
                    data.wind_speed,
                    data.wind_direction,
                    data.barometric_pressure,
                    data.cloud_cover,
                    data.soil_moisture,
                    data.water_level,
                    data.data_quality,
                    data.uv_index
                ])
            
            return response
            
        elif format_type == 'json':
            serializer = ClimateDataSerializer(climate_data, many=True)
            response = HttpResponse(json.dumps(serializer.data, default=str), content_type='application/json')
            response['Content-Disposition'] = f'attachment; filename="{station.name}_{timezone.now().strftime("%Y%m%d")}.json"'
            return response
            
        elif format_type == 'geojson':
            serializer = GeoJSONClimateDataSerializer(climate_data, many=True)
            response = HttpResponse(json.dumps(serializer.data, default=str), content_type='application/geo+json')
            response['Content-Disposition'] = f'attachment; filename="{station.name}_{timezone.now().strftime("%Y%m%d")}.geojson"'
            return response
    
    @action(detail=True, methods=['get'])
    def statistics(self, request, pk=None):
        """Get statistics for a specific weather station"""
        station = self.get_object()
        days = int(request.query_params.get('days', 30))
        
        start_date = timezone.now() - timedelta(days=days)
        stats = ClimateData.objects.filter(
            station=station,
            timestamp__gte=start_date
        ).aggregate(
            avg_temp=Avg('temperature'),
            max_temp=Max('temperature'),
            min_temp=Min('temperature'),
            avg_humidity=Avg('humidity'),
            total_precipitation=Sum('precipitation', default=0),
            avg_wind_speed=Avg('wind_speed'),
            max_wind_speed=Max('wind_speed'),
            avg_pressure=Avg('barometric_pressure'),
            max_uv=Max('uv_index')
        )
        
        return Response({
            'station': station.name,
            'period': f"Last {days} days",
            'stats': stats
        })
    
    @action(detail=True, methods=['post'])
    def push_data(self, request, pk=None):
        """Push weather data onto the station's stack"""
        station = self.get_object()
        serializer = StackedDataSerializer(data=request.data)
        
        if serializer.is_valid():
            success = station.push_data(serializer.validated_data)
            if success:
                return Response({
                    'success': True, 
                    'stack_size': station.stack_size(),
                    'message': 'Data added to stack successfully'
                })
            else:
                return Response({
                    'success': False,
                    'message': 'Stack is full'
                }, status=400)
        else:
            return Response(serializer.errors, status=400)
    
    @action(detail=True, methods=['post'])
    def process_stack(self, request, pk=None):
        """Process all data in the station's stack"""
        station = self.get_object()
        records_processed = station.process_data_stack()
        
        return Response({
            'success': True,
            'records_processed': records_processed,
            'message': f'Successfully processed {records_processed} records'
        })
    
    @action(detail=True, methods=['get'])
    def stack_info(self, request, pk=None):
        """Get information about the station's data stack"""
        station = self.get_object()
        latest_data = station.peek_data()
        
        data = {
            'station_id': station.id,
            'station_name': station.name,
            'stack_size': station.stack_size(),
            'max_stack_size': station.max_stack_size,
            'last_data_feed': station.last_data_feed,
            'auto_process': station.auto_process,
            'process_threshold': station.process_threshold,
            'latest_data': latest_data
        }
        
        serializer = StackInfoSerializer(data)
        return Response(serializer.data)

    def perform_create(self, serializer):
        """Override create to check for alerts when new data is added"""
        climate_data = serializer.save()
        
        # Check for potential alerts
        alerts = check_for_alerts(climate_data)
        
        # Create alerts and send notifications
        for alert_data in alerts:
            alert = create_alert_from_detection(climate_data.station, alert_data)
            send_alert_notifications(alert)
        
        return climate_data

class WeatherAlertViewSet(viewsets.ModelViewSet):
    queryset = WeatherAlert.objects.all().select_related('station', 'country', 'data_type')
    serializer_class = WeatherAlertSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['station', 'severity', 'status', 'country', 'data_type']
    search_fields = ['title', 'description', 'station__name', 'country__name']
    ordering_fields = ['created_at', 'updated_at', 'severity']
    
    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """Mark an alert as resolved"""
        alert = self.get_object()
        alert.status = 'resolved'
        alert.resolved_at = timezone.now()
        alert.save()
        
        serializer = self.get_serializer(alert)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def active(self, request):
        """Get all active alerts"""
        alerts = WeatherAlert.objects.filter(status='active')
        serializer = self.get_serializer(alerts, many=True)
        return Response(serializer.data)
    
# views.py (new map_data endpoint)
@api_view(['GET'])
def map_data(request):
    """API endpoint to get consolidated data for map display"""
    # Get stations with related data
    stations = WeatherStation.objects.select_related('country').all()
    
    # Get recent climate data for active stations
    recent_data = []
    active_stations = stations.filter(is_active=True)
    
    for station in active_stations:
        latest_reading = ClimateData.objects.filter(
            station=station
        ).order_by('-timestamp').first()
        
        if latest_reading:
            data = {
                'station_id': station.id,
                'station_name': station.name,
                'timestamp': latest_reading.timestamp,
                'data': {}
            }
            
            # Add available measurements
            for data_type in station.available_data_types():
                if hasattr(latest_reading, data_type):
                    value = getattr(latest_reading, data_type)
                    if value is not None:
                        data['data'][data_type] = value
            
            data['is_recent'] = latest_reading.timestamp > (timezone.now() - timedelta(days=1))
            recent_data.append(data)
    
    # Get active alerts with related info
    active_alerts = WeatherAlert.objects.select_related(
        'station', 'country', 'data_type'
    ).filter(status='active')
    
    return Response({
        'stations': {
            'type': 'FeatureCollection',
            'features': [station.to_representation() for station in stations]
        },
        'recent_data': recent_data,
        'alerts': WeatherAlertSerializer(active_alerts, many=True).data,
        'stats': {
            'total_stations': stations.count(),
            'active_stations': active_stations.count(),
            'stations_with_recent_data': len([d for d in recent_data if d['is_recent']]),
            'active_alerts': active_alerts.count(),
            'available_data_types': list(WeatherDataType.objects.values('name', 'display_name', 'unit'))
        }
    })


class CSVUploadView(FormView):
    template_name = 'maps/csv_upload.html'
    form_class = CSVUploadForm
    success_url = reverse_lazy('maps:import_success')

    # @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['current_datetime'] = timezone.now()
        return context

    def form_valid(self, form):
        try:
            import_type = form.cleaned_data['import_type']
            csv_file = form.cleaned_data['csv_file']

            # Process the file and store results
            if import_type == 'stations':
                result = self.process_stations_file(csv_file)
            elif import_type == 'climate_data':
                result = self.process_climate_data_file(csv_file)
            elif import_type == 'weather_data_types':
                result = self.process_weather_data_types_file(csv_file)
            elif import_type == 'countries':
                result = self.process_countries_file(csv_file)
            else:
                result = {'success': 0, 'error': 1, 'errors': ['Invalid import type']}

            # Store results in session
            self.request.session['import_results'] = result

            # If this is an AJAX request, return JSON response
            if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': True,
                    'redirect_url': self.get_success_url(),
                    'result': result
                })

            # Add message for regular form submission
            if result['success'] > 0:
                messages.success(
                    self.request,
                    f"Successfully processed {result['success']} records. "
                    f"Encountered {result['error']} errors."
                )
            else:
                messages.error(
                    self.request,
                    f"Import failed. Encountered {result['error']} errors."
                )

            return super().form_valid(form)

        except Exception as e:
            error_msg = str(e)
            messages.error(self.request, f"An error occurred: {error_msg}")
            
            # If AJAX request, return error response
            if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'error': error_msg
                }, status=400)
                
            return self.form_invalid(form)

    def process_stations_file(self, csv_file):
        """Process a CSV file containing weather station data"""
        result = {
            'success': 0,
            'error': 0,
            'errors': [],
            'type': 'Weather Stations'
        }

        try:
            decoded_file = csv_file.read().decode('utf-8')
            io_string = io.StringIO(decoded_file)
            reader = csv.DictReader(io_string)

            # Check for required fields - either (name, latitude, longitude) OR (name, location)
            if not ('name' in reader.fieldnames and
                    (('latitude' in reader.fieldnames and 'longitude' in reader.fieldnames) or
                     'location' in reader.fieldnames)):
                result['errors'].append(
                    "Missing required fields: 'name' and either ('latitude' and 'longitude') or 'location'")
                return result

            for row in reader:
                try:
                    # Check for station name
                    if not row.get('name'):
                        result['error'] += 1
                        result['errors'].append(f"Missing name in row: {row}")
                        continue

                    # Parse location - now supporting multiple input formats
                    location = None

                    # Option 1: Separate latitude/longitude fields
                    if 'latitude' in row and 'longitude' in row and row.get('latitude') and row.get('longitude'):
                        try:
                            latitude = float(row['latitude'])
                            longitude = float(row['longitude'])
                            location = Point(longitude, latitude, srid=4326)
                        except (ValueError, TypeError) as e:
                            result['error'] += 1
                            result['errors'].append(f"Invalid coordinates in row: {row}. Error: {str(e)}")
                            continue

                    # Option 2: Combined location field (format: "POINT(longitude latitude)" or "longitude,latitude")
                    elif 'location' in row and row.get('location'):
                        try:
                            location_str = row['location'].strip()

                            # Check if in WKT format: "POINT(longitude latitude)"
                            if location_str.upper().startswith('POINT'):
                                # Extract coordinates from POINT(lon lat) format
                                coords = location_str.strip('POINT()').split()
                                longitude = float(coords[0])
                                latitude = float(coords[1])
                                location = Point(longitude, latitude, srid=4326)

                            # Check if in simple format: "longitude,latitude"
                            elif ',' in location_str:
                                coords = location_str.split(',')
                                longitude = float(coords[0].strip())
                                latitude = float(coords[1].strip())
                                location = Point(longitude, latitude, srid=4326)
                            else:
                                raise ValueError(f"Unrecognized location format: {location_str}")

                        except (ValueError, TypeError, IndexError) as e:
                            result['error'] += 1
                            result['errors'].append(f"Invalid location format in row: {row}. Error: {str(e)}")
                            continue

                    # If no valid location data found
                    if not location:
                        result['error'] += 1
                        result['errors'].append(f"Missing or invalid location data in row: {row}")
                        continue

                    # Parse date if present
                    date_installed = None
                    if 'date_installed' in row and row['date_installed']:
                        try:
                            date_installed = parser.parse(row['date_installed']).date()
                        except ValueError:
                            # If date parsing fails, leave as None but log the issue
                            result['errors'].append(f"Invalid date format in row: {row}. Using null instead.")

                    # Parse decommissioned date if present
                    date_decommissioned = None
                    if 'date_decommissioned' in row and row['date_decommissioned']:
                        try:
                            date_decommissioned = parser.parse(row['date_decommissioned']).date()
                        except ValueError:
                            # If date parsing fails, leave as None but log the issue
                            result['errors'].append(
                                f"Invalid decommissioned date format in row: {row}. Using null instead.")

                    # Parse altitude if present
                    altitude = None
                    if 'altitude' in row and row['altitude']:
                        try:
                            altitude = float(row['altitude'])
                        except ValueError:
                            # If altitude parsing fails, leave as None but log the issue
                            result['errors'].append(f"Invalid altitude format in row: {row}. Using null instead.")

                    # Parse is_active if present
                    is_active = True
                    if 'is_active' in row and row['is_active']:
                        is_active = row['is_active'].lower() in ('true', 'yes', '1', 't', 'y')

                    # Get or create country if specified
                    country = None
                    if 'country' in row and row['country']:
                        try:
                            country = Country.objects.get(name=row['country'])
                        except Country.DoesNotExist:
                            try:
                                country = Country.objects.get(code=row['country'])
                            except Country.DoesNotExist:
                                result['errors'].append(f"Country not found: {row['country']}. Using null instead.")

                    # Parse data type flags
                    data_type_flags = {
                        'has_temperature': True,
                        'has_precipitation': True,
                        'has_humidity': True,
                        'has_wind': True,
                        'has_air_quality': False,
                        'has_soil_moisture': False,
                        'has_water_level': False
                    }

                    for flag_name in data_type_flags:
                        if flag_name in row and row[flag_name]:
                            data_type_flags[flag_name] = row[flag_name].lower() in ('true', 'yes', '1', 't', 'y')

                    # Create station_id if not provided
                    station_id = row.get('station_id')
                    if not station_id:
                        # Generate a station ID based on name and location
                        station_id = f"{row['name'].lower().replace(' ', '_')}_{location.y:.2f}_{location.x:.2f}"

                    # Create or update the station
                    station, created = WeatherStation.objects.update_or_create(
                        station_id=station_id,
                        defaults={
                            'name': row['name'],
                            'location': location,
                            'description': row.get('description', ''),
                            'altitude': altitude,
                            'is_active': is_active,
                            'date_installed': date_installed,
                            'date_decommissioned': date_decommissioned,
                            'country': country,
                            'region': row.get('region', ''),
                            **data_type_flags
                        }
                    )

                    result['success'] += 1

                except Exception as e:
                    result['error'] += 1
                    result['errors'].append(f"Error processing row: {row}. Error: {str(e)}")

        except Exception as e:
            result['errors'].append(f"Error processing file: {str(e)}")

        return result

    def process_climate_data_file(self, csv_file):
        """Process a CSV file containing climate data"""
        result = {
            'success': 0,
            'error': 0,
            'errors': [],
            'type': 'Climate Data'
        }

        try:
            decoded_file = csv_file.read().decode('utf-8')
            io_string = io.StringIO(decoded_file)
            reader = csv.DictReader(io_string)

            # Check for required fields - now supporting multiple station identifier options
            station_identifiers = ['station_name', 'station_id', 'station']
            has_station_identifier = any(field in reader.fieldnames for field in station_identifiers)

            if not (has_station_identifier and 'timestamp' in reader.fieldnames):
                result['errors'].append(f"Missing required fields: 'timestamp' and one of {station_identifiers}")
                return result

            # Get timezone for timestamps
            timezone = pytz.timezone('UTC')  # Default to UTC, adjust if needed

            for row in reader:
                try:
                    # Handle multiple station identifier options
                    station_identifier = None
                    for field in station_identifiers:
                        if field in row and row[field]:
                            station_identifier = row[field]
                            break

                    # Check for required fields
                    if not station_identifier or not row.get('timestamp'):
                        result['error'] += 1
                        result['errors'].append(f"Missing required data in row: {row}")
                        continue

                    # Find the station (try by station_id first, then by name)
                    try:
                        # First try getting by station_id
                        station = WeatherStation.objects.get(station_id=station_identifier)
                    except WeatherStation.DoesNotExist:
                        try:
                            # Then try by name
                            station = WeatherStation.objects.get(name=station_identifier)
                        except WeatherStation.DoesNotExist:
                            try:
                                # Finally try by ID if it looks like an integer
                                if station_identifier.isdigit():
                                    station = WeatherStation.objects.get(id=int(station_identifier))
                                else:
                                    raise WeatherStation.DoesNotExist()
                            except WeatherStation.DoesNotExist:
                                result['error'] += 1
                                result['errors'].append(f"Station not found: {station_identifier}")
                                continue

                    # Parse timestamp
                    try:
                        timestamp = parser.parse(row['timestamp'])
                        if timestamp.tzinfo is None:
                            timestamp = timezone.localize(timestamp)
                    except ValueError:
                        result['error'] += 1
                        result['errors'].append(f"Invalid timestamp in row: {row}")
                        continue

                    # Create climate data dictionary with defaults
                    climate_data = {
                        'station': station,
                        'timestamp': timestamp,
                        'year': timestamp.year,
                        'month': timestamp.month,
                        'data_quality': row.get('data_quality', 'medium'),
                    }

                    # Map numeric fields with proper conversion
                    numeric_fields = [
                        'temperature', 'humidity', 'precipitation', 'air_quality_index',
                        'wind_speed', 'wind_direction', 'barometric_pressure', 'cloud_cover',
                        'soil_moisture', 'water_level', 'uv_index'
                    ]

                    for field in numeric_fields:
                        if field in row and row[field]:
                            try:
                                climate_data[field] = float(row[field])
                            except ValueError:
                                # Skip this field if conversion fails, but log the error
                                result['errors'].append(f"Invalid {field} value in row: {row}. Skipping this field.")
                                pass

                    # Create or update the climate data
                    data, created = ClimateData.objects.update_or_create(
                        station=station,
                        timestamp=timestamp,
                        defaults=climate_data
                    )

                    result['success'] += 1

                except Exception as e:
                    result['error'] += 1
                    result['errors'].append(f"Error processing row: {row}. Error: {str(e)}")

        except Exception as e:
            result['errors'].append(f"Error processing file: {str(e)}")

        return result

    def process_weather_data_types_file(self, csv_file):
        """Process a CSV file containing weather data type definitions"""
        result = {
            'success': 0,
            'error': 0,
            'errors': [],
            'type': 'Weather Data Types'
        }

        try:
            decoded_file = csv_file.read().decode('utf-8')
            io_string = io.StringIO(decoded_file)
            reader = csv.DictReader(io_string)

            # Check for required fields
            if 'name' not in reader.fieldnames:
                result['errors'].append("Missing required field: 'name'")
                return result

            for row in reader:
                try:
                    # Check for required fields
                    if not row.get('name'):
                        result['error'] += 1
                        result['errors'].append(f"Missing name in row: {row}")
                        continue

                    # Parse numeric fields
                    min_value = None
                    if 'min_value' in row and row['min_value']:
                        try:
                            min_value = float(row['min_value'])
                        except ValueError:
                            result['errors'].append(f"Invalid min_value in row: {row}. Using null instead.")

                    max_value = None
                    if 'max_value' in row and row['max_value']:
                        try:
                            max_value = float(row['max_value'])
                        except ValueError:
                            result['errors'].append(f"Invalid max_value in row: {row}. Using null instead.")

                    # Create or update the weather data type
                    data_type, created = WeatherDataType.objects.update_or_create(
                        name=row['name'],
                        defaults={
                            'display_name': row.get('display_name', row['name']),
                            'description': row.get('description', ''),
                            'unit': row.get('unit', ''),
                            'min_value': min_value,
                            'max_value': max_value,
                            'icon': row.get('icon', '')
                        }
                    )

                    result['success'] += 1

                except Exception as e:
                    result['error'] += 1
                    result['errors'].append(f"Error processing row: {row}. Error: {str(e)}")

        except Exception as e:
            result['errors'].append(f"Error processing file: {str(e)}")

        return result

    def process_countries_file(self, csv_file):
        """Process a CSV file containing country information"""
        result = {
            'success': 0,
            'error': 0,
            'errors': [],
            'type': 'Countries'
        }

        try:
            decoded_file = csv_file.read().decode('utf-8')
            io_string = io.StringIO(decoded_file)
            reader = csv.DictReader(io_string)

            # Check for required fields
            if not ('name' in reader.fieldnames and 'code' in reader.fieldnames):
                result['errors'].append("Missing required fields: 'name' and 'code'")
                return result

            for row in reader:
                try:
                    # Check for required fields
                    if not row.get('name') or not row.get('code'):
                        result['error'] += 1
                        result['errors'].append(f"Missing name or code in row: {row}")
                        continue

                    # Create or update the country
                    country, created = Country.objects.update_or_create(
                        code=row['code'],
                        defaults={
                            'name': row['name']
                        }
                    )

                    result['success'] += 1

                except Exception as e:
                    result['error'] += 1
                    result['errors'].append(f"Error processing row: {row}. Error: {str(e)}")

        except Exception as e:
            result['errors'].append(f"Error processing file: {str(e)}")

        return result


class ImportSuccessView(View):
    template_name = 'maps/import_success.html'

    def get(self, request):
        # Get results from session
        results = request.session.get('import_results', {})
        
        # Clear results from session after displaying
        if 'import_results' in request.session:
            del request.session['import_results']
            
        return render(request, self.template_name, {
            'results': results,
            'has_results': bool(results)
        })


@api_view(['POST'])
# @permission_classes([IsAdminUser])
def api_import_csv(request):
    """API endpoint for importing CSV data"""
    if 'file' not in request.FILES:
        return Response({'error': 'No file provided'}, status=400)

    import_type = request.data.get('import_type', 'stations')
    csv_file = request.FILES['file']

    # Process the file
    view = CSVUploadView()
    if import_type == 'stations':
        result = view.process_stations_file(csv_file)
    elif import_type == 'climate_data':
        result = view.process_climate_data_file(csv_file)
    elif import_type == 'weather_data_types':
        result = view.process_weather_data_types_file(csv_file)
    elif import_type == 'countries':
        result = view.process_countries_file(csv_file)
    else:
        result = {'error': 'Invalid import type'}

    return Response(result)


def is_safe_path(base_dir, path):
    """
    Validate that the provided path is within an acceptable base directory
    to prevent directory traversal attacks
    """
    # Convert to absolute paths
    base_dir_abs = os.path.abspath(base_dir)
    path_abs = os.path.abspath(path)

    # Check if path is within base directory
    return os.path.commonpath([base_dir_abs]) == os.path.commonpath([base_dir_abs, path_abs])


# @login_required
def flash_drive_import_view(request):
    """View to import data from a flash drive with improved security and error handling"""
    if request.method == 'POST':
        form = FlashDriveImportForm(request.POST)
        if form.is_valid():
            import_type = form.cleaned_data['import_type']
            drive_path = form.cleaned_data['drive_path']

            # Security check: Validate the path is within acceptable boundaries
            if not is_safe_path('/media', drive_path):
                messages.error(request, "Invalid drive path. Path must be within the media directory.")
                return render(request, 'maps/flash_drive_import.html', {'form': form})

            # Verify the path exists
            if not os.path.exists(drive_path):
                messages.error(request, f"The specified path does not exist: {drive_path}")
                return render(request, 'maps/flash_drive_import.html', {'form': form})

            # Look for CSV files in the directory
            try:
                csv_files = [f for f in os.listdir(drive_path) if f.endswith('.csv')]
            except PermissionError:
                messages.error(request, f"Permission denied when trying to access: {drive_path}")
                return render(request, 'maps/flash_drive_import.html', {'form': form})
            except Exception as e:
                messages.error(request, f"Error accessing directory: {str(e)}")
                return render(request, 'maps/flash_drive_import.html', {'form': form})

            if not csv_files:
                messages.error(request, "No CSV files found in the specified directory.")
                return render(request, 'maps/flash_drive_import.html', {'form': form})

            # Process all CSV files
            results = {
                'success_total': 0,
                'error_total': 0,
                'file_results': []
            }

            for csv_file_name in csv_files:
                file_path = os.path.join(drive_path, csv_file_name)

                # Process the file with multiple encoding attempts
                try:
                    with open(file_path, 'r', encoding='utf-8') as file:
                        file_content = file.read()
                except UnicodeDecodeError:
                    try:
                        # Try another common encoding
                        with open(file_path, 'r', encoding='latin-1') as file:
                            file_content = file.read()
                    except UnicodeDecodeError:
                        # If both fail, report error and skip file
                        result = {
                            'file_name': csv_file_name,
                            'success': 0,
                            'error': 1,
                            'errors': [
                                "Could not decode file encoding. Please ensure it's properly encoded (UTF-8 or Latin-1)."]
                        }
                        results['error_total'] += 1
                        results['file_results'].append(result)
                        continue
                except Exception as e:
                    # Handle other file access errors
                    result = {
                        'file_name': csv_file_name,
                        'success': 0,
                        'error': 1,
                        'errors': [f"Error reading file: {str(e)}"]
                    }
                    results['error_total'] += 1
                    results['file_results'].append(result)
                    continue

                # Create a file-like object
                file_object = io.StringIO(file_content)
                file_object.name = csv_file_name

                # Process the file based on import type or filename prefix
                csv_view = CSVUploadView()

                if import_type != 'auto':
                    # Use the specified import type
                    if import_type == 'stations':
                        result = csv_view.process_stations_file(file_object)
                    elif import_type == 'climate_data':
                        driveresult = csv_view.process_climate_data_file(file_object)
                    elif import_type == 'weather_data_types':
                        result = csv_view.process_weather_data_types_file(file_object)
                    elif import_type == 'countries':
                        result = csv_view.process_countries_file(file_object)
                else:
                    # Auto-detect based on filename prefix
                    if csv_file_name.startswith('station_'):
                        result = csv_view.process_stations_file(file_object)
                    elif csv_file_name.startswith('climate_'):
                        result = csv_view.process_climate_data_file(file_object)
                    elif csv_file_name.startswith('datatype_'):
                        result = csv_view.process_weather_data_types_file(file_object)
                    elif csv_file_name.startswith('country_'):
                        result = csv_view.process_countries_file(file_object)
                    else:
                        # Default to climate data
                        result = csv_view.process_climate_data_file(file_object)

                # Add file name to the result
                result['file_name'] = csv_file_name

                # Update totals
                results['success_total'] += result['success']
                results['error_total'] += result['error']

                # Add to file results
                results['file_results'].append(result)

                # Option to move or rename processed files to avoid reimporting
                try:
                    if result['success'] > 0:
                        processed_path = file_path + '.processed'
                        # Only move if we don't already have a processed file
                        if not os.path.exists(processed_path):
                            os.rename(file_path, processed_path)
                except Exception as e:
                    # Log but don't fail if we can't move the file
                    logging.warning(f"Could not mark file as processed: {str(e)}")

            # Store results in session for display
            request.session['import_results'] = results

            return redirect('maps:import_success')
    else:
        form = FlashDriveImportForm()

    return render(request, 'maps/flash_drive_import.html', {'form': form})


def setup_automated_imports(flash_drive_path=None, import_interval=3600):
    """
    Set up scheduled imports from a flash drive with improved configuration and error handling

    This function should be called from your app's AppConfig.ready() method
    to set up scheduled tasks when the application starts.

    Parameters:
    - flash_drive_path: Path to monitor for CSV files, defaults to environment variable or '/media/usb'
    - import_interval: How often to check for new files (in seconds), defaults to environment variable or 3600
    """
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
        import logging

        # Use environment variables if path not provided
        if flash_drive_path is None:
            flash_drive_path = os.environ.get('FLASH_DRIVE_PATH', '/media/usb')

        # Use environment variable for interval if not provided
        if import_interval is None:
            import_interval = int(os.environ.get('IMPORT_INTERVAL', '3600'))

        def import_task():
            """Task to import CSV files from the flash drive path"""
            logging.info(f"Running scheduled import from {flash_drive_path}")
            try:
                # Security check for the path
                if not is_safe_path('/media', flash_drive_path):
                    logging.error(f"Unsafe path detected: {flash_drive_path}")
                    return

                if os.path.exists(flash_drive_path):
                    # Look for CSV files
                    try:
                        csv_files = [f for f in os.listdir(flash_drive_path) if f.endswith('.csv')]
                    except PermissionError:
                        logging.error(f"Permission denied when accessing {flash_drive_path}")
                        return
                    except Exception as e:
                        logging.error(f"Error accessing directory {flash_drive_path}: {str(e)}")
                        return

                    # Process files in appropriate order
                    process_files(flash_drive_path, 'country_', 'countries', csv_files)
                    process_files(flash_drive_path, 'datatype_', 'weather_data_types', csv_files)
                    process_files(flash_drive_path, 'station_', 'stations', csv_files)
                    process_files(flash_drive_path, 'climate_', 'climate_data', csv_files)
                else:
                    logging.warning(f"Flash drive path {flash_drive_path} does not exist")
            except Exception as e:
                logging.error(f"Error in import task: {str(e)}")

        def process_files(path, prefix, import_type, all_files):
            """Process specific types of CSV files"""
            # Filter files by prefix
            filtered_files = [f for f in all_files if f.startswith(prefix)]

            for file_name in filtered_files:
                try:
                    file_path = os.path.join(path, file_name)

                    # Try different encodings
                    try:
                        with open(file_path, 'r', encoding='utf-8') as file:
                            file_content = file.read()
                    except UnicodeDecodeError:
                        try:
                            with open(file_path, 'r', encoding='latin-1') as file:
                                file_content = file.read()
                        except UnicodeDecodeError:
                            logging.error(f"Cannot decode file {file_name}: Encoding issues")
                            continue

                    # Create a file-like object
                    file_object = io.StringIO(file_content)
                    file_object.name = file_name

                    # Process the file based on import type
                    csv_view = CSVUploadView()
                    if import_type == 'stations':
                        result = csv_view.process_stations_file(file_object)
                    elif import_type == 'climate_data':
                        result = csv_view.process_climate_data_file(file_object)
                    elif import_type == 'weather_data_types':
                        result = csv_view.process_weather_data_types_file(file_object)
                    elif import_type == 'countries':
                        result = csv_view.process_countries_file(file_object)
                    else:
                        logging.error(f"Unknown import type: {import_type}")
                        continue

                    # Log the result
                    logging.info(f"Imported {file_name}: {result['success']} success, {result['error']} errors")

                    # Move processed file to avoid reimporting
                    processed_path = file_path + '.processed'
                    if result['success'] > 0 and not os.path.exists(processed_path):
                        os.rename(file_path, processed_path)
                except Exception as e:
                    logging.error(f"Error processing {file_name}: {str(e)}")

        # Create and start the scheduler
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            import_task,
            trigger=IntervalTrigger(seconds=import_interval),
            id='csv_import_job',
            replace_existing=True
        )
        scheduler.start()
        logging.info(f"Scheduled automated CSV imports from {flash_drive_path} every {import_interval} seconds")
        
    except ImportError:
        logging.warning("APScheduler not installed. Automated imports will not run.")
    except Exception as e:
        logging.error(f"Error setting up automated imports: {str(e)}")


def station_statistics_view(request, station_id):
    """View for displaying station statistics in a template"""
    try:
        station = WeatherStation.objects.get(pk=station_id)
    except WeatherStation.DoesNotExist:
        messages.error(request, f"Weather station with ID {station_id} does not exist.")
        return redirect('maps:map')
    
    # Calculate days for different time periods
    days_30 = timezone.now() - timedelta(days=30)
    days_90 = timezone.now() - timedelta(days=90)
    days_365 = timezone.now() - timedelta(days=365)
    
    # Get statistics for different time periods
    stats_30 = ClimateData.objects.filter(
        station=station,
        timestamp__gte=days_30
    ).aggregate(
        avg_temp=Avg('temperature'),
        max_temp=Max('temperature'),
        min_temp=Min('temperature'),
        avg_humidity=Avg('humidity'),
        total_precipitation=Sum('precipitation', default=0),
        avg_wind_speed=Avg('wind_speed'),
        max_wind_speed=Max('wind_speed'),
        avg_pressure=Avg('barometric_pressure'),
        max_uv=Max('uv_index')
    )
    
    stats_90 = ClimateData.objects.filter(
        station=station,
        timestamp__gte=days_90
    ).aggregate(
        avg_temp=Avg('temperature'),
        max_temp=Max('temperature'),
        min_temp=Min('temperature'),
        avg_humidity=Avg('humidity'),
        total_precipitation=Sum('precipitation', default=0)
    )
    
    stats_365 = ClimateData.objects.filter(
        station=station,
        timestamp__gte=days_365
    ).aggregate(
        avg_temp=Avg('temperature'),
        total_precipitation=Sum('precipitation', default=0)
    )
    
    # Get recent climate data points for this station
    recent_data = ClimateData.objects.filter(
        station=station
    ).order_by('-timestamp')[:100]
    
    context = {
        'station': station,
        'stats_30': stats_30,
        'stats_90': stats_90,
        'stats_365': stats_365,
        'recent_data': recent_data,
        'current_year': timezone.now().year,
    }
    
    return render(request, 'maps/station_statistics.html', context)

def station_data_export_view(request, station_id):
    """View for station data export options"""
    try:
        station = WeatherStation.objects.get(pk=station_id)
    except WeatherStation.DoesNotExist:
        messages.error(request, f"Weather station with ID {station_id} does not exist.")
        return redirect('maps:map')
    
    # Calculate data availability
    earliest_record = ClimateData.objects.filter(station=station).order_by('timestamp').first()
    latest_record = ClimateData.objects.filter(station=station).order_by('-timestamp').first()
    record_count = ClimateData.objects.filter(station=station).count()
    
    # Get data types that are actually available for this station
    data_types = []
    data_type_fields = [
        'temperature', 'humidity', 'precipitation', 'wind_speed', 
        'wind_direction', 'barometric_pressure', 'air_quality_index',
        'cloud_cover', 'soil_moisture', 'water_level', 'uv_index'
    ]
    
    for field in data_type_fields:
        # Check if field exists in data and is not all null
        if ClimateData.objects.filter(station=station).exclude(**{field: None}).exists():
            # Get the display name if available, otherwise use the field name
            try:
                data_type = WeatherDataType.objects.get(name=field)
                display_name = data_type.display_name
                unit = data_type.unit
            except WeatherDataType.DoesNotExist:
                display_name = field.replace('_', ' ').title()
                unit = ''
                
            data_types.append({
                'field': field,
                'display_name': display_name,
                'unit': unit
            })
    
    context = {
        'station': station,
        'earliest_record': earliest_record,
        'latest_record': latest_record,
        'record_count': record_count,
        'data_types': data_types,
    }
    
    return render(request, 'maps/station_data_export.html', context)

def download_station_data(request, station_id):
    """Download station data in the specified format"""
    try:
        station = WeatherStation.objects.get(pk=station_id)
    except WeatherStation.DoesNotExist:
        messages.error(request, f"Weather station with ID {station_id} does not exist.")
        return redirect('maps:map')
    
    # Get parameters from query
    format_type = request.GET.get('format', 'csv').lower()
    date_from_str = request.GET.get('date_from')
    date_to_str = request.GET.get('date_to')
    selected_fields = request.GET.getlist('fields')
    
    # Default fields if none selected
    if not selected_fields:
        selected_fields = ['timestamp', 'temperature', 'humidity', 'precipitation']
    
    # Always include timestamp
    if 'timestamp' not in selected_fields:
        selected_fields = ['timestamp'] + selected_fields
    
    # Parse dates if provided
    filters = {'station': station}
    
    # Default date range - last 30 days if not specified
    end_date = timezone.now()
    start_date = end_date - timedelta(days=30)
    
    if date_from_str:
        try:
            start_date = parser.parse(date_from_str)
            if timezone.is_naive(start_date):
                start_date = timezone.make_aware(start_date)
            filters['timestamp__gte'] = start_date
        except ValueError:
            messages.warning(request, f"Invalid from date format: {date_from_str}. Using default.")
    
    if date_to_str:
        try:
            end_date = parser.parse(date_to_str)
            if timezone.is_naive(end_date):
                end_date = timezone.make_aware(end_date)
            filters['timestamp__lte'] = end_date
        except ValueError:
            messages.warning(request, f"Invalid to date format: {date_to_str}. Using default.")
    
    # Always add date filters even if not provided in request
    filters['timestamp__gte'] = filters.get('timestamp__gte', start_date)
    filters['timestamp__lte'] = filters.get('timestamp__lte', end_date)
    
    # Query the data
    data = ClimateData.objects.filter(**filters).order_by('-timestamp')
    
    # Create filename with date info
    date_str = timezone.now().strftime("%Y%m%d")
    filename = f"{station.name.replace(' ', '_')}_{date_str}"
    
    # Record the export if user is authenticated
    if request.user.is_authenticated:
        # Create the DataExport object with proper date_from and date_to values
        data_export = DataExport.objects.create(
            user=request.user,
            export_format=format_type,
            date_from=filters['timestamp__gte'],
            date_to=filters['timestamp__lte'],
            status='completed'
        )
        # Now add the station to the many-to-many relationship
        data_export.stations.add(station)
    
    if format_type == 'csv':
        # Create CSV response
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}.csv"'
        
        writer = csv.writer(response)
        
        # Write headers
        headers = ['Station Name', 'Station ID'] + [field for field in selected_fields]
        writer.writerow(headers)
        
        # Write data rows
        for record in data:
            row = [
                station.name,
                station.station_id or station.id,
            ]
            
            for field in selected_fields:
                if field == 'timestamp':
                    value = record.timestamp.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    value = getattr(record, field, '')
                row.append(value)
                
            writer.writerow(row)
            
        return response
        
    elif format_type == 'excel':
        # Create Excel file in memory
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output)
        worksheet = workbook.add_worksheet('Station Data')
        
        # Add bold format for headers
        bold = workbook.add_format({'bold': True})
        
        # Write headers
        headers = ['Station Name', 'Station ID'] + [field for field in selected_fields]
        for col, header in enumerate(headers):
            worksheet.write(0, col, header, bold)
        
        # Write data rows
        for row_idx, record in enumerate(data, start=1):
            worksheet.write(row_idx, 0, station.name)
            worksheet.write(row_idx, 1, station.station_id or str(station.id))
            
            for col_idx, field in enumerate(selected_fields, start=2):
                if field == 'timestamp':
                    value = record.timestamp.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    value = getattr(record, field, '')
                worksheet.write(row_idx, col_idx, value)
        
        workbook.close()
        
        # Prepare the response
        output.seek(0)
        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}.xlsx"'
        return response
        
    elif format_type == 'json':
        # Create list of dictionaries
        result = []
        for record in data:
            item = {
                'station_name': station.name,
                'station_id': station.station_id or station.id
            }
            
            for field in selected_fields:
                if field == 'timestamp':
                    item[field] = record.timestamp.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    item[field] = getattr(record, field, None)
                    
            result.append(item)
            
        # Create JSON response
        response = HttpResponse(json.dumps(result, default=str), content_type='application/json')
        response['Content-Disposition'] = f'attachment; filename="{filename}.json"'
        return response
    
    else:
        messages.error(request, f"Unsupported format: {format_type}")
        return redirect('maps:station_data_export', station_id=station_id)

class ClimateDataViewSet(viewsets.ModelViewSet):
    queryset = ClimateData.objects.all().select_related('station')
    serializer_class = ClimateDataSerializer
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['station', 'data_quality', 'year', 'month']
    ordering_fields = ['timestamp', 'temperature', 'precipitation']
    
    def get_serializer_class(self):
        if self.request.query_params.get('format') == 'geojson':
            return GeoJSONClimateDataSerializer
        return ClimateDataSerializer
    
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = self.get_serializer(queryset, many=True)
        
        # If GeoJSON format is requested, wrap in a FeatureCollection
        if request.query_params.get('format') == 'geojson':
            return Response({
                'type': 'FeatureCollection',
                'features': serializer.data
            })
        
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def recent(self, request):
        """Get the most recent climate data for all stations"""
        hours = int(request.query_params.get('hours', 24))
        data_types = request.query_params.getlist('data_types', [])
        since = timezone.now() - timedelta(hours=hours)
        
        # Get latest reading for each station
        subquery = ClimateData.objects.filter(
            station=OuterRef('station'),
            timestamp__gte=since
        ).order_by('-timestamp').values('id')[:1]
        
        query = ClimateData.objects.filter(
            id__in=Subquery(subquery)
        ).select_related('station')
        
        # Filter by data types if specified
        if data_types:
            conditions = []
            for data_type in data_types:
                if hasattr(ClimateData, data_type):
                    conditions.append(~models.Q(**{data_type: None}))
            if conditions:
                query = query.filter(reduce(operator.or_, conditions))
        
        serializer = self.get_serializer(query, many=True)
        return Response(serializer.data)

    def perform_create(self, serializer):
        """Override create to check for alerts when new data is added"""
        climate_data = serializer.save()
        
        # Check for potential alerts
        alerts = check_for_alerts(climate_data)
        
        # Create alerts and send notifications
        for alert_data in alerts:
            alert = create_alert_from_detection(climate_data.station, alert_data)
            send_alert_notifications(alert)
        
        return climate_data

@api_view(['POST'])
def push_station_data(request, station_id):
    """API endpoint for pushing data to a station's stack"""
    try:
        station = get_object_or_404(WeatherStation, pk=station_id)
        serializer = StackedDataSerializer(data=request.data)
        
        if serializer.is_valid():
            success = station.push_data(serializer.validated_data)
            if success:
                return Response({
                    'success': True, 
                    'stack_size': station.stack_size(),
                    'message': 'Data added to stack successfully'
                })
            else:
                return Response({
                    'success': False,
                    'message': 'Stack is full'
                }, status=400)
        else:
            return Response(serializer.errors, status=400)
    except Exception as e:
        return Response({'error': str(e)}, status=500)

@api_view(['POST'])
def process_station_stack(request, station_id):
    """API endpoint for processing a station's data stack"""
    try:
        station = get_object_or_404(WeatherStation, pk=station_id)
        records_processed = station.process_data_stack()
        
        return Response({
            'success': True,
            'records_processed': records_processed,
            'message': f'Successfully processed {records_processed} records'
        })
    except Exception as e:
        return Response({'error': str(e)}, status=500)

@api_view(['GET'])
def get_stack_info(request, station_id):
    """API endpoint for getting information about a station's data stack"""
    try:
        station = get_object_or_404(WeatherStation, pk=station_id)
        latest_data = station.peek_data()
        
        data = {
            'station_id': station.id,
            'station_name': station.name,
            'stack_size': station.stack_size(),
            'max_stack_size': station.max_stack_size,
            'last_data_feed': station.last_data_feed,
            'auto_process': station.auto_process,
            'process_threshold': station.process_threshold,
            'latest_data': latest_data
        }
        
        serializer = StackInfoSerializer(data)
        return Response(serializer.data)
    except Exception as e:
        return Response({'error': str(e)}, status=500)

@api_view(['POST'])
def clear_station_stack(request, station_id):
    """API endpoint for clearing a station's data stack"""
    try:
        station = get_object_or_404(WeatherStation, pk=station_id)
        import json
        
        # Get current stack size for reporting
        stack_size = station.stack_size()
        
        # Clear the stack
        station.data_stack = json.dumps([])
        station.save(update_fields=['data_stack'])
        
        return Response({
            'success': True,
            'cleared_items': stack_size,
            'message': f'Successfully cleared {stack_size} items from the stack'
        })
    except Exception as e:
        return Response({'error': str(e)}, status=500)

def station_data_stack_view(request, station_id):
    """View for managing a station's data stack in a template"""
    try:
        station = get_object_or_404(WeatherStation, pk=station_id)
        
        # Handle stack configuration updates if form submitted
        if request.method == 'POST':
            from .forms import DataStackSettingsForm
            form = DataStackSettingsForm(request.POST, instance=station)
            if form.is_valid():
                form.save()
                messages.success(request, "Data stack settings updated successfully")
            else:
                for field, errors in form.errors.items():
                    for error in errors:
                        messages.error(request, f"{field}: {error}")
        else:
            from .forms import DataStackSettingsForm
            form = DataStackSettingsForm(instance=station)
        
        # Get stack data for display
        try:
            import json
            stack_data = json.loads(station.data_stack) if station.data_stack else []
            # Limit to last 50 items to prevent overwhelming the page
            stack_preview = stack_data[-50:] if len(stack_data) > 50 else stack_data
        except:
            stack_preview = []
        
        context = {
            'station': station,
            'stack_size': station.stack_size(),
            'max_stack_size': station.max_stack_size,
            'form': form,
            'stack_preview': stack_preview,
            'has_data': len(stack_preview) > 0,
            'last_data_feed': station.last_data_feed,
        }
        
        return render(request, 'maps/station_data_stack.html', context)
        
    except WeatherStation.DoesNotExist:
        messages.error(request, f"Weather station with ID {station_id} does not exist.")
        return redirect('maps:map')

@api_view(['GET'])
def climate_data_recent(request):
    """Get recent climate data for all stations"""
    hours = int(request.query_params.get('hours', 24))
    since = timezone.now() - timedelta(hours=hours)
    
    # Get latest reading for each station
    climate_data = []
    stations = WeatherStation.objects.filter(is_active=True)
    
    for station in stations:
        latest = ClimateData.objects.filter(
            station=station,
            timestamp__gte=since
        ).order_by('-timestamp').first()
        
        if latest:
            climate_data.append(latest)
    
    serializer = ClimateDataSerializer(climate_data, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def station_climate_data(request, station_id):
    """Get climate data for a specific station - fallback endpoint for the map JS"""
    try:
        station = get_object_or_404(WeatherStation, pk=station_id)
        days = int(request.query_params.get('days', 7))
        start_date = timezone.now() - timedelta(days=days)
        
        data = ClimateData.objects.filter(
            station=station,
            timestamp__gte=start_date
        ).order_by('-timestamp')
        
        serializer = ClimateDataSerializer(data, many=True)
        return Response(serializer.data)
    except Exception as e:
        return Response({'error': str(e)}, status=500)


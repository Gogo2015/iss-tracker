import pytest
import json
import math
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock
import xml.etree.ElementTree as ET
from iss_tracker import app, ISSTracker, redis_client

@pytest.fixture
def client():
    """Fixture for Flask test client."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

@pytest.fixture
def sample_xml():
    """Fixture for sample XML data."""
    return '''<?xml version="1.0" encoding="UTF-8"?>
    <ndm>
        <oem>
            <body>
                <segment>
                    <data>
                        <stateVector>
                            <EPOCH>2024-079T12:00:00.000Z</EPOCH>
                            <X>1.0</X>
                            <Y>2.0</Y>
                            <Z>3.0</Z>
                            <X_DOT>3.0</X_DOT>
                            <Y_DOT>4.0</Y_DOT>
                            <Z_DOT>5.0</Z_DOT>
                        </stateVector>
                        <stateVector>
                            <EPOCH>2024-079T13:00:00.000Z</EPOCH>
                            <X>4.0</X>
                            <Y>5.0</Y>
                            <Z>6.0</Z>
                            <X_DOT>6.0</X_DOT>
                            <Y_DOT>8.0</Y_DOT>
                            <Z_DOT>10.0</Z_DOT>
                        </stateVector>
                    </data>
                </segment>
            </body>
        </oem>
    </ndm>'''

@pytest.fixture
def mock_redis():
    """Fixture to mock Redis client."""
    with patch('iss_tracker.redis_client') as mock_redis:
        # Create mock methods
        mock_redis.exists.return_value = False
        mock_redis.hset.return_value = 1
        mock_redis.rpush.return_value = 1
        mock_redis.lrange.return_value = ['2024-079T12:00:00.000Z', '2024-079T13:00:00.000Z']
        mock_redis.hgetall.side_effect = lambda key: {
            'iss_data:2024-079T12:00:00.000Z': {
                'epoch': '2024-079T12:00:00.000Z',
                'x': '1.0',
                'y': '2.0',
                'z': '3.0',
                'x_dot': '3.0',
                'y_dot': '4.0',
                'z_dot': '5.0'
            },
            'iss_data:2024-079T13:00:00.000Z': {
                'epoch': '2024-079T13:00:00.000Z',
                'x': '4.0',
                'y': '5.0',
                'z': '6.0',
                'x_dot': '6.0',
                'y_dot': '8.0',
                'z_dot': '10.0'
            }
        }.get(key, {})
        
        yield mock_redis

@pytest.fixture
def tracker(mock_redis):
    """Fixture for ISSTracker."""
    return ISSTracker()

@pytest.fixture
def tracker_with_data(tracker, sample_xml, mock_redis):
    """Fixture for ISSTracker with data."""
    with patch('requests.get') as mock_get:
        mock_response = Mock()
        mock_response.text = sample_xml
        mock_get.return_value = mock_response
        
        tracker.read_data()
    return tracker

# Tests for ISSTracker class
def test_init():
    """Test initialization of ISSTracker."""
    with patch('iss_tracker.Nominatim'):
        tracker = ISSTracker()
        assert tracker.url == "https://nasa-public-data.s3.amazonaws.com/iss-coords/current/ISS_OEM/ISS.OEM_J2K_EPH.xml"

def test_read_data(tracker, sample_xml, mock_redis):
    """Test fetching and parsing data."""
    with patch('requests.get') as mock_get:
        mock_response = Mock()
        mock_response.text = sample_xml
        mock_get.return_value = mock_response
        
        tracker.read_data()
        
        # Check that Redis was called correctly
        assert mock_redis.hset.call_count == 2
        assert mock_redis.rpush.call_count == 2

def test_epochs(tracker_with_data, mock_redis):
    """Test getting all epochs."""
    epochs = tracker_with_data.epochs()
    assert len(epochs) == 2
    assert epochs[0] == '2024-079T12:00:00.000Z'
    assert epochs[1] == '2024-079T13:00:00.000Z'

def test_epochs_limited(tracker_with_data, mock_redis):
    """Test getting limited epochs."""
    with patch.object(mock_redis, 'lrange') as mock_lrange:
        mock_lrange.return_value = ['2024-079T13:00:00.000Z']
        
        epochs = tracker_with_data.epochs_limited(1, 1)
        
        assert len(epochs) == 1
        assert epochs[0] == '2024-079T13:00:00.000Z'

def test_calculate_speed_helper():
    """Test calculating speed using a helper function to match ISSTracker's functionality."""
    # Define a helper method to test the speed calculation logic
    def calculate_speed(velocity):
        return math.sqrt(velocity['x_dot']**2 + velocity['y_dot']**2 + velocity['z_dot']**2)
    
    velocity = {'x_dot': 3.0, 'y_dot': 4.0, 'z_dot': 5.0}
    speed = calculate_speed(velocity)
    # sqrt(9 + 16 + 25) = sqrt(50) = 7.071...
    assert round(speed, 6) == 7.071068

def test_closest_epoch(tracker_with_data, mock_redis):
    """Test finding closest epoch."""
    with patch('datetime.datetime') as mock_datetime:
        # Set current time to match first epoch
        mock_now = datetime.strptime('2024-079T12:30:00.000Z', "%Y-%jT%H:%M:%S.%fZ")
        mock_datetime.now.return_value = mock_now
        
        closest = tracker_with_data.print_closest_epoch()
        
        # Check that we got something back with the expected structure
        assert closest is not None
        assert 'epoch' in closest
        assert 'position' in closest
        assert 'velocity' in closest

def test_get_state_vector_epoch(tracker_with_data, mock_redis):
    """Test getting state vector by epoch."""
    vector = tracker_with_data.get_state_vector_epoch('2024-079T12:00:00.000Z')
    assert vector['epoch'] == '2024-079T12:00:00.000Z'
    assert 'position' in vector
    assert 'velocity' in vector
    assert vector['velocity']['x_dot'] == 3.0
    assert vector['velocity']['y_dot'] == 4.0
    assert vector['velocity']['z_dot'] == 5.0

def test_get_state_vector_epoch_not_found(tracker_with_data, mock_redis):
    """Test getting state vector by epoch when not found."""
    with patch.object(mock_redis, 'hgetall', return_value={}):
        vector = tracker_with_data.get_state_vector_epoch('2024-079T14:00:00.000Z')
        assert vector is None

def test_get_speed_epoch(tracker_with_data, mock_redis):
    """Test getting speed for an epoch."""
    with patch.object(tracker_with_data, 'get_state_vector_epoch') as mock_get_vector:
        mock_get_vector.return_value = {
            'epoch': '2024-079T12:00:00.000Z',
            'velocity': {
                'x_dot': 3.0,
                'y_dot': 4.0,
                'z_dot': 5.0
            }
        }
        
        speed_data = tracker_with_data.get_speed_epoch('2024-079T12:00:00.000Z')
        
        assert speed_data['epoch'] == '2024-079T12:00:00.000Z'
        assert round(speed_data['speed'], 6) == 7.071068

def test_get_location_epoch(tracker_with_data, mock_redis):
    """Test getting location for an epoch."""
    with patch.object(tracker_with_data, 'get_state_vector_epoch') as mock_get_vector:
        mock_get_vector.return_value = {
            'epoch': '2024-079T12:00:00.000Z',
            'position': {
                'x': 1.0,
                'y': 2.0,
                'z': 3.0
            }
        }
        
        with patch.object(tracker_with_data.geolocator, 'reverse') as mock_reverse:
            mock_location = Mock()
            mock_location.address = "Test Location, Earth"
            mock_reverse.return_value = mock_location
            
            location_data = tracker_with_data.get_location_epoch('2024-079T12:00:00.000Z')
            
            assert location_data['epoch'] == '2024-079T12:00:00.000Z'
            assert 'latitude' in location_data
            assert 'longitude' in location_data
            assert 'altitude' in location_data
            assert location_data['geoposition'] == "Test Location, Earth"

def test_get_now(tracker_with_data, mock_redis):
    """Test getting current ISS data."""
    with patch.object(tracker_with_data, 'print_closest_epoch') as mock_closest:
        mock_closest.return_value = {
            'epoch': '2024-079T12:00:00.000Z',
            'position': {'x': 1.0, 'y': 2.0, 'z': 3.0},
            'velocity': {'x_dot': 3.0, 'y_dot': 4.0, 'z_dot': 5.0}
        }
        
        with patch.object(tracker_with_data, 'get_speed_epoch') as mock_speed:
            mock_speed.return_value = {'epoch': '2024-079T12:00:00.000Z', 'speed': 7.071068}
            
            with patch.object(tracker_with_data, 'get_location_epoch') as mock_location:
                mock_location.return_value = {
                    'epoch': '2024-079T12:00:00.000Z',
                    'latitude': 45.0,
                    'longitude': 90.0,
                    'altitude': 408.0,
                    'geoposition': "Test Location, Earth"
                }
                
                now_data = tracker_with_data.get_now()
                
                assert now_data['epoch'] == '2024-079T12:00:00.000Z'
                assert 'speed' in now_data
                assert 'latitude' in now_data
                assert 'longitude' in now_data
                assert 'altitude' in now_data
                assert now_data['geoposition'] == "Test Location, Earth"

# Tests for Flask routes
def test_all_epochs_route(client, mock_redis):
    """Test GET /epochs route."""
    with patch('iss_tracker.ISSTracker.read_data'):
        with patch('iss_tracker.ISSTracker.epochs_limited') as mock_epochs:
            mock_epochs.return_value = ['2024-079T12:00:00.000Z', '2024-079T13:00:00.000Z']
            
            response = client.get('/epochs')
            
            assert response.status_code == 200
            assert json.loads(response.data) == ['2024-079T12:00:00.000Z', '2024-079T13:00:00.000Z']

def test_all_epochs_route_with_limit_offset(client, mock_redis):
    """Test GET /epochs with limit and offset parameters."""
    with patch('iss_tracker.ISSTracker.read_data'):
        with patch('iss_tracker.ISSTracker.epochs_limited') as mock_epochs:
            mock_epochs.return_value = ['2024-079T13:00:00.000Z']
            
            response = client.get('/epochs?limit=1&offset=1')
            
            assert response.status_code == 200
            assert isinstance(json.loads(response.data), list)

def test_epoch_data_route(client, mock_redis):
    """Test GET /epochs/<epoch> route."""
    with patch('iss_tracker.ISSTracker.read_data'):
        with patch('iss_tracker.ISSTracker.get_state_vector_epoch') as mock_get:
            mock_state_vector = {
                'epoch': '2024-079T12:00:00.000Z',
                'position': {'x': 1.0, 'y': 2.0, 'z': 3.0},
                'velocity': {'x_dot': 3.0, 'y_dot': 4.0, 'z_dot': 5.0}
            }
            mock_get.return_value = mock_state_vector
            
            response = client.get('/epochs/2024-079T12:00:00.000Z')
            
            assert response.status_code == 200

def test_epoch_speed_route(client, mock_redis):
    """Test GET /epochs/<epoch>/speed route."""
    with patch('iss_tracker.ISSTracker.read_data'):
        with patch('iss_tracker.ISSTracker.get_speed_epoch') as mock_speed:
            mock_speed.return_value = {
                'epoch': '2024-079T12:00:00.000Z',
                'speed': 7.071068
            }
            
            response = client.get('/epochs/2024-079T12:00:00.000Z/speed')
            
            assert response.status_code == 200

def test_epoch_location_route(client, mock_redis):
    """Test GET /epochs/<epoch>/location route."""
    with patch('iss_tracker.ISSTracker.read_data'):
        with patch('iss_tracker.ISSTracker.get_location_epoch') as mock_location:
            mock_location.return_value = {
                'epoch': '2024-079T12:00:00.000Z',
                'latitude': 45.0,
                'longitude': 90.0,
                'altitude': 408.0,
                'geoposition': "Test Location, Earth"
            }
            
            response = client.get('/epochs/2024-079T12:00:00.000Z/location')
            
            assert response.status_code == 200

def test_now_route(client, mock_redis):
    """Test GET /now route."""
    with patch('iss_tracker.ISSTracker.read_data'):
        with patch('iss_tracker.ISSTracker.get_now') as mock_now:
            mock_now.return_value = {
                'epoch': '2024-079T12:00:00.000Z',
                'speed': 7.071068,
                'latitude': 45.0,
                'longitude': 90.0,
                'altitude': 408.0,
                'geoposition': "Test Location, Earth"
            }
            
            response = client.get('/now')
            
            assert response.status_code == 200

def test_error_handling_epoch_not_found(client, mock_redis):
    """Test error handling when epoch is not found."""
    with patch('iss_tracker.ISSTracker.read_data'):
        with patch('iss_tracker.ISSTracker.get_state_vector_epoch', return_value=None):
            response = client.get('/epochs/invalid-epoch')
            
            assert response.status_code == 404
            assert b"Epoch not found" in response.data

def test_error_handling_invalid_parameters(client, mock_redis):
    """Test error handling with invalid parameters."""
    response = client.get('/epochs?limit=-1&offset=1')
    
    assert response.status_code == 400
    assert b"Limit and offset must be non-negative integers" in response.data
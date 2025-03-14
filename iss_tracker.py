import requests
from datetime import datetime
import math
import logging
import xml.etree.ElementTree as ET
from flask import Flask, request
import redis
import os
from geopy.geocoders import Nominatim

#Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Redis setup
redis_client = redis.Redis(host='redis-db', port=6379, db=0)

class ISSTracker:
    
    def __init__(self):
        self.url = "https://nasa-public-data.s3.amazonaws.com/iss-coords/current/ISS_OEM/ISS.OEM_J2K_EPH.xml"
        self.geolocator = Nominatim(user_agent="iss-tracker")

    def read_data(self):
        
        response = requests.get(self.url)
        root = ET.fromstring(response.text)

        for state_vector in root.findall(".//stateVector"):
            epoch = state_vector.findtext('EPOCH')
            
            # Store in Redis instead of list
            redis_client.hset(f"iss_data:{epoch}", mapping={
                'epoch': epoch,
                'x': float(state_vector.findtext('X')),
                'y': float(state_vector.findtext('Y')),
                'z': float(state_vector.findtext('Z')),
                'x_dot': float(state_vector.findtext('X_DOT')),
                'y_dot': float(state_vector.findtext('Y_DOT')),
                'z_dot': float(state_vector.findtext('Z_DOT'))
            })
            
            # Add to epochs list
            redis_client.rpush("iss_epochs", epoch)

    def print_time_range(self):
        epochs = self.epochs()
        if not epochs:
            print("No epoch data available")
            return
            
        start = datetime.strptime(epochs[0], "%Y-%jT%H:%M:%S.%fZ")
        end = datetime.strptime(epochs[-1], "%Y-%jT%H:%M:%S.%fZ")

        #Used AI here to generate printf to make it readable
        start_readable = start.strftime("%B %d, %Y at %I:%M:%S %p")
        end_readable = end.strftime("%B %d, %Y at %I:%M:%S %p")

        print("The data time range is from: ")
        print(start_readable + " to ")
        print(end_readable)

    def print_closest_epoch(self):
        now = datetime.now()
        epochs = self.epochs()
        
        if not epochs:
            return None

        #The datetime.strptime(...) is AI Generated
        timediffdata = [abs(datetime.strptime(epoch, "%Y-%jT%H:%M:%S.%fZ") - now) for epoch in epochs]

        #Horrible way computationally to find this by the way but simplest
        smallest_diff = min(timediffdata)
        closest_epoch = epochs[timediffdata.index(smallest_diff)]
        
        state_vector = self.get_state_vector_epoch(closest_epoch)
        
        return state_vector

    def print_speeds(self, closest):
        def calculate_speed(velocity):
            return math.sqrt(velocity['x_dot']**2 + velocity['y_dot']**2 + velocity['z_dot']**2)
    
        speeds = []
        for epoch in self.epochs():
            state_vector = self.get_state_vector_epoch(epoch)
            if state_vector:
                velocity = {
                    'x_dot': state_vector['velocity']['x_dot'],
                    'y_dot': state_vector['velocity']['y_dot'],
                    'z_dot': state_vector['velocity']['z_dot']
                }
                speeds.append(calculate_speed(velocity))
        
        if not speeds:
            print("No speed data available")
            return
        
        avg_speed = sum(speeds)/len(speeds)
        
        velocity = {
            'x_dot': closest['velocity']['x_dot'],
            'y_dot': closest['velocity']['y_dot'],
            'z_dot': closest['velocity']['z_dot']
        }
        instant_speed = calculate_speed(velocity)

        print("Average Speed over Whole Dataset: " + str(avg_speed))
        print("Instantaneous Speed: " + str(instant_speed))

    def analyze_data(self):
        self.read_data()
        self.print_time_range()
        closest = self.print_closest_epoch()
        self.print_speeds(closest)

    def epochs(self):
        # Make sure data is in Redis
        self.read_data()
        return redis_client.lrange("iss_epochs", 0, -1)

    def epochs_limited(self, limit, offset):
        # Make sure data is in Redis
        self.read_data()
        
        epochs = redis_client.lrange("iss_epochs", 0, -1)
        epochs = epochs[offset:]
        epochs = epochs[:limit]

        return epochs

    def get_state_vector_epoch(self, epoch):
        # Make sure data is in Redis
        self.read_data()
        
        # Get from Redis
        data = redis_client.hgetall(f"iss_data:{epoch}")
        
        if not data:
            return None
            
        return {
            'epoch': data['epoch'],
            'position': {
                'x': float(data['x']),
                'y': float(data['y']),
                'z': float(data['z'])
            },
            'velocity': {
                'x_dot': float(data['x_dot']),
                'y_dot': float(data['y_dot']),
                'z_dot': float(data['z_dot'])
            }
        }

    def get_speed_epoch(self, epoch):
        state_vector = self.get_state_vector_epoch(epoch)

        def calculate_speed(velocity):
            return math.sqrt(velocity['x_dot']**2 + velocity['y_dot']**2 + velocity['z_dot']**2)
        
        if state_vector:
            speed = calculate_speed(state_vector['velocity'])
            return {'epoch': epoch, 'speed': speed}
        else:
            print("Failed to find epoch.")
            return None
            
    def get_location_epoch(self, epoch):
        # New function for location
        state_vector = self.get_state_vector_epoch(epoch)
        
        if not state_vector:
            return None
            
        position = state_vector['position']
        
        # Earth radius in km
        earth_radius = 6371.0
        
        # Convert from Cartesian to spherical coordinates
        x = position['x']
        y = position['y']
        z = position['z']
        
        r = math.sqrt(x**2 + y**2 + z**2)
        longitude = math.degrees(math.atan2(y, x))
        latitude = math.degrees(math.asin(z / r))
        altitude = r - earth_radius
        
        # Get location name
        try:
            location = self.geolocator.reverse(f"{latitude}, {longitude}", zoom=4, language='en')
            geoposition = location.address if location else "Over water or uninhabited area"
        except Exception as e:
            logger.warning(f"Geocoding error: {e}")
            geoposition = "Location data unavalible"
        
        return {
            'epoch': epoch,
            'latitude': latitude,
            'longitude': longitude,
            'altitude': altitude,
            'geoposition': geoposition
        }

    def get_now(self):
        closest = self.print_closest_epoch()
        
        if not closest:
            return None
            
        epoch = closest['epoch']
        
        # Get speed and location
        speed = self.get_speed_epoch(epoch)['speed']
        location = self.get_location_epoch(epoch)
        
        return {
            'epoch': epoch,
            'speed': speed,
            'latitude': location['latitude'],
            'longitude': location['longitude'],
            'altitude': location['altitude'],
            'geoposition': location['geoposition']
        }

tracker = ISSTracker()
    
"""FLASK PORTION"""
@app.route('/epochs', methods = ['GET'])
def epoch_limit_data():
    try:
        limit = request.args.get('limit')
        offset = request.args.get('offset')

        if limit is not None and offset is not None:
            try:
                limit = int(limit)
                offset = int(offset)
                if limit < 0 or offset < 0:
                    return {"error": "Limit and offset must be non-negative integers"}, 400
            except ValueError:
                return {"error": "Limit and offset must be integers"}, 400

            return tracker.epochs_limited(limit, offset)
        return tracker.epochs()
    except Exception as e:
        logger.error(f"Error in /epochs route: {e}")
        return {"error": str(e)}, 500

@app.route('/epochs/<epoch>', methods = ['GET'])
def epoch_data(epoch):
    try:
        data = tracker.get_state_vector_epoch(epoch)
        if data is None:
            return {"error": f"Epoch not found: {epoch}"}, 404
        return data
    except Exception as e:
        logger.error(f"Error in /epochs/{epoch} route: {e}")
        return {"error": str(e)}, 500

@app.route('/epochs/<epoch>/speed', methods = ['GET'])
def epoch_speed(epoch):
    try:
        data = tracker.get_speed_epoch(epoch)
        if data is None:
            return {"error": f"Epoch not found: {epoch}"}, 404
        return data
    except Exception as e:
        logger.error(f"Error in /epochs/{epoch}/speed route: {e}")
        return {"error": str(e)}, 500

@app.route('/epochs/<epoch>/location', methods = ['GET'])
def epoch_location(epoch):
    try:
        data = tracker.get_location_epoch(epoch)
        if data is None:
            return {"error": f"Epoch not found: {epoch}"}, 404
        return data
    except Exception as e:
        logger.error(f"Error in /epochs/{epoch}/location route: {e}")
        return {"error": str(e)}, 500

@app.route('/now', methods = ['GET'])
def now_data():
    try:
        data = tracker.get_now()
        if data is None:
            return {"error": "Could not determine current ISS data"}, 500
        return data
    except Exception as e:
        logger.error(f"Error in /now route: {e}")
        return {"error": str(e)}, 500

if __name__ == "__main__":
    # Load data on startup
    tracker.read_data()
    app.run(debug=True, host='0.0.0.0')
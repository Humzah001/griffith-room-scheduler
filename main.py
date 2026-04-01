from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import google.oauth2.id_token
from google.auth.transport import requests
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import starlette.status as status
import datetime
from bson import ObjectId



uri = "mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?appName=<app>"

client = MongoClient(uri, server_api=ServerApi('1'))

try:
    client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
except Exception as e:
    print(e)


app = FastAPI()


db = client['A01-3181143']
user_collection = db['users']
room_collection = db['rooms']
day_collection = db['days']
booking_collection = db['bookings']

firebase_request_adapter = requests.Request()

app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory='templates')




def getUser(user_token):
    user = user_collection.find_one({'user_id': user_token['user_id']})
    if not user:
        user_dict = {
            'user_id': user_token['user_id'],
            'email': user_token.get('email', 'unknown'),
        }
        user_collection.insert_one(user_dict)
        user = user_collection.find_one({'user_id': user_token['user_id']})
    return user

def validateFirebaseToken(id_token):
    if not id_token:
        return None
    user_token = None
    try:
        user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    except ValueError as err:
        print(str(err))
    return user_token


def getOrCreateDay(room_id, date_str):
    day = day_collection.find_one({'room_id': room_id, 'date': date_str})
    if not day:
        day_dict = {
            'room_id': room_id,
            'date': date_str,
            'booking_list': []
        }
        day_collection.insert_one(day_dict)
        day = day_collection.find_one({'room_id': room_id, 'date': date_str})

        room = room_collection.find_one({'_id': room_id})
        room['day_list'].append(day['_id'])
        room_collection.find_one_and_update(
            {'_id': room_id},
            {'$set': {'day_list': room['day_list']}}
        )
    return day

def checkBookingClash(day_id, start_time, end_time, exclude_booking_id=None):
    day = day_collection.find_one({'_id': day_id})
    if not day:
        return False

    for booking_id in day['booking_list']:
        if exclude_booking_id and booking_id == exclude_booking_id:
            continue

        booking = booking_collection.find_one({'_id': booking_id})
        if not booking:
            continue

        if start_time < booking['end_time'] and end_time > booking['start_time']:
            return True

    return False

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




@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)

    if not user_token:
        return templates.TemplateResponse("main.html", {
            "request": request,
            "user_token": None,
            "error_message": None,
            "user_info": None,
            "rooms": None,
            "user_bookings": None,
        })

    user = getUser(user_token)
    rooms = list(room_collection.find({}))

    raw_bookings = booking_collection.find({"user_id": user_token["user_id"]})
    user_bookings = []
    for booking in raw_bookings:
        day = day_collection.find_one({"_id": booking["day_id"]})
        room = room_collection.find_one({"_id": booking["room_id"]})
        if day and room:
            user_bookings.append({
                "booking": booking,
                "room_name": room["room_name"],
                "date": day["date"],
            })
    user_bookings.sort(key=lambda x: (x["date"], x["booking"]["start_time"]))

    return templates.TemplateResponse("main.html", {
        "request": request,
        "user_token": user_token,
        "error_message": None,
        "user_info": user,
        "rooms": rooms,
        "user_bookings": user_bookings,
    })



@app.post("/add-room", response_class=RedirectResponse)
async def addRoom(request: Request):

    user_token = validateFirebaseToken(request.cookies.get("token"))
    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    room_name = form["room_name"].strip()

    if not room_name or room_collection.find_one({"room_name": room_name}):
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    room_collection.insert_one({
        "room_name": room_name,
        "day_list": [],
        "created_by": user_token["user_id"],
    })

    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)



@app.post("/add-booking", response_class=RedirectResponse)
async def addBooking(request: Request):
    user_token = validateFirebaseToken(request.cookies.get("token"))
    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    room_id = ObjectId(form["room_id"])
    date_str = form["date"]
    start_time = form["start_time"]
    end_time = form["end_time"]

    if not date_str or start_time >= end_time:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    day = getOrCreateDay(room_id, date_str)

    if checkBookingClash(day["_id"], start_time, end_time):
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    user = getUser(user_token)
    new_booking = booking_collection.insert_one({
        "room_id": room_id,
        "day_id": day["_id"],
        "user_id": user_token["user_id"],
        "user_email": user["email"],
        "start_time": start_time,
        "end_time": end_time,
        "date": date_str,
    })

    day["booking_list"].append(new_booking.inserted_id)
    day_collection.find_one_and_update(
        {"_id": day["_id"]},
        {"$set": {"booking_list": day["booking_list"]}}
    )

    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)



@app.post("/delete-booking", response_class=RedirectResponse)
async def deleteBooking(request: Request):
    user_token = validateFirebaseToken(request.cookies.get("token"))
    if not user_token:
        return RedirectResponse("/")

    booking_id = ObjectId((await request.form())["booking_id"])
    booking = booking_collection.find_one({"_id": booking_id})

    if not booking or booking["user_id"] != user_token["user_id"]:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    day = day_collection.find_one({"_id": booking["day_id"]})
    if day and booking_id in day["booking_list"]:
        day["booking_list"].remove(booking_id)
        day_collection.find_one_and_update(
            {"_id": day["_id"]},
            {"$set": {"booking_list": day["booking_list"]}}
        )

    booking_collection.delete_one({"_id": booking_id})

    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)



@app.post("/delete-room", response_class=RedirectResponse)
async def deleteRoom(request: Request):
    user_token = validateFirebaseToken(request.cookies.get("token"))
    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    room_id = ObjectId(form["room_id"])

    room = room_collection.find_one({"_id": room_id})
    if not room:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    if room["created_by"] != user_token["user_id"]:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    for day_id in room["day_list"]:
        day = day_collection.find_one({"_id": day_id})
        if day and len(day["booking_list"]) > 0:
            return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    for day_id in room["day_list"]:
        day_collection.delete_one({"_id": day_id})

    room_collection.delete_one({"_id": room_id})

    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)



@app.get("/edit-booking", response_class=HTMLResponse)
async def editBookingForm(request: Request):
    user_token = validateFirebaseToken(request.cookies.get("token"))
    if not user_token:
        return RedirectResponse("/")

    booking_id = request.query_params.get("booking_id")
    if not booking_id:
        return RedirectResponse("/")

    booking = booking_collection.find_one({"_id": ObjectId(booking_id)})
    if not booking or booking["user_id"] != user_token["user_id"]:
        return RedirectResponse("/")

    room = room_collection.find_one({"_id": booking["room_id"]})
    day = day_collection.find_one({"_id": booking["day_id"]})

    return templates.TemplateResponse("edit_booking.html", {
        "request": request,
        "user_token": user_token,
        "booking": booking,
        "room_name": room["room_name"] if room else "Unknown",
        "date": day["date"] if day else "Unknown",
        "error_message": None
    })




@app.post("/edit-booking", response_class=RedirectResponse)
async def editBookingPost(request: Request):
    user_token = validateFirebaseToken(request.cookies.get("token"))
    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    booking_id = ObjectId(form["booking_id"])
    new_start = form["start_time"]
    new_end = form["end_time"]
    new_date = form["date"]

    booking = booking_collection.find_one({"_id": booking_id})
    if not booking or booking["user_id"] != user_token["user_id"]:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    if new_start >= new_end:
        return RedirectResponse("/edit-booking?booking_id=" + str(booking_id),
                                status_code=status.HTTP_302_FOUND)

    old_day = day_collection.find_one({"_id": booking["day_id"]})
    old_date = old_day["date"] if old_day else ""

    if new_date == old_date:
        if checkBookingClash(booking["day_id"], new_start, new_end, booking_id):
            return RedirectResponse("/edit-booking?booking_id=" + str(booking_id),
                                    status_code=status.HTTP_302_FOUND)

        booking_collection.find_one_and_update(
            {"_id": booking_id},
            {"$set": {"start_time": new_start, "end_time": new_end}}
        )

    else:
        new_day = getOrCreateDay(booking["room_id"], new_date)

        if checkBookingClash(new_day["_id"], new_start, new_end):
            return RedirectResponse("/edit-booking?booking_id=" + str(booking_id),
                                    status_code=status.HTTP_302_FOUND)

        if old_day and booking_id in old_day["booking_list"]:
            old_day["booking_list"].remove(booking_id)
            day_collection.find_one_and_update(
                {"_id": old_day["_id"]},
                {"$set": {"booking_list": old_day["booking_list"]}}
            )

        new_day["booking_list"].append(booking_id)
        day_collection.find_one_and_update(
            {"_id": new_day["_id"]},
            {"$set": {"booking_list": new_day["booking_list"]}}
        )

        booking_collection.find_one_and_update(
            {"_id": booking_id},
            {"$set": {
                "day_id": new_day["_id"],
                "date": new_date,
                "start_time": new_start,
                "end_time": new_end
            }}
        )

    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)


    


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

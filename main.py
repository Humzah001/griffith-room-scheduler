from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import google.oauth2.id_token
from google.auth.transport import requests
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import re
import starlette.status as status
import datetime
from urllib.parse import parse_qsl, unquote, urlencode
from bson import ObjectId


APP_TITLE = "Griffith Room Scheduler"
APP_HOME = "/system"

uri = "mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?appName=<app>"

client = MongoClient(uri, server_api=ServerApi('1'))

try:
    client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
except Exception as e:
    print(e)


app = FastAPI(
    title=APP_TITLE,
    description="Griffith Room Scheduler — room booking for Griffith College Dublin.",
)


@app.middleware("http")
async def disable_html_caching(request: Request, call_next):
    """HTML for this app depends on the session cookie; avoid serving a stale guest page after login."""
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if content_type.partition(";")[0].strip().lower() == "text/html":
        response.headers["Cache-Control"] = "private, no-store, max-age=0, must-revalidate"
    return response


db = client['A01-3181143']
user_collection = db['users']
room_collection = db['rooms']
day_collection = db['days']
booking_collection = db['bookings']

firebase_request_adapter = requests.Request()

FLASH_ERRORS = {
    "room_dup": "A room with that name already exists (matching is not case-sensitive).",
    "room_empty": "Please enter a room name.",
    "booking_clash": "That time overlaps another booking for this room.",
    "booking_time": "End time must be after start time.",
    "booking_fields": "Please choose a room, date, and valid start and end times.",
    "booking_room": "Please select a room from the list.",
    "booking_room_invalid": "Invalid room selection.",
    "booking_room_missing": "That room no longer exists.",
    "delete_booking_denied": "You can only delete your own bookings.",
    "delete_booking_invalid": "Invalid booking.",
    "delete_room_denied": "Only the person who created the room can delete it.",
    "delete_room_bookings": "You cannot delete a room that still has bookings.",
    "delete_room_missing": "That room was not found.",
    "edit_denied": "You cannot view or edit this booking.",
    "edit_time": "End time must be after start time.",
    "edit_clash": "That time overlaps another booking for this room.",
    "edit_fields": "Please enter valid date and times.",
    "room_not_found": "That room was not found.",
    "room_bad_link": "Invalid room link. Open the room from the list again.",
    "filter_date": "Please choose a date to filter by.",
    "filter_room": "Please choose a room to filter by.",
}

FLASH_OK = {
    "room_added": "Room added successfully.",
    "booking_added": "Booking added successfully.",
    "booking_deleted": "Booking removed.",
    "room_deleted": "Room deleted successfully.",
    "booking_updated": "Booking updated successfully.",
}


def with_query(url: str, **params) -> str:
    """Append or merge query string parameters onto url."""
    if "?" in url:
        base, q = url.split("?", 1)
        pairs = dict(parse_qsl(q))
    else:
        base, pairs = url, {}
    for k, v in params.items():
        if v is None:
            pairs.pop(k, None)
        else:
            pairs[k] = str(v)
    qs = urlencode(pairs)
    return f"{base}?{qs}" if qs else base


def flash_from_request(request: Request):
    err = request.query_params.get("err", "").strip()
    ok = request.query_params.get("ok", "").strip()
    if err:
        return FLASH_ERRORS.get(err, "Something went wrong. Please try again."), "error"
    if ok:
        msg = FLASH_OK.get(ok)
        if msg:
            return msg, "success"
    return None, None


app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory='templates')

# Working day window for occupancy / calendar (assignment: 09:00–18:00)
WORK_START_MIN = 9 * 60
WORK_END_MIN = 18 * 60
WORK_WINDOW_MIN = WORK_END_MIN - WORK_START_MIN
CALENDAR_SLOT_COUNT = 9  # hourly slots 09:00–10:00 … 17:00–18:00


def next_five_dates():
    today = datetime.date.today()
    return [(today + datetime.timedelta(days=i)).isoformat() for i in range(5)]


def normalize_time(t: str) -> str:
    parts = t.strip().split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    return f"{h:02d}:{m:02d}"


def time_to_minutes(t: str) -> int:
    t = normalize_time(t)
    h, m = map(int, t.split(":"))
    return h * 60 + m


def minutes_to_time_str(mins: int) -> str:
    return f"{mins // 60:02d}:{mins % 60:02d}"


def overlap_work_minutes(start_time: str, end_time: str) -> int:
    s = time_to_minutes(start_time)
    e = time_to_minutes(end_time)
    lo = max(s, WORK_START_MIN)
    hi = min(e, WORK_END_MIN)
    return max(0, hi - lo)


def calendar_hour_labels():
    return [f"{h:02d}:00" for h in range(9, 18)]


def bookings_for_day_doc(day_doc):
    if not day_doc:
        return []
    out = []
    for bid in day_doc.get("booking_list", []):
        b = booking_collection.find_one({"_id": bid})
        if b:
            out.append(b)
    out.sort(key=lambda x: normalize_time(x["start_time"]))
    return out


def day_occupancy_percent(room_id, date_str):
    day_doc = day_collection.find_one({"room_id": room_id, "date": date_str})
    books = bookings_for_day_doc(day_doc)
    used = 0
    for b in books:
        try:
            used += overlap_work_minutes(b["start_time"], b["end_time"])
        except (ValueError, IndexError, KeyError, TypeError):
            continue
    if WORK_WINDOW_MIN <= 0:
        return 0
    pct = int(round(100 * used / WORK_WINDOW_MIN))
    return min(100, pct)


def slot_busy(bookings, slot_index):
    slot_start = WORK_START_MIN + slot_index * 60
    slot_end = slot_start + 60
    for b in bookings:
        try:
            bs = time_to_minutes(b["start_time"])
            be = time_to_minutes(b["end_time"])
        except (ValueError, IndexError, KeyError, TypeError):
            continue
        if bs < slot_end and be > slot_start:
            return True, b.get("user_email", "Booked")
    return False, ""


def build_calendar_data(room_id, dates_5):
    calendar_data = []
    for d in dates_5:
        day_doc = day_collection.find_one({"room_id": room_id, "date": d})
        books = bookings_for_day_doc(day_doc)
        hours_cells = []
        for i in range(CALENDAR_SLOT_COUNT):
            booked, info = slot_busy(books, i)
            hours_cells.append({"is_booked": booked, "booking_info": info})
        calendar_data.append({"date": d, "hours": hours_cells})
    return calendar_data


def earliest_free_slot(room_id, dates_5):
    for d in dates_5:
        day_doc = day_collection.find_one({"room_id": room_id, "date": d})
        books = bookings_for_day_doc(day_doc)
        for i in range(CALENDAR_SLOT_COUNT):
            slot_start = WORK_START_MIN + i * 60
            slot_end = slot_start + 60
            busy, _ = slot_busy(books, i)
            if not busy:
                return {"date": d, "time": minutes_to_time_str(slot_start)}
    return None


def all_bookings_for_room(room):
    """All bookings for this room from the bookings collection (not only day_list)."""
    items = []
    for b in booking_collection.find({"room_id": room["_id"]}):
        day_doc = day_collection.find_one({"_id": b["day_id"]})
        d = day_doc["date"] if day_doc else b.get("date", "")
        items.append({"booking": b, "date": d})
    items.sort(key=lambda x: (x["date"], normalize_time(x["booking"]["start_time"])))
    return items


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if user_token:
        q = request.url.query
        dest = APP_HOME + ("?" + str(q) if q else "")
        return RedirectResponse(dest, status_code=status.HTTP_302_FOUND)
    flash_message, flash_kind = flash_from_request(request)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "app_title": APP_TITLE,
        "flash_message": flash_message,
        "flash_kind": flash_kind,
    })


@app.get("/system", response_class=HTMLResponse)
async def system_dashboard(request: Request):
    id_token = request.cookies.get("token")
    user_token = validateFirebaseToken(id_token)
    if not user_token:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    flash_message, flash_kind = flash_from_request(request)

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

    q_day = request.query_params.get("filter_day", "").strip()
    filtered_bookings = None
    if q_day:
        filtered_bookings = []
        for room in rooms:
            day_doc = day_collection.find_one({"room_id": room["_id"], "date": q_day})
            if not day_doc:
                continue
            for bid in day_doc.get("booking_list", []):
                b = booking_collection.find_one({"_id": bid})
                if b:
                    filtered_bookings.append({
                        "booking": b,
                        "room_name": room["room_name"],
                    })
        filtered_bookings.sort(
            key=lambda x: (x["booking"]["start_time"], x["room_name"])
        )

    q_room = request.query_params.get("filter_room", "").strip()
    room_user_bookings = None
    filter_room_id = None
    if q_room:
        filter_room_id = q_room
        try:
            rid = ObjectId(q_room)
            room_user_bookings = [
                x for x in user_bookings if x["booking"]["room_id"] == rid
            ]
        except Exception:
            room_user_bookings = []
            filter_room_id = None

    return templates.TemplateResponse("system.html", {
        "request": request,
        "app_title": APP_TITLE,
        "user_token": user_token,
        "flash_message": flash_message,
        "flash_kind": flash_kind,
        "user_info": user,
        "rooms": rooms,
        "user_bookings": user_bookings,
        "filter_date": q_day or None,
        "filtered_bookings": filtered_bookings,
        "filter_room_id": filter_room_id,
        "room_user_bookings": room_user_bookings if filter_room_id else None,
    })



def room_name_taken_case_insensitive(name: str) -> bool:
    """True if any room exists with the same name ignoring ASCII case."""
    n = name.strip()
    if not n:
        return False
    pattern = re.compile("^" + re.escape(n) + "$", re.IGNORECASE)
    return room_collection.find_one({"room_name": pattern}) is not None


@app.post("/add-room", response_class=RedirectResponse)
async def addRoom(request: Request):

    user_token = validateFirebaseToken(request.cookies.get("token"))
    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    room_name = form["room_name"].strip()

    if not room_name:
        return RedirectResponse(
            with_query(APP_HOME, err="room_empty"),
            status_code=status.HTTP_302_FOUND,
        )
    if room_name_taken_case_insensitive(room_name):
        return RedirectResponse(
            with_query(APP_HOME, err="room_dup"),
            status_code=status.HTTP_302_FOUND,
        )

    room_collection.insert_one({
        "room_name": room_name,
        "day_list": [],
        "created_by": user_token["user_id"],
    })

    return RedirectResponse(
        with_query(APP_HOME, ok="room_added"),
        status_code=status.HTTP_302_FOUND,
    )



@app.post("/add-booking", response_class=RedirectResponse)
async def addBooking(request: Request):
    user_token = validateFirebaseToken(request.cookies.get("token"))
    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    rid = (form.get("room_id") or "").strip()
    if not rid:
        return RedirectResponse(
            with_query(APP_HOME, err="booking_room"),
            status_code=status.HTTP_302_FOUND,
        )
    try:
        room_id = ObjectId(rid)
    except Exception:
        return RedirectResponse(
            with_query(APP_HOME, err="booking_room_invalid"),
            status_code=status.HTTP_302_FOUND,
        )

    date_str = (form.get("date") or "").strip()
    try:
        start_time = normalize_time(form.get("start_time") or "")
        end_time = normalize_time(form.get("end_time") or "")
    except (ValueError, IndexError):
        return RedirectResponse(
            with_query(APP_HOME, err="booking_fields"),
            status_code=status.HTTP_302_FOUND,
        )

    if not date_str:
        return RedirectResponse(
            with_query(APP_HOME, err="booking_fields"),
            status_code=status.HTTP_302_FOUND,
        )
    if time_to_minutes(start_time) >= time_to_minutes(end_time):
        return RedirectResponse(
            with_query(APP_HOME, err="booking_time"),
            status_code=status.HTTP_302_FOUND,
        )

    if not room_collection.find_one({"_id": room_id}):
        return RedirectResponse(
            with_query(APP_HOME, err="booking_room_missing"),
            status_code=status.HTTP_302_FOUND,
        )

    day = getOrCreateDay(room_id, date_str)

    if checkBookingClash(day["_id"], start_time, end_time):
        return RedirectResponse(
            with_query(APP_HOME, err="booking_clash"),
            status_code=status.HTTP_302_FOUND,
        )

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

    bl = list(day.get("booking_list") or [])
    bl.append(new_booking.inserted_id)
    day_collection.find_one_and_update(
        {"_id": day["_id"]},
        {"$set": {"booking_list": bl}}
    )

    return RedirectResponse(
        with_query(APP_HOME, ok="booking_added"),
        status_code=status.HTTP_302_FOUND,
    )



@app.post("/delete-booking", response_class=RedirectResponse)
async def deleteBooking(request: Request):
    user_token = validateFirebaseToken(request.cookies.get("token"))
    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    try:
        booking_id = ObjectId(form.get("booking_id"))
    except Exception:
        return RedirectResponse(
            with_query(APP_HOME, err="delete_booking_invalid"),
            status_code=status.HTTP_302_FOUND,
        )
    booking = booking_collection.find_one({"_id": booking_id})

    if not booking or booking["user_id"] != user_token["user_id"]:
        return RedirectResponse(
            with_query(APP_HOME, err="delete_booking_denied"),
            status_code=status.HTTP_302_FOUND,
        )

    day = day_collection.find_one({"_id": booking["day_id"]})
    if day:
        bl = list(day.get("booking_list") or [])
        if booking_id in bl:
            bl.remove(booking_id)
            day_collection.find_one_and_update(
                {"_id": day["_id"]},
                {"$set": {"booking_list": bl}}
            )

    booking_collection.delete_one({"_id": booking_id})

    referer = request.headers.get("referer") or ""
    if "/view-room" in referer and "room_id=" in referer:
        return RedirectResponse(
            with_query(referer, ok="booking_deleted"),
            status_code=status.HTTP_302_FOUND,
        )

    return RedirectResponse(
        with_query(APP_HOME, ok="booking_deleted"),
        status_code=status.HTTP_302_FOUND,
    )



@app.post("/delete-room", response_class=RedirectResponse)
async def deleteRoom(request: Request):
    user_token = validateFirebaseToken(request.cookies.get("token"))
    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    try:
        room_id = ObjectId(form.get("room_id"))
    except Exception:
        return RedirectResponse(
            with_query(APP_HOME, err="delete_room_missing"),
            status_code=status.HTTP_302_FOUND,
        )

    room = room_collection.find_one({"_id": room_id})
    if not room:
        return RedirectResponse(
            with_query(APP_HOME, err="delete_room_missing"),
            status_code=status.HTTP_302_FOUND,
        )

    if room["created_by"] != user_token["user_id"]:
        return RedirectResponse(
            with_query(APP_HOME, err="delete_room_denied"),
            status_code=status.HTTP_302_FOUND,
        )

    for day_id in room.get("day_list") or []:
        day = day_collection.find_one({"_id": day_id})
        if day and len(day.get("booking_list") or []) > 0:
            return RedirectResponse(
                with_query(APP_HOME, err="delete_room_bookings"),
                status_code=status.HTTP_302_FOUND,
            )

    for day_id in room.get("day_list") or []:
        day_collection.delete_one({"_id": day_id})

    room_collection.delete_one({"_id": room_id})

    return RedirectResponse(
        with_query(APP_HOME, ok="room_deleted"),
        status_code=status.HTTP_302_FOUND,
    )


@app.post("/filter-by-day", response_class=RedirectResponse)
async def filter_by_day(request: Request):
    user_token = validateFirebaseToken(request.cookies.get("token"))
    if not user_token:
        return RedirectResponse("/")
    form = await request.form()
    d = form.get("filter_date", "").strip()
    if not d:
        return RedirectResponse(
            with_query(APP_HOME, err="filter_date"),
            status_code=status.HTTP_302_FOUND,
        )
    return RedirectResponse(
        with_query(APP_HOME, filter_day=d),
        status_code=status.HTTP_302_FOUND,
    )


@app.post("/filter-by-room", response_class=RedirectResponse)
async def filter_by_room(request: Request):
    user_token = validateFirebaseToken(request.cookies.get("token"))
    if not user_token:
        return RedirectResponse("/")
    form = await request.form()
    rid = form.get("room_id", "").strip()
    if not rid:
        return RedirectResponse(
            with_query(APP_HOME, err="filter_room"),
            status_code=status.HTTP_302_FOUND,
        )
    return RedirectResponse(
        with_query(APP_HOME, filter_room=rid),
        status_code=status.HTTP_302_FOUND,
    )


@app.get("/view-room", response_class=HTMLResponse)
async def view_room(request: Request):
    user_token = validateFirebaseToken(request.cookies.get("token"))
    if not user_token:
        flash_message, flash_kind = flash_from_request(request)
        return templates.TemplateResponse("view_room.html", {
            "request": request,
            "app_title": APP_TITLE,
            "user_token": None,
            "user_info": None,
            "flash_message": flash_message,
            "flash_kind": flash_kind,
            "room": None,
            "occupancy_data": [],
            "earliest_free": None,
            "calendar_data": [],
            "hours": [],
            "all_bookings": [],
        })

    room_id_param = request.query_params.get("room_id", "").strip()
    if not room_id_param:
        return RedirectResponse(
            with_query(APP_HOME, err="room_bad_link"),
            status_code=status.HTTP_302_FOUND,
        )
    try:
        room_oid = ObjectId(room_id_param)
    except Exception:
        return RedirectResponse(
            with_query(APP_HOME, err="room_bad_link"),
            status_code=status.HTTP_302_FOUND,
        )

    room = room_collection.find_one({"_id": room_oid})
    if not room:
        return RedirectResponse(
            with_query(APP_HOME, err="room_not_found"),
            status_code=status.HTTP_302_FOUND,
        )

    user = getUser(user_token)
    flash_message, flash_kind = flash_from_request(request)
    dates_5 = next_five_dates()
    occupancy_data = [
        {"date": d, "occupancy": day_occupancy_percent(room_oid, d)}
        for d in dates_5
    ]
    earliest_free = earliest_free_slot(room_oid, dates_5)
    hours = calendar_hour_labels()
    calendar_data = build_calendar_data(room_oid, dates_5)
    all_bookings = all_bookings_for_room(room)

    return templates.TemplateResponse("view_room.html", {
        "request": request,
        "app_title": APP_TITLE,
        "user_token": user_token,
        "user_info": user,
        "flash_message": flash_message,
        "flash_kind": flash_kind,
        "room": room,
        "occupancy_data": occupancy_data,
        "earliest_free": earliest_free,
        "calendar_data": calendar_data,
        "hours": hours,
        "all_bookings": all_bookings,
    })


@app.get("/edit-booking", response_class=HTMLResponse)
async def editBookingForm(request: Request):
    user_token = validateFirebaseToken(request.cookies.get("token"))
    if not user_token:
        return RedirectResponse("/")

    booking_id = request.query_params.get("booking_id")
    if not booking_id:
        return RedirectResponse(
            with_query(APP_HOME, err="edit_denied"),
            status_code=status.HTTP_302_FOUND,
        )

    try:
        bid_obj = ObjectId(booking_id)
    except Exception:
        return RedirectResponse(
            with_query(APP_HOME, err="edit_denied"),
            status_code=status.HTTP_302_FOUND,
        )

    booking = booking_collection.find_one({"_id": bid_obj})
    if not booking or booking["user_id"] != user_token["user_id"]:
        return RedirectResponse(
            with_query(APP_HOME, err="edit_denied"),
            status_code=status.HTTP_302_FOUND,
        )

    room = room_collection.find_one({"_id": booking["room_id"]})
    day = day_collection.find_one({"_id": booking["day_id"]})
    flash_message, flash_kind = flash_from_request(request)

    return templates.TemplateResponse("edit_booking.html", {
        "request": request,
        "app_title": APP_TITLE,
        "user_token": user_token,
        "booking": booking,
        "room_name": room["room_name"] if room else "Unknown",
        "date": day["date"] if day else "Unknown",
        "flash_message": flash_message,
        "flash_kind": flash_kind,
    })




@app.post("/edit-booking", response_class=RedirectResponse)
async def editBookingPost(request: Request):
    user_token = validateFirebaseToken(request.cookies.get("token"))
    if not user_token:
        return RedirectResponse("/")

    form = await request.form()
    try:
        booking_id = ObjectId(form.get("booking_id"))
    except Exception:
        return RedirectResponse(
            with_query(APP_HOME, err="edit_denied"),
            status_code=status.HTTP_302_FOUND,
        )
    try:
        new_start = normalize_time(form.get("start_time") or "")
        new_end = normalize_time(form.get("end_time") or "")
    except (ValueError, IndexError):
        return RedirectResponse(
            with_query(
                f"/edit-booking?booking_id={booking_id}",
                err="edit_fields",
            ),
            status_code=status.HTTP_302_FOUND,
        )
    new_date = (form.get("date") or "").strip()

    booking = booking_collection.find_one({"_id": booking_id})
    if not booking or booking["user_id"] != user_token["user_id"]:
        return RedirectResponse(
            with_query(APP_HOME, err="edit_denied"),
            status_code=status.HTTP_302_FOUND,
        )

    if time_to_minutes(new_start) >= time_to_minutes(new_end):
        return RedirectResponse(
            with_query(f"/edit-booking?booking_id={booking_id}", err="edit_time"),
            status_code=status.HTTP_302_FOUND,
        )

    old_day = day_collection.find_one({"_id": booking["day_id"]})
    old_date = old_day["date"] if old_day else ""

    if new_date == old_date:
        if checkBookingClash(booking["day_id"], new_start, new_end, booking_id):
            return RedirectResponse(
                with_query(f"/edit-booking?booking_id={booking_id}", err="edit_clash"),
                status_code=status.HTTP_302_FOUND,
            )

        booking_collection.find_one_and_update(
            {"_id": booking_id},
            {"$set": {"start_time": new_start, "end_time": new_end}}
        )

    else:
        new_day = getOrCreateDay(booking["room_id"], new_date)

        if checkBookingClash(new_day["_id"], new_start, new_end):
            return RedirectResponse(
                with_query(f"/edit-booking?booking_id={booking_id}", err="edit_clash"),
                status_code=status.HTTP_302_FOUND,
            )

        if old_day:
            obl = list(old_day.get("booking_list") or [])
            if booking_id in obl:
                obl.remove(booking_id)
                day_collection.find_one_and_update(
                    {"_id": old_day["_id"]},
                    {"$set": {"booking_list": obl}}
                )

        nbl = list(new_day.get("booking_list") or [])
        if booking_id not in nbl:
            nbl.append(booking_id)
        day_collection.find_one_and_update(
            {"_id": new_day["_id"]},
            {"$set": {"booking_list": nbl}}
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

    return RedirectResponse(
        with_query(APP_HOME, ok="booking_updated"),
        status_code=status.HTTP_302_FOUND,
    )


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
    if "%" in id_token:
        id_token = unquote(id_token)
    user_token = None
    try:
        user_token = google.oauth2.id_token.verify_firebase_token(
            id_token,
            firebase_request_adapter,
            clock_skew_in_seconds=60,
        )
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
        if room:
            dlist = list(room.get('day_list') or [])
            if day['_id'] not in dlist:
                dlist.append(day['_id'])
                room_collection.find_one_and_update(
                    {'_id': room_id},
                    {'$set': {'day_list': dlist}}
                )
    return day

def checkBookingClash(day_id, start_time, end_time, exclude_booking_id=None):
    day = day_collection.find_one({'_id': day_id})
    if not day:
        return False

    try:
        s = time_to_minutes(start_time)
        e = time_to_minutes(end_time)
    except (ValueError, IndexError):
        return True

    for booking_id in day.get('booking_list', []):
        if exclude_booking_id and booking_id == exclude_booking_id:
            continue

        booking = booking_collection.find_one({'_id': booking_id})
        if not booking:
            continue

        try:
            bs = time_to_minutes(booking['start_time'])
            be = time_to_minutes(booking['end_time'])
        except (ValueError, IndexError, KeyError):
            continue

        if s < be and e > bs:
            return True

    return False

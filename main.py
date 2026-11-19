from mcp.server.fastmcp import FastMCP
import os, time, requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

mcp = FastMCP("vetter-bridge", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))

DOMAIN = (os.environ.get("VETTER_DOMAIN") or "").strip().strip('"').strip("'").rstrip("/")
API_KEY = (os.environ.get("VETTER_API_KEY") or "").strip().strip('"').strip("'")
CLIENT_ID = (os.environ.get("VETTER_CLIENT_ID") or "").strip().strip('"').strip("'")
CLIENT_SECRET = (os.environ.get("VETTER_CLIENT_SECRET") or "").strip().strip('"').strip("'")
SCOPE = (os.environ.get("VETTER_SCOPE") or "APIService").strip()
TZ = ZoneInfo("America/New_York")

_token = None
_token_exp = 0


def get_token():
    global _token, _token_exp
    if _token and time.time() < _token_exp - 60:
        return _token
    last = "no response"
    for key in (CLIENT_SECRET, API_KEY):
        if not key:
            continue
        r = requests.post(
            DOMAIN + "/oauth/access_token",
            headers={"x-api-key": key, "Cache-Control": "no-cache"},
            data={
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "scope": SCOPE,
            },
            timeout=30,
        )
        if r.status_code < 400:
            data = r.json()
            _token = data.get("access_token")
            _token_exp = time.time() + int(data.get("expires_in", 3600))
            if not _token:
                raise RuntimeError("Vetter login returned no token: " + r.text[:300])
            return _token
        last = str(r.status_code) + " " + r.text[:300]
    raise RuntimeError("Vetter login failed: " + last)


def headers():
    return {
        "Authorization": "Bearer " + get_token(),
        "x-api-key": CLIENT_SECRET or API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def api(method, path, **kwargs):
    url = DOMAIN + "/api/1.0.0/" + API_KEY + path
    return requests.request(method, url, headers=headers(), timeout=30, **kwargs)


def fail(r):
    return "Request failed (" + str(r.status_code) + "): " + r.text[:400]


def unwrap(data):
    if not isinstance(data, dict):
        return data
    resp = data.get("response", data)
    if isinstance(resp, dict):
        if "resource" in resp:
            return resp["resource"]
        if "resources" in resp:
            return resp["resources"]
    return data


def as_list(data):
    obj = unwrap(data)
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ("data", "items", "resources"):
            if isinstance(obj.get(key), list):
                return obj[key]
        if obj.get("id"):
            return [obj]
    return []


def one(data):
    obj = unwrap(data)
    if isinstance(obj, list):
        return obj[0] if obj else {}
    return obj if isinstance(obj, dict) else {}


def label_of(item):
    return str(item.get("label") or item.get("name") or item.get("firstName") or "")


def pick(items, name):
    want = (name or "").strip().lower()
    if not want:
        return None
    for item in items:
        label = label_of(item).lower()
        if label == want or want in label:
            return item
    return None


def pages(path, params=None):
    found = []
    base = dict(params or {})
    for page in range(1, 6):
        q = dict(base)
        q["page"] = str(page)
        q["perPage"] = "200"
        r = api("GET", path, params=q)
        if r.status_code >= 400:
            break
        batch = as_list(r.json())
        if not batch:
            break
        found.extend(batch)
        if len(batch) < 200:
            break
    return found


def parse_iso(value):
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def clock(dt):
    return dt.strftime("%Y-%m-%d %I:%M %p ET")


@mcp.tool()
def find_client(phone: str) -> str:
    """Find a client by phone number. Returns client id, name, and pets."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    r = api("GET", "/clients", params={"phone": digits or phone, "page": "1", "perPage": "20"})
    if r.status_code >= 400:
        return fail(r)
    items = as_list(r.json())
    if not items:
        return "No client found with that phone number."
    lines = []
    for c in items:
        pets = ", ".join(p.get("name", "?") + " id " + str(p.get("id", "?")) for p in c.get("patients", []) or [])
        lines.append(
            "Client: " + str(c.get("firstName", "")) + " " + str(c.get("lastName", ""))
            + " | id: " + str(c.get("id"))
            + " | pets: " + (pets or "none listed")
        )
    return "\n".join(lines)


@mcp.tool()
def create_client(first_name: str, last_name: str, mobile: str, email: str = "", address: str = "", city: str = "", state: str = "FL", zipcode: str = "") -> str:
    """Create a new client. mobile is the phone number. Returns the new client id."""
    body = {
        "firstName": first_name,
        "lastName": last_name,
        "mobile": "".join(ch for ch in mobile if ch.isdigit()) or mobile,
        "email": email,
        "address": address,
        "city": city,
        "state": state,
        "zipcode": zipcode,
        "country": "US",
    }
    r = api("POST", "/clients", json=body)
    if r.status_code >= 400:
        return fail(r)
    made = one(r.json())
    return "Created client " + first_name + " " + last_name + " | id: " + str(made.get("id"))


@mcp.tool()
def create_patient(client_id: str, name: str, species: str, sex: str = "Unknown", breed: str = "") -> str:
    """Add a pet to an existing client. species like Dog or Cat. Returns the new patient id."""
    species_row = pick(pages("/species"), species)
    if not species_row:
        names = ", ".join(label_of(s) for s in pages("/species")[:12])
        return "Could not match species. Ask again. Options include: " + names
    sex_row = pick(pages("/sexes"), sex) or pick(pages("/sexes"), "Unknown")
    body = {
        "name": name,
        "species": {"id": species_row.get("id")},
        "status": "Active",
    }
    if sex_row:
        body["sex"] = {"id": sex_row.get("id")}
    if breed:
        breed_row = pick(pages("/breeds"), breed)
        if breed_row:
            body["breeds"] = [{"id": breed_row.get("id")}]
    r = api("POST", "/patients", json=body)
    if r.status_code >= 400:
        return fail(r)
    made = one(r.json())
    patient_id = str(made.get("id"))
    link = api("POST", "/patient/" + patient_id + "/clients", json={"id": client_id, "isPrimary": 1})
    if link.status_code >= 400:
        return "Created pet " + name + " | id: " + patient_id + " but linking to the client failed: " + fail(link)
    return "Created pet " + name + " | id: " + patient_id + " | owner: " + client_id


@mcp.tool()
def list_providers() -> str:
    """List appointment providers and their ids."""
    rows = pages("/appointment-providers")
    if not rows:
        return "No providers found."
    lines = []
    for p in rows:
        lines.append(str(p.get("firstName", "")) + " " + str(p.get("lastName", label_of(p))) + " | id: " + str(p.get("id")))
    return "\n".join(lines)


@mcp.tool()
def list_appointment_types() -> str:
    """List appointment types and their ids."""
    rows = pages("/appointment-types")
    if not rows:
        return "No appointment types found."
    return "\n".join(label_of(t) + " | id: " + str(t.get("id")) for t in rows)


@mcp.tool()
def list_appointments(client_id: str) -> str:
    """List upcoming appointments for a client id. Includes appointment id, time, pet, and status."""
    start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=45)
    r = api("GET", "/census", params={
        "start": start.isoformat(),
        "end": end.isoformat(),
        "page": "1",
        "perPage": "200",
    })
    if r.status_code >= 400:
        return fail(r)
    lines = []
    for a in as_list(r.json()):
        owner = a.get("client") or {}
        if str(owner.get("id")) != str(client_id):
            continue
        if str(a.get("status", "")).lower() == "cancelled":
            continue
        begin = parse_iso(a.get("start"))
        finish = parse_iso(a.get("end"))
        pet = (a.get("patient") or {}).get("name", "?")
        who = (a.get("provider") or {}).get("firstName", "")
        when = clock(begin) if begin else str(a.get("start"))
        until = clock(finish) if finish else str(a.get("end"))
        lines.append("id: " + str(a.get("id")) + " | " + when + " to " + until + " | pet: " + str(pet) + " | status: " + str(a.get("status")) + " | provider: " + str(who))
    if not lines:
        return "No upcoming appointments for that client."
    return "\n".join(lines[:15])


@mcp.tool()
def check_open_slots(date: str, provider_id: str = "") -> str:
    """Open house-call windows for a date (YYYY-MM-DD), 9 AM to 5 PM Eastern, skipping times already booked."""
    day = datetime.strptime(date, "%Y-%m-%d")
    start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=TZ)
    end = start + timedelta(days=1)
    r = api("GET", "/census", params={"start": start.isoformat(), "end": end.isoformat(), "page": "1", "perPage": "200"})
    if r.status_code >= 400:
        return fail(r)
    busy = []
    for a in as_list(r.json()):
        if str(a.get("status", "")).lower() == "cancelled":
            continue
        if provider_id and str((a.get("provider") or {}).get("id")) != str(provider_id):
            continue
        begin = parse_iso(a.get("start"))
        finish = parse_iso(a.get("end"))
        if begin and finish:
            busy.append((begin, finish))
    lines = []
    slot = datetime(day.year, day.month, day.day, 9, 0, tzinfo=TZ)
    last = datetime(day.year, day.month, day.day, 17, 0, tzinfo=TZ)
    while slot < last:
        nxt = slot + timedelta(hours=1)
        taken = any(slot < b and nxt > a for a, b in busy)
        if not taken:
            lines.append(clock(slot) + " to " + clock(nxt) + " | start: " + slot.isoformat() + " | end: " + nxt.isoformat())
        slot = nxt
    if not lines:
        return "No open hour between 9 AM and 5 PM ET on " + date + "."
    return "\n".join(lines)


@mcp.tool()
def book_appointment(client_id: str, patient_id: str, start: str, end: str, provider_id: str, appointment_type_id: str, reason: str = "Home visit", location: str = "At client") -> str:
    """Book an appointment. start and end must be ISO8601. location At client for house calls."""
    body = {
        "reason": reason,
        "start": start,
        "end": end,
        "appointmentType": {"id": appointment_type_id},
        "provider": {"id": provider_id},
        "client": {"id": client_id},
        "patient": {"id": patient_id},
        "location": location,
    }
    r = api("POST", "/appointments", json=body)
    if r.status_code >= 400:
        return fail(r)
    made = one(r.json())
    return "Booked. Appointment id: " + str(made.get("id")) + " | " + str(made.get("start"))


@mcp.tool()
def reschedule_appointment(appointment_id: str, start: str, end: str) -> str:
    """Move an existing appointment. start and end must be ISO8601."""
    current = api("GET", "/appointments/" + appointment_id)
    if current.status_code >= 400:
        return fail(current)
    appt = one(current.json())
    body = {
        "reason": appt.get("reason") or "Home visit",
        "start": start,
        "end": end,
        "appointmentType": {"id": (appt.get("appointmentType") or {}).get("id")},
        "provider": {"id": (appt.get("provider") or {}).get("id")},
        "location": appt.get("location") or "At client",
    }
    if (appt.get("client") or {}).get("id"):
        body["client"] = {"id": appt["client"]["id"]}
    if (appt.get("patient") or {}).get("id"):
        body["patient"] = {"id": appt["patient"]["id"]}
    r = api("PUT", "/appointments/" + appointment_id, json=body)
    if r.status_code >= 400:
        return fail(r)
    return "Rescheduled appointment " + appointment_id + " to " + start


@mcp.tool()
def cancel_appointment(appointment_id: str) -> str:
    """Cancel an appointment as cancelled by the client."""
    r = api("PUT", "/census/" + appointment_id, json={"status": 3, "reason": 1})
    if r.status_code >= 400:
        return fail(r)
    return "Cancelled appointment " + appointment_id


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

from mcp.server.fastmcp import FastMCP
import os, time, requests

mcp = FastMCP("vetter-bridge", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
DOMAIN = os.environ.get("VETTER_DOMAIN")
API_KEY = os.environ.get("VETTER_API_KEY")
CLIENT_ID = os.environ.get("VETTER_CLIENT_ID")
CLIENT_SECRET = os.environ.get("VETTER_CLIENT_SECRET")
SCOPE = os.environ.get("VETTER_SCOPE", "APIService")

_token = None
_token_exp = 0

def get_token():
    global _token, _token_exp
    if _token and time.time() < _token_exp - 60:
        return _token
    r = requests.post(
        DOMAIN + "/api/oauth/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": SCOPE,
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    _token = data.get("access_token")
    _token_exp = time.time() + int(data.get("expires_in", 3600))
    return _token

def headers():
    return {
        "Authorization": "Bearer " + get_token(),
        "x-api-key": CLIENT_SECRET,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

@mcp.tool()
def find_client(phone: str) -> str:
    """Find a client by phone number. Returns client id, name, and pets."""
    r = requests.get(
        DOMAIN + "/api/1.0.0/" + API_KEY + "/clients",
        headers=headers(),
        params={"phone": phone, "page": "1", "perPage": "20"},
        timeout=30,
    )
    r.raise_for_status()
    payload = r.json()
    items = payload.get("data")
    if items is None:
        items = payload.get("items")
    if not items:
        return "No client found with that phone number."
    out = list()
    for c in items:
        pets_list = c.get("patients") or list()
        names = list()
        for p in pets_list:
            names.append(p.get("name", "?"))
        pets = ", ".join(names)
        line = "Client: " + str(c.get("firstName", "")) + " " + str(c.get("lastName", ""))
        line = line + " | id: " + str(c.get("id")) + " | pets: " + (pets or "none listed")
        out.append(line)
    return "\n".join(out)

@mcp.tool()
def check_open_slots(date: str, provider_id: str = "") -> str:
    """Check open appointment slots for a date (YYYY-MM-DD). Optionally filter by provider id."""
    params = {"date": date, "page": "1", "perPage": "50"}
    if provider_id:
        params.update({"provider_id": provider_id})
    r = requests.get(
        DOMAIN + "/api/1.0.0/" + API_KEY + "/appointments/open-slots",
        headers=headers(),
        params=params,
        timeout=30,
    )
    if r.status_code == 404:
        r = requests.get(
            DOMAIN + "/api/1.0.0/" + API_KEY + "/schedule/open-slots",
            headers=headers(),
            params=params,
            timeout=30,
        )
    r.raise_for_status()
    data = r.json()
    slots = data.get("data")
    if slots is None:
        slots = data.get("items")
    if slots is None and isinstance(data, list):
        slots = data
    if not slots:
        return "No open slots found for " + date + "."
    lines = list()
    count = 0
    for s in slots:
        if count >= 15:
            break
        provider = s.get("provider") or dict()
        start = str(s.get("start", "?"))
        end = str(s.get("end", "?"))
        name = str(provider.get("name", s.get("providerId", "?")))
        lines.append(start + " - " + end + " | provider: " + name)
        count = count + 1
    return "\n".join(lines)

@mcp.tool()
def book_appointment(
    client_id: str,
    patient_id: str,
    start: str,
    end: str,
    provider_id: str,
    appointment_type_id: str,
    reason: str = "Home visit",
    location: str = "At client",
) -> str:
    """Book an appointment. start/end in ISO8601. location At client for house calls."""
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
    r = requests.post(
        DOMAIN + "/api/1.0.0/" + API_KEY + "/appointments",
        headers=headers(),
        json=body,
        timeout=30,
    )
    if r.status_code >= 400:
        return "Booking failed (" + str(r.status_code) + "): " + r.text
    d = r.json()
    provider = d.get("provider") or dict()
    return (
        "Booked! Appointment id: "
        + str(d.get("id"))
        + " | "
        + str(d.get("start"))
        + " with provider "
        + str(provider.get("name", "?"))
    )

if __name__ == "__main__":
    mcp.run(transport="streamable-http")

from mcp.server.fastmcp import FastMCP
import os, time, requests

mcp = FastMCP("vetter-bridge")

DOMAIN = os.environ API_KEY = os.environ CLIENT_ID = os.environ CLIENT_SECRET = os.environ SCOPE = os.environ.get("VETTER_SCOPE", "APIService")

_token = None
_token_exp = 0

def get_token():
    global _token, _token_exp
    if _token and time.time() < _token_exp - 60:
        return _token
    r = requests.post(f"{DOMAIN}/api/oauth/access_token", data={
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": SCOPE,
    }, timeout=30)
    r.raise_for_status()
    data = r.json()
    _token = data _token_exp = time.time() + int(data.get("expires_in", 3600))
    return _token

def headers():
    return {
        "Authorization": f"Bearer {get_token()}",
        "x-api-key": CLIENT_SECRET,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

@mcp.tool()
def find_client(phone: str) -> str:
    """Find a client by phone number. Returns client id, name, and pets."""
    r = requests.get(f"{DOMAIN}/api/1.0.0/{API_KEY}/clients",
                     headers=headers(),
                     params={"phone": phone, "page": "1", "perPage": "20"},
                     timeout=30)
    r.raise_for_status()
    items = r.json().get("data", r.json().get("items", []))
    if not items:
        return "No client found with that phone number."
    out = []
    for c in items:
        pets = ", ".join(p.get("name", "?") for p in c.get("patients", []))
        out.append(f"Client: {c.get('firstName','')} {c.get('lastName','')} | id: {c.get('id')} | pets: {pets or 'none listed'}")
    return "\n".join(out)

@mcp.tool()
def check_open_slots(date: str, provider_id: str = "") -> str:
    """Check open appointment slots for a date (YYYY-MM-DD). Optionally filter by provider id."""
    params = {"date": date, "page": "1", "perPage": "50"}
    if provider_id:
        params = provider_id
    r = requests.get(f"{DOMAIN}/api/1.0.0/{API_KEY}/appointments/open-slots",
                     headers=headers(), params=params, timeout=30)
    if r.status_code == 404:
        r = requests.get(f"{DOMAIN}/api/1.0.0/{API_KEY}/schedule/open-slots",
                         headers=headers(), params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    slots = data.get("data", data.get("items", data if isinstance(data, list) else []))
    if not slots:
        return f"No open slots found for {date}."
    lines = []
    for s in slots[:15]:
        lines.append(f"{s.get('start','?')} - {s.get('end','?')} | provider: {s.get('provider',{}).get('name', s.get('providerId','?'))}")
    return "\n".join(lines)

@mcp.tool()
def book_appointment(client_id: str, patient_id: str, start: str, end: str,
                     provider_id: str, appointment_type_id: str,
                     reason: str = "Home visit", location: str = "At client") -> str:
    """Book an appointment. start/end in ISO8601. location 'At client' for house calls."""
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
    r = requests.post(f"{DOMAIN}/api/1.0.0/{API_KEY}/appointments",
                      headers=headers(), json=body, timeout=30)
    if r.status_code >= 400:
        return f"Booking failed ({r.status_code}): {r.text[:500]}"
    d = r.json()
    return f"Booked! Appointment id: {d.get('id')} | {d.get('start')} with provider {d.get('provider',{}).get('name','?')}"

if __name__ == "__main__":
    mcp.run(transport="streamable-http")

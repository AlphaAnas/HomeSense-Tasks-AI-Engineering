import os
import re

import logging
import sqlite3
import datetime
from typing import Optional

from dotenv import load_dotenv

from pydantic import BaseModel
from google import genai
from google.genai import types


from prompts import SYSTEM_INSTRUCTION



load_dotenv()

DB_PATH = "listings.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler("conversation.log"), logging.StreamHandler()],
)
log = logging.getLogger("leasing_agent")

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
MODEL = "gemini-3.5-flash"



# ---------- DB helpers / tools ----------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def search_units(city: Optional[str] = None, max_rent: Optional[int] = None, min_beds: Optional[int] = None):
    conn = db()
    q = """SELECT u.id, u.unit_number, u.beds, u.baths, u.rent, u.available_from,
                  b.name as building_name, b.address, b.city, b.state, b.zip
           FROM units u JOIN buildings b ON u.building_id = b.id
           WHERE u.is_active = 1"""
    params = []
    if city:
        q += " AND b.city = ?"
        params.append(city)
    if max_rent is not None:
        q += " AND u.rent IS NOT NULL AND u.rent <= ?"
        params.append(max_rent)
    if min_beds is not None:
        q += " AND u.beds >= ?"
        params.append(min_beds)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["rent"] = d["rent"] if d["rent"] is not None else "NOT_ON_FILE"
        result.append(d)
    return {"units": result}

def get_unit_details(unit_id: int):
    conn = db()
    row = conn.execute(
        """SELECT u.*, b.name as building_name, b.address, b.city, b.state, b.zip
           FROM units u JOIN buildings b ON u.building_id = b.id
           WHERE u.id = ?""", (unit_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {"error": "unit_not_found"}
    d = dict(row)
    d["rent"] = d["rent"] if d["rent"] is not None else "NOT_ON_FILE"
    return d

def request_tour(unit_id: int, tour_time: str, client_name: str):
    conn = db()
    unit = conn.execute("SELECT * FROM units WHERE id = ?", (unit_id,)).fetchone()
    if not unit:
        conn.close()
        return {"success": False, "reason": "unit does not exist"}
    if unit["is_active"] != 1:
        conn.close()
        return {"success": False, "reason": "unit is inactive"}
    try:
        t = datetime.datetime.fromisoformat(tour_time)
    except ValueError:
        conn.close()
        return {"success": False, "reason": "invalid tour_time format, use ISO 8601"}
    if not (9 <= t.hour < 18 or (t.hour == 18 and t.minute == 0 and t.second == 0)):
        conn.close()
        return {"success": False, "reason": "requested time is outside allowed hours 09:00-18:00"}
    conn.execute(
        "INSERT INTO tours (unit_id, tour_at, client_name, created_at) VALUES (?,?,?,?)",
        (unit_id, tour_time, client_name, datetime.datetime.now().isoformat()),
    )
    conn.commit()
    tour_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return {"success": True, "tour_id": tour_id}

TOOL_IMPL = {
    "search_units": search_units,
    "get_unit_details": get_unit_details,
    "request_tour": request_tour,
}

# ---------- Gemini tool schema ----------

tools = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="search_units",
        description="Search active units by city, max rent, min beds.",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "max_rent": {"type": "integer"},
                "min_beds": {"type": "integer"},
            },
        },
    ),
    types.FunctionDeclaration(
        name="get_unit_details",
        description="Get full details of one unit by id.",
        parameters={
            "type": "object",
            "properties": {"unit_id": {"type": "integer"}},
            "required": ["unit_id"],
        },
    ),
    types.FunctionDeclaration(
        name="request_tour",
        description="Book a tour for a unit. Server enforces business rules; refusal reasons must be relayed to the user as-is.",
        parameters={
            "type": "object",
            "properties": {
                "unit_id": {"type": "integer"},
                "tour_time": {"type": "string", "description": "ISO 8601, e.g. 2025-06-10T14:00:00"},
                "client_name": {"type": "string"},
            },
            "required": ["unit_id", "tour_time", "client_name"],
        },
    ),
])


# ---------- session store ----------

SESSIONS = {}  # session_id -> list[types.Content]

def refresh_known_rents():
    conn = db()
    rows = conn.execute("SELECT rent FROM units WHERE rent IS NOT NULL").fetchall()
    conn.close()
    return {str(r["rent"]) for r in rows}

def grounding_check(reply_text: str):
    '''Checks if any rental prices mentioned in the reply actually exist in the database,
      and logs a warning if a price is not found.'''
    real = refresh_known_rents()
    found = re.findall(r"\$\s?([\d,]{3,7})", reply_text)
    for f in found:
        num = f.replace(",", "")
        if num not in real:
            log.warning(f"UNGROUNDED PRICE DETECTED in reply: ${f}")
    return reply_text

# ---------- chat endpoint ----------

class ChatRequest(BaseModel):
    session_id: str
    message: str

def call_gemini_with_retry(contents):
    for attempt in range(2):
        try:
            return client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    tools=[tools],
                ),
            )
        except Exception as e:
            log.error(f"Gemini call failed (attempt {attempt+1}): {e}")
            if attempt == 1:
                raise
    return None

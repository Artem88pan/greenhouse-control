import os
from typing import List, Dict, Any
import httpx
from base64 import b64encode

STATE_BASE = os.getenv("STATE_BASE", "http://state-mock:8000")
STATE_USER = os.getenv("STATE_USERNAME", "state_user")
STATE_PASS = os.getenv("STATE_PASSWORD", "state_pass")

def _auth_header() -> Dict[str, str]:
    token = b64encode(f"{STATE_USER}:{STATE_PASS}".encode()).decode()
    return {"Authorization": f"Basic {token}"}

async def evaluate(client: httpx.AsyncClient, rows: List[Dict[str, Any]]) -> int:

    r = await client.post(
        f"{STATE_BASE}/greenhouse_state/",
        json=rows,
        headers=_auth_header(),
        timeout=None)
    r.raise_for_status()
    return int(r.json())

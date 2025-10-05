

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from uuid import UUID

import httpx
from httpx import BasicAuth

GH_BASE = os.getenv("GREENHOUSE_BASE", "http://greenhouse-mock:8000")
GH_USER = os.getenv("GH_USERNAME", "greenhouse_user")
GH_PASS = os.getenv("GH_PASSWORD", "greenhouse_pass")

auth = BasicAuth(GH_USER, GH_PASS)


async def get_info(client: httpx.AsyncClient, gid: UUID) -> Dict[str, Any]:
    r = await client.get(f"{GH_BASE}/greenhouse_info/{gid}", auth=auth, timeout=30.0)
    r.raise_for_status()
    return r.json()


async def _post_series(client: httpx.AsyncClient, path: str, ids: List[UUID]):
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    frm = now - timedelta(hours=23)
    payload = {
        "greenhouses": [str(g) for g in ids],
        "dt_from": frm.isoformat(),
        "dt_to": now.isoformat(),
    }
    r = await client.post(f"{GH_BASE}{path}", json=payload, auth=auth, timeout=60.0)
    r.raise_for_status()
    return r.json()

async def get_temperature(client: httpx.AsyncClient, ids: List[UUID]):
    return await _post_series(client, "/temperature/", ids)

async def get_humidity(client: httpx.AsyncClient, ids: List[UUID]):
    return await _post_series(client, "/humidity/", ids)

async def get_ph(client: httpx.AsyncClient, ids: List[UUID]):
    return await _post_series(client, "/pH/", ids)

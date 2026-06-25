"""
Czech ARES (Administrative Register of Economic Subjects) integration.
Public REST API — no auth required.
"""
from __future__ import annotations

import logging

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_BASE = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty"
_TIMEOUT = 8
_DEFAULT_SEARCH_LIMIT = 8


class AresResult(BaseModel):
    ico: str
    name: str
    dic: str = ""
    address: str = ""


def _build_address(sidlo: dict) -> str:
    parts = [
        sidlo.get("nazevUlice", ""),
        str(sidlo.get("cisloDomovni", "")) if sidlo.get("cisloDomovni") else "",
        sidlo.get("nazevObce", ""),
        str(sidlo.get("psc", "")) if sidlo.get("psc") else "",
    ]
    return ", ".join(p for p in parts if p)


async def get_by_ico(ico: str) -> AresResult | None:
    url = f"{_BASE}/{ico}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("ARES lookup failed for ICO %s: %s", ico, exc)
        return None

    return AresResult(
        ico=ico,
        name=data.get("obchodniJmeno", ""),
        dic=data.get("dic", ""),
        address=_build_address(data.get("sidlo", {})),
    )


async def search_by_name(name: str, limit: int = _DEFAULT_SEARCH_LIMIT) -> list[AresResult]:
    url = f"{_BASE}/vyhledat"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json={"obchodniJmeno": name, "pocet": limit})
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("ARES search failed for '%s': %s", name, exc)
        return []

    results: list[AresResult] = []
    for item in data.get("ekonomickeSubjekty", []):
        results.append(AresResult(
            ico=item.get("ico", ""),
            name=item.get("obchodniJmeno", ""),
            dic=item.get("dic", ""),
            address=_build_address(item.get("sidlo", {})),
        ))
    return results

import httpx
from pathlib import Path

from .config import settings


async def send_discord(message: str, file_path: str | None = None) -> bool:
    if not settings.DISCORD_WEBHOOK_URL:
        return False
    try:
        async with httpx.AsyncClient() as client:
            if file_path:
                with open(file_path, "rb") as f:
                    files = {"file": (Path(file_path).name, f, "application/pdf")}
                    data = {"content": message}
                    resp = await client.post(
                        settings.DISCORD_WEBHOOK_URL, data=data, files=files, timeout=20
                    )
            else:
                resp = await client.post(
                    settings.DISCORD_WEBHOOK_URL, json={"content": message}, timeout=10
                )
            resp.raise_for_status()
            return True
    except Exception:
        return False

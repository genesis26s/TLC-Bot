import aiohttp
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any

class RobloxAuditEngine:
    @staticmethod
    async def fetch_user_data(username: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        async with aiohttp.ClientSession() as session:
            # Username to ID Resolution
            async with session.post("https://users.roblox.com/v1/usernames/users", json={"usernames": [username]}) as resp:
                if resp.status != 200:
                    return False, "Roblox API unreachable.", None
                data = await resp.json()
                if not data.get("data"):
                    return False, f"Roblox user `{username}` does not exist.", None
                
                roblox_id = data["data"][0]["id"]
                roblox_name = data["data"][0]["name"]

            # Fetch Detailed Profile Data
            async with session.get(f"https://users.roblox.com/v1/users/{roblox_id}") as resp:
                if resp.status != 200:
                    return False, "Failed to retrieve Roblox profile details.", None
                
                user_info = await resp.json()
                user_info["resolved_id"] = roblox_id
                user_info["resolved_name"] = roblox_name
                return True, "Success", user_info

    @staticmethod
    def calculate_account_age(created_iso: str) -> int:
        dt = datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days

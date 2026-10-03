import aiohttp
from typing import Tuple, Optional
import logging

logger = logging.getLogger("TLCBot.ThreatIntel")

class ThreatIntelEngine:
    @staticmethod
    async def check_ip(ip_address: str, api_key: Optional[str] = None) -> Tuple[bool, int, str]:
        if not ip_address or ip_address in ("127.0.0.1", "0.0.0.0"):
            return False, 0, "Clean Network (Localhost)"

        url = f"https://proxycheck.io/v2/{ip_address}?vpn=1&asn=1"
        if api_key:
            url += f"&key={api_key}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=5) as resp:
                    if resp.status != 200:
                        return False, 0, "Proxy API Unavailable"
                    
                    data = await resp.json()
                    ip_data = data.get(ip_address, {})
                    is_proxy = ip_data.get("proxy") == "yes"
                    node_type = ip_data.get("type", "Residential/Unknown")
                    risk_score = 90 if is_proxy else 0

                    return is_proxy, risk_score, f"Type: {node_type}"
        except Exception as e:
            logger.error(f"Threat intel check error: {e}")
            return False, 0, f"Check Error: {str(e)}"

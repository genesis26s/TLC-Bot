import aiohttp
from typing import Tuple, List, Optional
import logging

logger = logging.getLogger("TLCBot.ThreatIntel")

class ThreatIntelEngine:
    def __init__(self):
        self.known_bad_ips = set()

    @staticmethod
    async def check_ip(ip_address: str, api_key: Optional[str] = None) -> Tuple[bool, int, str]:
        if not ip_address or ip_address in ("127.0.0.1", "0.0.0.0", "::1"):
            return False, 0, "Clean Network (Localhost)"

        if ip_address.startswith(("10.", "172.16.", "192.168.")):
            return False, 0, "Private Subnet"

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

    async def check_ip_risk(self, client_ip: str) -> Tuple[int, List[str]]:
        risk_score = 0
        reasons = []

        if client_ip in ("127.0.0.1", "::1") or client_ip.startswith(("10.", "172.16.", "192.168.")):
            return 0, []

        if client_ip in self.known_bad_ips:
            risk_score += 40
            reasons.append(f"Client IP ({client_ip}) flagged in internal threat blacklist.")

        is_proxy, threat_risk, vpn_details = await self.check_ip(client_ip)
        if is_proxy:
            risk_score += threat_risk
            reasons.append(f"Proxy/VPN detected: {vpn_details}")

        return risk_score, reasons

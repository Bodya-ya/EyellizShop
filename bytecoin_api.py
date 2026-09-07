import os
import httpx
from decimal import Decimal
import logging
logger = logging.getLogger(__name__)




class BytecoinAPI:
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json"
        }

    async def get_service_info(self) -> dict:
        """Получает информацию о сервисе"""
        async with httpx.AsyncClient(verify=False, trust_env=False) as client:
            response = await client.get(
                f"{self.base_url}/service/info",
                headers=self.headers
            )
            if response.status_code == 200:
                return response.json()["data"]
            else:
                logger.error(f"get_service_info failed: {response.text}")
                raise Exception(f"API error: {response.status_code}")

    async def get_balance(self) -> Decimal:
        """Возвращает баланс монет сервиса"""
        info = await self.get_service_info()
        return Decimal(info["balance"])

    async def transfer_to_user(self, user_id: int, sum_coins: Decimal, idempotency_key: str) -> dict:
        # Отладка
        logger.info(f"DEBUG transfer: user_id={user_id}, sum={sum_coins}, key={idempotency_key}")
        logger.info(f"DEBUG sum format: {sum_coins:.9f}")
        async with httpx.AsyncClient(verify=False, trust_env=False) as client:
            response = await client.post(
                f"{self.base_url}/service/transfer",
                headers={
                    **self.headers,
                    "Idempotency-Key": idempotency_key
                },
                json={
                    "user_id": user_id,
                    "sum": f"{sum_coins:.9f}"
                }
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"transfer_to_user failed: {response.text}")
                return response.json()

    async def get_user_info(self, user_ids: list[int]) -> dict:
        """Получает информацию о пользователях"""
        async with httpx.AsyncClient(verify=False, trust_env=False) as client:
            response = await client.post(
                f"{self.base_url}/users/info",
                headers=self.headers,
                json={"user_ids": user_ids}
            )
            if response.status_code == 200:
                return response.json()["data"]
            else:
                logger.error(f"get_user_info failed: {response.text}")
                raise Exception(f"API error: {response.status_code}")

    async def get_service_history(self, limit: int = 20, offset: int = 0) -> dict:
        """Получает историю операций"""
        async with httpx.AsyncClient(verify=False, trust_env=False) as client:
            response = await client.get(
                f"{self.base_url}/service/history",
                headers=self.headers,
                params={"limit": limit, "offset": offset}
            )
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"get_service_history failed: {response.text}")
                raise Exception(f"API error: {response.status_code}")


bytecoin_api = BytecoinAPI(
    api_key=os.getenv("BYTECOIN_API_KEY", ""),
    base_url=os.getenv("BYTECOIN_BASE_URL", "https://bytecoin.space/api/public/v1")
)
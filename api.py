"""
MoviePilot API 客户端模块

提供与 MoviePilot 后端 API 的异步交互能力，包括：
- API Key 认证（Query 参数方式）
- 媒体信息搜索
- 电影/电视剧订阅
- 下载进度查询
"""

from typing import Any
from astrbot.api import logger
import httpx
import asyncio


class MoviepilotApi:
    """MoviePilot API 客户端"""

    def __init__(self, config: dict[str, Any]):
        """
        初始化 API 客户端

        Args:
            config: 配置字典，需包含 mp_url, mp_apikey
        """
        self.base_url = config.get("mp_url", "").rstrip("/")
        self.api_key = config.get("mp_apikey", "")
        self.timeout = config.get("mp_timeout", 120)
        self.max_retries = config.get("mp_max_retries", 3)
        self.retry_delay = config.get("mp_retry_delay", 1)

    def validate_config(self) -> tuple[bool, str]:
        """
        验证配置是否完整

        Returns:
            (是否有效, 错误信息)
        """
        if not self.base_url:
            return False, "MoviePilot 地址未配置 (mp_url)"
        if not self.api_key:
            return False, "MoviePilot API Key 未配置 (mp_apikey)"
        return True, ""

    async def _request(
        self,
        url: str,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any | None:
        """
        发送 HTTP 请求（带重试机制）

        Args:
            url: 请求 URL
            method: 请求方法 (GET/POST_JSON)
            params: URL 查询参数（API Key 会自动附加）
            data: 请求体数据

        Returns:
            响应数据或 None
        """
        headers = {"User-Agent": "AstrBot-MP-Plugin/2.0.0"}

        # API Key 通过查询参数传递
        params = dict(params) if params else {}
        params["token"] = self.api_key

        timeout = httpx.Timeout(self.timeout, read=self.timeout)

        logger.debug(f"API 请求: {method} {url}")

        # 重试逻辑
        last_error = None
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    if method == "GET":
                        response = await client.get(url, headers=headers, params=params)
                    elif method == "POST_JSON":
                        response = await client.post(
                            url, headers=headers, params=params, json=data
                        )
                    else:
                        logger.error(f"不支持的请求方法: {method}")
                        return None

                    if response.status_code == 200:
                        return response.json()
                    elif response.status_code in (401, 403):
                        logger.error("认证失败 (401/403)，请检查 API Key 是否正确")
                        return None
                    else:
                        logger.warning(f"请求失败 ({response.status_code}): {response.text}")
                        last_error = f"HTTP {response.status_code}"

            except httpx.TimeoutException as e:
                last_error = f"请求超时: {e}"
                logger.warning(f"请求超时 (尝试 {attempt + 1}/{self.max_retries}): {url}")
            except httpx.ConnectError as e:
                last_error = f"连接错误: {e}"
                logger.warning(f"连接失败 (尝试 {attempt + 1}/{self.max_retries}): {url}")
            except Exception as e:
                last_error = f"请求异常: {e}"
                logger.warning(f"请求异常 (尝试 {attempt + 1}/{self.max_retries}): {e}")

            if attempt < self.max_retries - 1:
                await asyncio.sleep(self.retry_delay * (attempt + 1))

        logger.error(f"请求最终失败 ({self.max_retries} 次重试): {last_error}")
        return None

    @staticmethod
    def _unwrap(response: Any) -> Any:
        """Unwrap MoviePilot's ``{"success": ..., "data": ...}`` envelope."""
        if isinstance(response, dict) and "data" in response:
            return response["data"]
        return response

    async def search_media_info(self, media_name: str) -> list[dict] | None:
        """
        搜索媒体信息

        Args:
            media_name: 媒体名称

        Returns:
            搜索结果列表或 None
        """
        try:
            return self._unwrap(
                await self._request(
                    url=self.base_url + "/api/v1/media/search",
                    method="GET",
                    params={"title": media_name},
                )
            )
        except Exception as e:
            logger.error(f"搜索媒体失败: {e}")
            return None

    async def list_all_seasons(self, tmdbid: str | int | None) -> list[dict] | None:
        """
        获取电视剧所有季

        Args:
            tmdbid: TMDB ID

        Returns:
            季度列表或 None
        """
        # 验证 tmdbid
        if not tmdbid or str(tmdbid) in ("tv", "movie"):
            logger.warning(f"获取季度列表时收到无效的 TMDB ID: {tmdbid}")
            return None

        try:
            tmdb_id_int = int(tmdbid)
        except (ValueError, TypeError):
            logger.warning(f"获取季度列表时 TMDB ID 格式错误: {tmdbid}")
            return None

        try:
            return self._unwrap(
                await self._request(
                    url=self.base_url + f"/api/v1/tmdb/seasons/{tmdb_id_int}",
                    method="GET",
                )
            )
        except Exception as e:
            logger.error(f"获取季度列表失败: {e}")
            return None

    async def subscribe_movie(self, movie: dict[str, Any]) -> bool:
        """
        订阅电影

        Args:
            movie: 电影信息字典

        Returns:
            是否订阅成功
        """
        body = {
            "name": movie.get("title"),
            "tmdbid": movie.get("tmdb_id"),
            "type": "电影",
        }
        try:
            response = await self._request(
                url=self.base_url + "/api/v1/subscribe/",
                method="POST_JSON",
                data=body,
            )
            success = response.get("success", False) if response else False
            if success:
                logger.info(f"成功订阅电影: {movie.get('title')}")
            return success
        except Exception as e:
            logger.error(f"订阅电影失败: {e}")
            return False

    async def subscribe_series(self, movie: dict[str, Any], season: int) -> bool:
        """
        订阅电视剧指定季

        Args:
            movie: 电视剧信息字典
            season: 季数

        Returns:
            是否订阅成功
        """
        body = {
            "name": movie.get("title"),
            "tmdbid": movie.get("tmdb_id"),
            "season": season,
        }
        try:
            response = await self._request(
                url=self.base_url + "/api/v1/subscribe/",
                method="POST_JSON",
                data=body,
            )
            success = response.get("success", False) if response else False
            if success:
                logger.info(f"成功订阅电视剧: {movie.get('title')} 第{season}季")
            return success
        except Exception as e:
            logger.error(f"订阅电视剧失败: {e}")
            return False

    async def get_download_progress(self) -> list[dict] | None:
        """
        获取下载进度

        Returns:
            下载任务列表或 None
        """
        try:
            data = self._unwrap(
                await self._request(
                    url=self.base_url + "/api/v1/download/",
                    method="GET",
                )
            )
            if data is None:
                return None
            return data if data else []
        except Exception as e:
            logger.error(f"获取下载进度失败: {e}")
            return None

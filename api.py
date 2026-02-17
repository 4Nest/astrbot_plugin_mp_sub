"""
MoviePilot API 客户端模块

提供与 MoviePilot 后端 API 的异步交互能力，包括：
- 用户认证与 Token 管理（带缓存）
- 媒体信息搜索
- 电影/电视剧订阅
- 下载进度查询
"""

from typing import Any
from astrbot.api import logger
import httpx
import asyncio
import time


class MoviepilotApi:
    """MoviePilot API 客户端"""

    def __init__(self, config: dict[str, Any]):
        """
        初始化 API 客户端

        Args:
            config: 配置字典，需包含 mp_url, mp_username, mp_password
        """
        self.base_url = config.get("mp_url", "").rstrip("/")
        self.mp_username = config.get("mp_username", "")
        self.mp_password = config.get("mp_password", "")
        self.timeout = config.get("mp_timeout", 120)
        self.max_retries = config.get("mp_max_retries", 3)
        self.retry_delay = config.get("mp_retry_delay", 1)

        # Token 缓存
        self._cached_token: str | None = None
        self._token_expires_at: float = 0
        self._token_lock = asyncio.Lock()
        self._token_buffer = 300  # Token 过期前 5 分钟重新获取

    def validate_config(self) -> tuple[bool, str]:
        """
        验证配置是否完整

        Returns:
            (是否有效, 错误信息)
        """
        if not self.base_url:
            return False, "MoviePilot URL 未配置 (mp_url)"
        if not self.mp_username:
            return False, "MoviePilot 用户名未配置 (mp_username)"
        if not self.mp_password:
            return False, "MoviePilot 密码未配置 (mp_password)"
        return True, ""

    async def _get_mp_token(self) -> str | None:
        """
        获取 MoviePilot 访问令牌（带缓存）

        Returns:
            访问令牌或 None（获取失败时）
        """
        # 检查缓存是否有效
        current_time = time.time()
        async with self._token_lock:
            if self._cached_token and current_time < (self._token_expires_at - self._token_buffer):
                logger.debug("使用缓存的 Token")
                return self._cached_token

        # 缓存无效，重新获取
        if not self.mp_password:
            logger.error("MoviePilot 密码不能为空")
            return None

        api_path = "/api/v1/login/access-token"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        form_data = {
            "username": self.mp_username,
            "password": self.mp_password,
        }

        data = await self._request(
            url=self.base_url + api_path,
            method="POST_FORM",
            headers=headers,
            data=form_data,
            use_auth=False,
        )

        if data and "access_token" in data:
            token = data["access_token"]
            # 解析 token 获取过期时间（JWT 格式）
            expires_in = data.get("expires_in", 3600)  # 默认 1 小时
            async with self._token_lock:
                self._cached_token = token
                self._token_expires_at = current_time + expires_in
            logger.info("Token 获取成功，缓存生效")
            return token
        return None

    async def _get_headers(self) -> dict[str, str] | None:
        """
        获取带认证的请求头

        Returns:
            请求头字典或 None（认证失败时）
        """
        token = await self._get_mp_token()
        if not token:
            logger.error("访问 MoviePilot 失败，请确认密码或是否开启了两步验证")
            return None
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": "AstrBot-MP-Plugin/1.2.0",
        }

    async def _request(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        use_auth: bool = True,
    ) -> Any | None:
        """
        发送 HTTP 请求（带重试机制）

        Args:
            url: 请求 URL
            method: 请求方法 (GET/POST_JSON/POST_FORM)
            headers: 请求头
            data: 请求数据
            use_auth: 是否需要认证

        Returns:
            响应数据或 None
        """
        if headers is None:
            headers = {"User-Agent": "AstrBot-MP-Plugin/1.2.0"}

        # 需要认证时获取认证头
        if use_auth:
            auth_headers = await self._get_headers()
            if not auth_headers:
                return None
            headers.update(auth_headers)

        timeout = httpx.Timeout(self.timeout, read=self.timeout)

        logger.debug(f"API 请求: {method} {url}")

        # 重试逻辑
        last_error = None
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    if method == "GET":
                        response = await client.get(url, headers=headers)
                    elif method == "POST_JSON":
                        response = await client.post(url, headers=headers, json=data)
                    elif method == "POST_FORM":
                        response = await client.post(url, headers=headers, data=data)
                    else:
                        logger.error(f"不支持的请求方法: {method}")
                        return None

                    if response.status_code == 200:
                        return response.json()
                    elif response.status_code == 401:
                        logger.error("认证失败 (401)，请检查用户名密码")
                        # 清除缓存，下次重新获取
                        async with self._token_lock:
                            self._cached_token = None
                            self._token_expires_at = 0
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

    async def search_media_info(self, media_name: str) -> list[dict] | None:
        """
        搜索媒体信息

        Args:
            media_name: 媒体名称

        Returns:
            搜索结果列表或 None
        """
        api_path = f"/api/v1/media/search?title={media_name}"
        try:
            return await self._request(
                url=self.base_url + api_path,
                method="GET",
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

        api_path = f"/api/v1/tmdb/seasons/{tmdb_id_int}"
        try:
            return await self._request(
                url=self.base_url + api_path,
                method="GET",
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
        api_path = "/api/v1/subscribe/"
        body = {
            "name": movie.get("title"),
            "tmdbid": movie.get("tmdb_id"),
            "type": "电影",
        }
        try:
            response = await self._request(
                url=self.base_url + api_path,
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
        api_path = "/api/v1/subscribe/"
        body = {
            "name": movie.get("title"),
            "tmdbid": movie.get("tmdb_id"),
            "season": season,
        }
        try:
            response = await self._request(
                url=self.base_url + api_path,
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
        api_path = "/api/v1/download/"
        try:
            data = await self._request(
                url=self.base_url + api_path,
                method="GET",
            )
            if data is None:
                return None
            return data if data else []
        except Exception as e:
            logger.error(f"获取下载进度失败: {e}")
            return None

    async def clear_token_cache(self) -> None:
        """清除 Token 缓存（用于测试或手动刷新）"""
        async with self._token_lock:
            self._cached_token = None
            self._token_expires_at = 0
        logger.info("Token 缓存已清除")

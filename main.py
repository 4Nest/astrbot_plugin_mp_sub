"""
MoviePilot 订阅插件

基于 AstrBot 框架的 MoviePilot 订阅管理插件，
支持搜索订阅影片和查看下载进度。
"""

from typing import Any
import asyncio

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp
from astrbot.core.utils.session_waiter import (
    session_waiter,
    SessionController,
)

from .api import MoviepilotApi


@register(
    "moviepilot_sub",
    "4Nest",
    "MoviePilot 订阅管理插件",
    "1.2.0",
    "https://github.com/4Nest/astrbot_plugin_mp_sub",
)
class MoviePilotPlugin(Star):
    """MoviePilot 订阅插件主类"""

    def __init__(self, context: Context, config: dict[str, Any]):
        super().__init__(context)
        self.config = config
        self.api = MoviepilotApi(config)
        self.state: dict[str, dict[str, Any]] = {}
        self.state_lock = asyncio.Lock()

        # 验证配置
        self._validate_config()

    def _validate_config(self) -> None:
        """验证配置并在启动时报告问题"""
        valid, error_msg = self.api.validate_config()
        if not valid:
            logger.error(f"[MoviePilot] 配置错误: {error_msg}")
        else:
            logger.info("[MoviePilot] 配置验证通过")

    @filter.command("mp_help")
    async def show_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """📺 MoviePilot 订阅插件使用帮助

📌 可用命令：
━━━━━━━━━━━━━━━━━━
/sub <片名>
  搜索并订阅影片（支持电影和电视剧）
  示例：/sub 星际穿越

/download
  查看当前下载进度

/mp_help
  显示本帮助信息
━━━━━━━━━━━━━━━━━━

💡 使用提示：
• 搜索后回复序号进行订阅
• 回复 0 取消操作
• 电视剧会自动列出可选季度
• 搜索超时时间为 60 秒
"""
        yield event.plain_result(help_text)

    @filter.command("sub")
    async def subscribe(self, event: AstrMessageEvent, message: str):
        """
        订阅影片

        Args:
            message: 影片名称
        """
        # 检查输入
        if not message or not message.strip():
            yield event.plain_result("❌ 请输入影片名称，例如：/sub 星际穿越")
            return

        # 检查 API 配置
        valid, error_msg = self.api.validate_config()
        if not valid:
            yield event.plain_result(f"⚠️ 插件配置错误：{error_msg}\n请联系管理员检查配置。")
            return

        media_name = message.strip()
        user_id = event.get_sender_id()
        logger.info(f"用户 {user_id} 搜索影片: {media_name}")

        # 搜索影片
        try:
            movies = await self.api.search_media_info(media_name)
        except Exception as e:
            logger.error(f"搜索影片异常: {e}")
            yield event.plain_result("❌ 搜索服务暂时不可用，请稍后重试。")
            return

        if not movies:
            yield event.plain_result(f'🔍 未找到与 "{media_name}" 相关的影片，请尝试其他关键词。')
            return

        # 显示搜索结果
        result_lines = [
            "🔍 搜索结果",
            "━━━━━━━━━━━━━━━━━━",
            f"共找到 {len(movies)} 部相关影片：",
            "",
        ]
        for i, movie in enumerate(movies, 1):
            title = movie.get('title', '未知')
            year = movie.get('year', '')
            media_type = movie.get('type', '未知')
            type_icon = "🎬" if media_type == "电影" else "📺"
            year_str = f" ({year})" if year else ""
            result_lines.append(f"  {i}. {type_icon} {title}{year_str}")
        
        result_lines.extend([
            "",
            "💡 提示：回复序号订阅（0 取消）",
            "━━━━━━━━━━━━━━━━━━",
        ])
        result_text = "\n".join(result_lines)
        yield event.plain_result(result_text)

        # 启动会话等待用户选择
        await self._wait_for_movie_selection(event, movies)

    async def _wait_for_movie_selection(self, event: AstrMessageEvent, movies: list[dict]) -> None:
        """
        等待用户选择影片

        Args:
            event: 原始消息事件
            movies: 影片列表
        """
        user_id = event.get_sender_id()

        @session_waiter(timeout=60, record_history_chains=False)
        async def selection_waiter(controller: SessionController, ev: AstrMessageEvent):
            user_input = ev.message_str.strip()
            current_state = await self._get_user_state(user_id)

            # 处理季度选择状态
            if current_state.get("waiting_for") == "season":
                await self._process_season_selection(ev, controller, user_id, user_input)
                return

            # 处理影片选择
            await self._process_movie_index_selection(ev, controller, user_id, user_input, movies)

        try:
            await selection_waiter(event)
        except TimeoutError:
            await event.send(event.plain_result("⏰ 操作超时，已退出选择。"))
        except Exception as e:
            logger.error(f"会话处理异常: {e}")
            await event.send(event.plain_result("❌ 发生错误，请重新尝试。"))
        finally:
            await self._clear_user_state(user_id)
            event.stop_event()

    async def _process_movie_index_selection(
        self,
        event: AstrMessageEvent,
        controller: SessionController,
        user_id: str,
        user_input: str,
        movies: list[dict],
    ) -> None:
        """
        处理影片索引选择

        Args:
            event: 消息事件
            controller: 会话控制器
            user_id: 用户ID
            user_input: 用户输入
            movies: 影片列表
        """

        try:
            index = int(user_input) - 1
        except ValueError:
            await event.send(event.plain_result("⚠️ 请输入有效的数字序号。"))
            controller.keep(timeout=60, reset_timeout=True)
            return

        # 用户取消
        if index == -1:
            await event.send(event.plain_result("❌ 已取消操作。"))
            controller.stop()
            return

        # 验证索引范围
        if not (0 <= index < len(movies)):
            await event.send(event.plain_result("⚠️ 无效的序号，请输入列表中的数字。"))
            controller.keep(timeout=60, reset_timeout=True)
            return

        selected_movie = movies[index]
        logger.info(f"用户 {user_id} 选择了: {selected_movie.get('title')}")

        # 处理电视剧
        if selected_movie.get("type") == "电视剧":
            await self._handle_tv_series_selection(event, controller, user_id, selected_movie)
        else:
            # 处理电影订阅
            await self._subscribe_movie(event, controller, selected_movie)

    async def _handle_tv_series_selection(
        self,
        event: AstrMessageEvent,
        controller: SessionController,
        user_id: str,
        movie: dict[str, Any],
    ) -> None:
        """
        处理电视剧选择，获取并显示季度列表

        Args:
            event: 消息事件
            controller: 会话控制器
            user_id: 用户ID
            movie: 电视剧信息
        """
        tmdb_id = movie.get("tmdb_id")
        if not tmdb_id or str(tmdb_id) in ("tv", "movie"):
            logger.warning(f"电视剧缺少有效的 TMDB ID: {tmdb_id}")
            await event.send(event.plain_result("❌ 影片信息不完整，无法获取季度信息。\n这可能是因为该影片缺少 TMDB 信息，建议直接尝试订阅。"))
            controller.stop()
            return

        # 获取季度列表
        try:
            seasons = await self.api.list_all_seasons(tmdb_id)
        except Exception as e:
            logger.error(f"获取季度列表失败: {e}")
            await event.send(event.plain_result("❌ 无法获取季度信息，请稍后重试。"))
            controller.stop()
            return

        if not seasons:
            await event.send(event.plain_result("❌ 没有找到可用的季度信息。"))
            controller.stop()
            return

        # 显示季度列表
        result_lines = [
            f"📺 {movie.get('title', '未知')}",
            "━━━━━━━━━━━━━━━━━━",
            "📂 请选择要订阅的季度：",
            "",
        ]
        for s in seasons:
            season_num = s.get('season_number', '?')
            season_name = s.get('name', '未命名')
            # 如果名称就是"第 X 季"，就不重复显示
            if season_name == f"第 {season_num} 季":
                result_lines.append(f"  🔹 第 {season_num} 季")
            else:
                result_lines.append(f"  🔹 第 {season_num} 季｜{season_name}")
        
        result_lines.extend([
            "",
            "💡 提示：回复季数数字即可订阅（0 退出）",
            "━━━━━━━━━━━━━━━━━━",
        ])
        result_text = "\n".join(result_lines)
        await event.send(event.plain_result(result_text))

        # 更新用户状态
        await self._set_user_state(
            user_id,
            {
                "selected_movie": movie,
                "seasons": seasons,
                "waiting_for": "season",
            },
        )

        controller.keep(timeout=60, reset_timeout=True)

    async def _process_season_selection(
        self,
        event: AstrMessageEvent,
        controller: SessionController,
        user_id: str,
        user_input: str,
    ) -> None:
        """
        处理季度选择

        Args:
            event: 消息事件
            controller: 会话控制器
            user_id: 用户ID
            user_input: 用户输入
        """
        try:
            season_number = int(user_input)
        except ValueError:
            await event.send(event.plain_result("⚠️ 请输入有效的季数。"))
            controller.keep(timeout=60, reset_timeout=True)
            return

        # 用户取消
        if season_number == 0:
            await event.send(event.plain_result("❌ 已取消操作。"))
            controller.stop()
            return

        # 获取用户状态
        state = await self._get_user_state(user_id)
        selected_movie = state.get("selected_movie", {})
        seasons = state.get("seasons", [])

        if not selected_movie or not seasons:
            await event.send(event.plain_result("❌ 会话已过期，请重新搜索。"))
            controller.stop()
            return

        # 验证季度有效性
        valid_season = any(s.get("season_number") == season_number for s in seasons)
        if not valid_season:
            await event.send(event.plain_result("⚠️ 无效的季数，请从列表中选择。"))
            controller.keep(timeout=60, reset_timeout=True)
            return

        # 执行订阅
        logger.info(f"用户 {user_id} 订阅季度: {selected_movie.get('title')} 第{season_number}季")
        try:
            success = await self.api.subscribe_series(selected_movie, season_number)
        except Exception as e:
            logger.error(f"订阅电视剧失败: {e}")
            await event.send(event.plain_result("❌ 订阅服务暂时不可用，请稍后重试。"))
            controller.stop()
            return

        if success:
            result_text = (
                "✅ 订阅成功！\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"📺 类型：电视剧\n"
                f"🎬 片名：{selected_movie.get('title')}"
            )
            year = selected_movie.get('year')
            if year:
                result_text += f" ({year})"
            result_text += f"\n📌 季度：第 {season_number} 季\n"
            result_text += "━━━━━━━━━━━━━━━━━━"
        else:
            result_text = "❌ 订阅失败，请检查 MoviePilot 服务状态或稍后重试。"

        await event.send(event.plain_result(result_text))
        controller.stop()

    async def _subscribe_movie(
        self,
        event: AstrMessageEvent,
        controller: SessionController,
        movie: dict[str, Any],
    ) -> None:
        """
        订阅电影

        Args:
            event: 消息事件
            controller: 会话控制器
            movie: 电影信息
        """
        try:
            success = await self.api.subscribe_movie(movie)
        except Exception as e:
            logger.error(f"订阅电影失败: {e}")
            await event.send(event.plain_result("❌ 订阅服务暂时不可用，请稍后重试。"))
            controller.stop()
            return

        if success:
            result_text = (
                "✅ 订阅成功！\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "📺 类型：电影\n"
                f"🎬 片名：{movie.get('title')}"
            )
            year = movie.get('year')
            if year:
                result_text += f" ({year})"
            result_text += "\n━━━━━━━━━━━━━━━━━━"
        else:
            result_text = "❌ 订阅失败，请检查 MoviePilot 服务状态或稍后重试。"

        await event.send(event.plain_result(result_text))
        controller.stop()

    async def _get_user_state(self, user_id: str) -> dict[str, Any]:
        """获取用户状态（线程安全）"""
        async with self.state_lock:
            return self.state.get(user_id, {}).copy()

    async def _set_user_state(self, user_id: str, state: dict[str, Any]) -> None:
        """设置用户状态（线程安全）"""
        async with self.state_lock:
            self.state[user_id] = state

    async def _clear_user_state(self, user_id: str) -> None:
        """清除用户状态（线程安全）"""
        async with self.state_lock:
            self.state.pop(user_id, None)

    @filter.command("download")
    async def show_download_progress(self, event: AstrMessageEvent):
        """查看下载进度"""
        # 检查 API 配置
        valid, error_msg = self.api.validate_config()
        if not valid:
            yield event.plain_result(f"⚠️ 插件配置错误：{error_msg}\n请联系管理员检查配置。")
            return

        try:
            progress_data = await self.api.get_download_progress()
        except Exception as e:
            logger.error(f"获取下载进度异常: {e}")
            yield event.plain_result("❌ 获取下载进度失败，请稍后重试。")
            return

        if progress_data is None:
            yield event.plain_result("❌ 无法连接到 MoviePilot 服务，请检查配置。")
            return

        if len(progress_data) == 0:
            yield event.plain_result("📭 当前没有正在下载的任务。")
            return

        # 格式化下载进度
        result_lines = [f"📥 当前下载任务 ({len(progress_data)} 个)\n" + "=" * 30]

        for task in progress_data:
            media = task.get("media", {})
            title = media.get("title") or task.get("title", "未知")
            season = media.get("season", "")
            episode = media.get("episode", "")
            progress = task.get("progress", 0)
            state = task.get("state", "unknown")
            speed = task.get("speed", "")

            # 进度条
            bar_length = 20
            filled = int(bar_length * progress / 100)
            bar = "█" * filled + "░" * (bar_length - filled)

            # 状态图标
            state_icon = {
                "downloading": "⬇️",
                "seeding": "✅",
                "paused": "⏸️",
                "error": "❌",
                "unknown": "❓",
            }.get(state.lower(), "❓")

            # 格式化任务信息
            task_line = f"{state_icon} {title}"
            if season:
                task_line += f" {season}"
            if episode:
                task_line += f" {episode}"

            result_lines.append(f"\n{task_line}")
            result_lines.append(f"   [{bar}] {progress:.1f}%")
            if speed:
                result_lines.append(f"   💨 {speed}")

        result_lines.append("\n" + "=" * 30)
        yield event.plain_result("\n".join(result_lines))

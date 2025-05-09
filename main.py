from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import time
import astrbot.api.message_components as Comp
from astrbot.core.utils.session_waiter import (
    session_waiter,
    SessionController,
)
from .api import MoviepilotApi

@register("MoviepilotSubscribe", "4Nest", "MoviepilotQQ机器人订阅 插件", "1.1.0", "https://github.com/4Nest/astrbot_plugin_mp_sub")
class MyPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.api = MoviepilotApi(config)  # 将 api 定义为实例属性
        self.state = {}  # 初始化状态管理字典
        print(self.config)

    @filter.command("sub")
    async def sub(self, event: AstrMessageEvent, message: str):
        '''订阅影片'''
        movies = await self.api.search_media_info(message)  # 使用 self.api 访问实例属性
        if movies:
            movie_list = "\n".join([f"{i + 1}. {movie['title']} ({movie['year']})" for i, movie in enumerate(movies)])
            print(movie_list)
            media_list = "\n查询到的影片如下\n请直接回复序号进行订阅（回复0退出选择）：\n" + movie_list
            yield event.plain_result(media_list)
            
            # 使用会话控制器等待用户回复
            @session_waiter(timeout=60, record_history_chains=False)
            async def movie_selection_waiter(controller: SessionController, event: AstrMessageEvent):
                try:
                    user_input = event.message_str.strip()
                    user_id = event.get_sender_id()
                    
                    # 检查用户是否在等待选择季度
                    user_state = self.state.get(user_id, {})
                    if user_state.get("waiting_for") == "season":
                        # 用户正在选择季度
                        try:
                            season_number = int(user_input)
                            selected_movie = user_state["selected_movie"]
                            seasons = user_state["seasons"]
                            
                            # 验证季度是否有效
                            valid_season = False
                            for season in seasons:
                                if season['season_number'] == season_number:
                                    valid_season = True
                                    break
                            
                            if valid_season:
                                # 订阅电视剧的指定季度
                                success = await self.api.subscribe_series(selected_movie, season_number)
                                message_result = event.make_result()
                                if success:
                                    message_result.chain = [Comp.Plain(f"\n订阅类型：{selected_movie['type']}\n订阅影片：{selected_movie['title']} ({selected_movie['year']})\n订阅第 {season_number} 季成功！")]
                                else:
                                    message_result.chain = [Comp.Plain("订阅失败。")]
                                await event.send(message_result)
                                # 清除状态
                                self.state.pop(user_id, None)
                                controller.stop()
                            else:
                                message_result = event.make_result()
                                message_result.chain = [Comp.Plain("无效的季数，请重新输入。")]
                                await event.send(message_result)
                                controller.keep(timeout=60, reset_timeout=True)
                        except ValueError:
                            message_result = event.make_result()
                            message_result.chain = [Comp.Plain("请输入一个有效的季数。")]
                            await event.send(message_result)
                            controller.keep(timeout=60, reset_timeout=True)
                        return
                    
                    # 处理电影选择
                    try:
                        index = int(user_input) - 1
                        
                        if index == -1:  # 用户输入0
                            message_result = event.make_result()
                            message_result.chain = [Comp.Plain("操作已取消。")]
                            await event.send(message_result)
                            controller.stop()
                            return
                            
                        if 0 <= index < len(movies):
                            selected_movie = movies[index]
                            if selected_movie['type'] == "电视剧":
                                # 如果是电视剧，获取所有季数
                                seasons = await self.api.list_all_seasons(selected_movie['tmdb_id'])
                                if seasons:
                                    season_list = "\n".join(
                                        [f"第 {season['season_number']} 季 {season['name']}" for season in seasons])
                                    season_list = "\n查询到的季如下\n请直接回复季数进行选择：\n" + season_list
                                    
                                    message_result = event.make_result()
                                    message_result.chain = [Comp.Plain(season_list)]
                                    await event.send(message_result)
                                    
                                    # 继续等待用户选择季数
                                    controller.keep(timeout=60, reset_timeout=True)
                                    
                                    # 更新状态
                                    self.state[user_id] = {
                                        "selected_movie": selected_movie,
                                        "seasons": seasons,
                                        "waiting_for": "season"
                                    }
                                else:
                                    message_result = event.make_result()
                                    message_result.chain = [Comp.Plain("没有找到可用的季数。")]
                                    await event.send(message_result)
                                    controller.stop()
                            else:
                                # 如果是电影，直接订阅
                                success = await self.api.subscribe_movie(selected_movie)
                                message_result = event.make_result()
                                if success:
                                    message_result.chain = [Comp.Plain(f"\n订阅类型：{selected_movie['type']}\n订阅影片：{selected_movie['title']} ({selected_movie['year']})\n订阅成功！")]
                                else:
                                    message_result.chain = [Comp.Plain("订阅失败。")]
                                await event.send(message_result)
                                controller.stop()
                        else:
                            message_result = event.make_result()
                            message_result.chain = [Comp.Plain("无效的序号，请重新输入。")]
                            await event.send(message_result)
                            controller.keep(timeout=60, reset_timeout=True)
                    except ValueError:
                        message_result = event.make_result()
                        message_result.chain = [Comp.Plain("请输入一个数字。")]
                        await event.send(message_result)
                        controller.keep(timeout=60, reset_timeout=True)
                except Exception as e:
                    logger.error(f"处理用户输入时出错: {e}")
                    message_result = event.make_result()
                    message_result.chain = [Comp.Plain(f"处理输入时出错: {str(e)}")]
                    await event.send(message_result)
                    controller.stop()
            
            try:
                await movie_selection_waiter(event)
            except Exception as e:
                logger.error(f"Movie selection error: {e}")
                yield event.plain_result(f"发生错误：{str(e)}")
            finally:
                event.stop_event()
        else:
            yield event.plain_result("没有查询到影片，请检查名字。")

    @filter.command("download")
    async def progress(self, event: AstrMessageEvent):
        '''查看下载'''
        progress_data = await self.api.get_download_progress()
        if progress_data is not None:  # 如果成功获取到数据
            if len(progress_data) == 0:  # 如果没有正在下载的任务
                yield event.plain_result("当前没有正在下载的任务。")
                return
            
            # 格式化下载进度信息
            progress_list = []
            for task in progress_data:
                media = task.get('media', {})
                title = media.get('title', task.get('title', '未知'))
                season = media.get('season', '')
                episode = media.get('episode', '')
                progress = round(task.get('progress', 0), 2)  # 保留两位小数
                
                # 按照要求格式化：title season episode：progress
                formatted_info = f"{title} {season} {episode}：{progress}%"
                progress_list.append(formatted_info)
                
            result = "\n".join(progress_list)
            yield event.plain_result(result)
        else:
            yield event.plain_result("获取下载进度失败，请稍后重试。")

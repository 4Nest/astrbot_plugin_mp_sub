from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from .api import MoviepilotApi

@register("MoviepilotSubscribe", "4Nest", "MoviepilotQQ机器人订阅 插件", "1.0.0", "https://github.com/4Nest/astrbot_plugin_mp_sub")
class MyPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.api = MoviepilotApi(config)  # 将 api 定义为实例属性
        self.state = {}  # 初始化状态管理字典
        print(self.config)

    @filter.command("sub")
    async def sub(self, event: AstrMessageEvent, message: str):
        movies = await self.api.search_media_info(message)  # 使用 self.api 访问实例属性
        if movies:
            movie_list = "\n".join([f"{i + 1}. {movie['title']} ({movie['year']})" for i, movie in enumerate(movies)])
            print(movie_list)
            media_list = "\n查询到的影片如下\n@机器人 /select 0 退出选择\n请@机器人 /select 序号 进行订阅：\n" + movie_list
            yield event.plain_result(media_list)
            user_id = event.get_sender_id()  # 获取用户ID
            self.state[user_id] = {"movies": movies}  # 保存用户状态
        else:
            yield event.plain_result("没有查询到影片，请检查名字。")

    @filter.command("select")
    async def select(self, event: AstrMessageEvent, message: str):
        user_id = event.get_sender_id()  # 获取用户ID
        user_state = self.state.get(user_id)
        if user_state and "movies" in user_state:
            try:
                index = int(message) - 1
                if index == -1:  # 用户输入0
                    yield event.plain_result("操作已取消。")
                    del self.state[user_id]  # 清除用户状态
                    return
                if 0 <= index < len(user_state["movies"]):
                    selected_movie = user_state["movies"][index]
                    if selected_movie['type'] == "电视剧":
                        # 如果是电视剧，获取所有季数
                        seasons = await self.api.list_all_seasons(selected_movie['tmdb_id'])
                        if seasons:
                            season_list = "\n".join(
                                [f"第 {season['season_number']} 季 {season['name']}" for season in seasons])
                            season_list = "\n查询到的季如下\n请@机器人 /season 序号 进行选择：\n请选择季数：\n" + season_list
                            yield event.plain_result(season_list)
                            user_state["selected_movie"] = selected_movie
                            user_state["seasons"] = seasons
                        else:
                            yield event.plain_result("没有找到可用的季数。")
                    else:
                        # 如果是电影，直接订阅
                        success = await self.api.subscribe_movie(selected_movie)
                        if success:
                            yield event.plain_result(f"\n订阅类型：{selected_movie['type']}\n订阅影片：{selected_movie['title']} ({selected_movie['year']})\n订阅成功！")
                            del self.state[user_id]  # 清除用户状态
                        else:
                            yield event.plain_result("订阅失败。")
                else:
                    yield event.plain_result("无效的序号，请重新输入。")
            except ValueError:
                yield event.plain_result("请输入一个数字。")
        else:
            yield event.plain_result("请先使用 /sub 命令搜索影片。")

    @filter.command("season")
    async def season(self, event: AstrMessageEvent, message: str):
        user_id = event.get_sender_id()  # 获取用户ID
        user_state = self.state.get(user_id)
        if user_state and "selected_movie" in user_state and "seasons" in user_state:
            try:
                season_number = int(message)
                seasons = user_state["seasons"]
                selected_movie = user_state["selected_movie"]
                for season in seasons:
                    if season['season_number'] == season_number:
                        success = await self.api.subscribe_series(selected_movie, season_number)
                        if success:
                            yield event.plain_result(f"\n订阅类型：{selected_movie['type']}\n订阅影片：{selected_movie['title']} ({selected_movie['year']})\n订阅第 {season_number} 季成功！")
                            del self.state[user_id]  # 清除用户状态
                        else:
                            yield event.plain_result("订阅失败。")
                        return
                yield event.plain_result("无效的季数，请重新输入。")
            except ValueError:
                yield event.plain_result("请输入一个数字。")
        else:
            yield event.plain_result("请先使用 /sub 和 /select 命令选择电视剧和季数。")

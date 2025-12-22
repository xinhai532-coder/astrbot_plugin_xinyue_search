import asyncio
import aiohttp
import yaml
import os
import time
from urllib.parse import urlparse, parse_qs
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from typing import Dict, List
from collections import defaultdict


class RateLimiter:
    """请求限流器 - 滑动窗口算法"""
    
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        """
        初始化限流器
        :param max_requests: 窗口期内最大请求数
        :param window_seconds: 窗口期时长（秒）
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: Dict[str, List[float]] = defaultdict(list)
    
    def is_allowed(self, user_id: str) -> bool:
        """
        检查用户是否允许请求
        :param user_id: 用户标识
        :return: True 允许，False 拒绝
        """
        now = time.time()
        # 清理过期的请求记录
        self.requests[user_id] = [
            t for t in self.requests[user_id] 
            if now - t < self.window_seconds
        ]
        # 检查是否超过限制
        if len(self.requests[user_id]) >= self.max_requests:
            return False
        # 记录本次请求
        self.requests[user_id].append(now)
        return True
    
    def get_wait_time(self, user_id: str) -> int:
        """获取用户需要等待的秒数"""
        if not self.requests[user_id]:
            return 0
        oldest = min(self.requests[user_id])
        wait = self.window_seconds - (time.time() - oldest)
        return max(0, int(wait))


@register("astrbot_plugin_xinyue_search", "阿立", "心悦搜索机器人插件", "1.3.6")
class XinyueSearchBotPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        # 如果config为None，使用空字典
        if config is None:
            config = {}

        # 适配AstrBot配置模式，将外部配置键映射到内部配置
        self.config = {
            # API基础配置 - 适配外部配置键
            'base_url': config.get('api_url', 'https://youdomain.com').rstrip('/'),
            'api_key': config.get('api_key', ''),

            # 搜索配置
            'max_retries': config.get('max_retries', 3),
            'search_timeout': config.get('timeout', 10),
            'transfer_timeout': config.get('transfer_timeout', 30),
            'results_per_page': config.get('max_results', 5),

            # 转存功能配置
            'enable_transfer': config.get('enable_transfer', True),
            'transfer_delay': config.get('transfer_delay', 1),

            # 日志配置
            'log_level': 'INFO',
            'log_format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',

            # 搜索类型配置（可自定义）
            'search_types': {
                '夸克': 0,
                '百度': 2,
                'UC': 3,
                '迅雷': 4
            },

            # 指令配置（可自定义）
            'search_commands': {
                '搜': '夸克',
                '百度': '百度',
                'uc': 'UC',
                'UC': 'UC',
                '迅雷': '迅雷'
            },

            # 响应消息配置
            'messages': {
                'searching': '全网搜索中，请稍等片刻……',
                'no_results': '未找到相关资源',
                'search_error': '搜索过程中发生错误，请稍后重试',
                'transfer_success': '✅ 转存成功！\n📁 资源标题：{0}\n🔗 分享链接：{1}',
                'transfer_disabled': '❌ 转存功能未启用',
                'api_key_required': '❌ 需要配置API密钥才能使用转存功能',
                'baidu_format_error': '指令格式错误，请使用：百度资源名称',
                'baidu_example': '请输入要搜索的资源名称，例如：百度{0}',
                'uc_format_error': '指令格式错误，请使用：uc资源名称',
                'uc_example': '请输入要搜索的资源名称，例如：uc{0}',
                'uc_upper_format_error': '指令格式错误，请使用：UC资源名称',
                'uc_upper_example': '请输入要搜索的资源名称，例如：UC{0}',
                'xunlei_format_error': '指令格式错误，请使用：迅雷资源名称',
                'xunlei_example': '请输入要搜索的资源名称，例如：迅雷{0}',
                'last_page': '已经是最后一页了',
                'no_search_session': '没有找到搜索会话，请先进行搜索',
                'next_page_error': '获取下一页失败，请稍后重试',
                'first_page': '已经是第一页了',
                'previous_page_error': '获取上一页失败，请稍后重试',
                'invalid_transfer_command': '❌ 无效的转存指令格式',
                'no_search_for_transfer': '❌ 没有找到可转存的搜索结果',
                'search_expired': '❌ 搜索会话已过期，请重新搜索',
                'invalid_resource_index': '❌ 无效的资源序号，请输入1-{0}之间的数字',
                'no_valid_link': '❌ 该资源没有有效的分享链接',
                'only_quark_support': '❌ 目前仅支持夸克网盘转存',
                'transferring': '正在转存《{0}》，请稍候...',
                'transfer_error': '❌ 转存失败，请稍后重试',
                'empty_keyword': '❌ 搜索关键词不能为空',
                'keyword_too_long': '❌ 搜索关键词过长（超过100字符）',
                'parse_search_failed': '❌ 解析搜索结果失败',
                'too_many_requests': '❌ 请求过于频繁，请稍后再试',
                'search_service_unavailable': '❌ 搜索服务异常，HTTP状态码：{0}',
                'search_timeout': '❌ 搜索超时，请稍后重试',
                'network_error': '❌ 网络错误，请检查网络连接',
                'unknown_search_error': '❌ 搜索发生未知错误：{0}',
                'search_service_unavailable_temporarily': '服务暂时不可用',
                'search_failed': '搜索失败',
                'full_network_search_results': '🔍 全网搜索结果：{0}\n\n',
                'api_key_not_configured': '❌ API密钥未配置，请联系管理员配置api_key',
                'search_command_format_error': '指令格式错误，请使用：{0}资源名称',
                'search_example_format': '请输入要搜索的资源名称，例如：{0}{1}',
                'search_unknown_error': '{0}搜索过程中发生未知错误，请稍后重试',
                'keyword_empty_error': '搜索关键词不能为空',
                'keyword_too_long_error': '搜索关键词过长，请缩短后重试',
                'search_no_results_format': '未找到与\'{0}\'相关的资源',
                'search_too_many_requests': '请求过于频繁，请稍后再试',
                'search_service_unavailable_format': '搜索服务暂时不可用，HTTP状态码: {0}',
                'search_timeout_error': '搜索超时，请稍后重试',
                'network_connection_error': '网络连接异常，请检查网络后重试',
                'search_service_temporarily_unavailable': '搜索服务暂时不可用，请稍后重试',
                'search_unknown_error_format': '搜索过程中发生未知错误: {0}',
                'search_results_parse_failed': '搜索结果解析失败，请稍后重试',
                'no_previous_search': '您还没有进行搜索，请先使用搜索指令，例如：\n搜电影名\n百度电影名\nuc电影名\n迅雷电影名',
                'next_page_unknown_error': '处理下一页指令时发生未知错误，请稍后重试',
                'previous_page_unknown_error': '处理上一页指令时发生未知错误，请稍后重试',
                'transfer_failed': '❌ 转存失败：{0}',
                'transfer_service_error': '❌ 转存服务异常，HTTP状态码：{0}',
                'transfer_timeout': '❌ 转存超时，请稍后重试',
                'transfer_process_error': '❌ 转存过程中发生错误：{0}',
                'show_config_error': '显示配置信息时发生错误',
                'unknown_error': '未知错误',
                'invalid_page_number': '❌ 页码超出范围，总页数为{0}',
                'format_search_error': '❌ 格式化搜索结果失败',
                'resource_title_default': '未知标题',
                'resource_link_format': '{0}. {1}\n链接: `{2}`',
                'resource_title_format': '{0}. {1}',
                'quark_transfer_prompt': '💡 回复"转存{0}"可转存到夸克网盘',
                'search_results_header': '🔍 共找到 {0} 个相关资源：',
                'search_results_separator': '─' * 14,
                'search_results_footer': '链接有效期5分钟，过期请重搜',
                'search_results_separator_footer': '─' * 14,
                'search_results_website_promo': '更多资源请上 {0}',
                'search_results_page_info': '📄 第 {0}/{1} 页',
                'search_results_navigation': '💡 回复"上/下"或"0/1"翻页',
                'no_resources_found': '未找到相关资源: {0}\n\n💡 提示：请尝试其他网盘搜索'
            }
        }

        # 从配置中提取常用参数
        self.base_url = self.config['base_url']
        self.api_key = self.config['api_key']
        self.max_retries = self.config['max_retries']
        self.search_timeout = self.config['search_timeout']
        self.transfer_timeout = self.config['transfer_timeout']
        self.results_per_page = self.config['results_per_page']
        self.enable_transfer = self.config['enable_transfer']
        self.enable_pagination = config.get('enable_pagination', True)  # 是否启用分页功能
        self.transfer_delay = self.config['transfer_delay']
        self.search_types = self.config['search_types']
        self.search_commands = self.config['search_commands']
        self.messages = self.config['messages']

        # 初始化限流器
        self.rate_limiter = RateLimiter(max_requests=10, window_seconds=60)
        
        # 用户会话状态存储，用于分页功能
        self.user_sessions: Dict[str, Dict] = {}
        
        # Cookie缓存（避免重复请求）
        self._cookie_cache: Dict[str, tuple] = {}  # {pan_type: (cookie_value, timestamp)}

    def _load_config(self) -> dict:
        """加载配置文件"""
        config_file = os.path.join(os.path.dirname(__file__), 'config.yaml')
        default_config = {
            'base_url': 'https://youdomain.com',
            'api_key': '',
            'max_retries': 3,
            'search_timeout': 10,
            'transfer_timeout': 30,
            'results_per_page': 5,
            'enable_transfer': True,
            'transfer_delay': 1,
            'log_level': 'INFO',
            # 默认消息配置
            'messages': {
                'searching': '全网搜索中，请稍等片刻……',
                'no_results': '未找到相关资源',
                'search_error': '搜索过程中发生错误，请稍后重试',
                'transfer_success': '✅ 转存成功！\n📁 资源标题：{0}\n🔗 分享链接：{1}',
                'transfer_disabled': '❌ 转存功能未启用',
                'api_key_required': '❌ 需要配置API密钥才能使用转存功能',
                'baidu_format_error': '指令格式错误，请使用：百度资源名称',
                'baidu_example': '请输入要搜索的资源名称，例如：百度{0}',
                'uc_format_error': '指令格式错误，请使用：uc资源名称',
                'uc_example': '请输入要搜索的资源名称，例如：uc{0}',
                'uc_upper_format_error': '指令格式错误，请使用：UC资源名称',
                'uc_upper_example': '请输入要搜索的资源名称，例如：UC{0}',
                'xunlei_format_error': '指令格式错误，请使用：迅雷资源名称',
                'xunlei_example': '请输入要搜索的资源名称，例如：迅雷{0}',
                'last_page': '已经是最后一页了',
                'no_search_session': '没有找到搜索会话，请先进行搜索',
                'next_page_error': '获取下一页失败，请稍后重试',
                'first_page': '已经是第一页了',
                'previous_page_error': '获取上一页失败，请稍后重试',
                'invalid_transfer_command': '❌ 无效的转存指令格式',
                'no_search_for_transfer': '❌ 没有找到可转存的搜索结果',
                'search_expired': '❌ 搜索会话已过期，请重新搜索',
                'invalid_resource_index': '❌ 无效的资源序号，请输入1-{0}之间的数字',
                'no_valid_link': '❌ 该资源没有有效的分享链接',
                'only_quark_support': '❌ 目前仅支持夸克网盘转存',
                'transferring': '正在转存《{0}》，请稍候...',
                'transfer_error': '❌ 转存失败，请稍后重试',
                'empty_keyword': '❌ 搜索关键词不能为空',
                'keyword_too_long': '❌ 搜索关键词过长（超过100字符）',
                'parse_search_failed': '❌ 解析搜索结果失败',
                'too_many_requests': '❌ 请求过于频繁，请稍后再试',
                'search_service_unavailable': '❌ 搜索服务异常，HTTP状态码：{0}',
                'search_timeout': '❌ 搜索超时，请稍后重试',
                'network_error': '❌ 网络错误，请检查网络连接',
                'unknown_search_error': '❌ 搜索发生未知错误：{0}',
                'search_service_unavailable_temporarily': '服务暂时不可用',
                'search_failed': '搜索失败',
                'full_network_search_results': '🔍 全网搜索结果：{0}\n\n',
                'api_key_not_configured': '❌ API密钥未配置，请联系管理员配置api_key',
                'search_command_format_error': '指令格式错误，请使用：{0}资源名称',
                'search_example_format': '请输入要搜索的资源名称，例如：{0}{1}',
                'search_unknown_error': '{0}搜索过程中发生未知错误，请稍后重试',
                'keyword_empty_error': '搜索关键词不能为空',
                'keyword_too_long_error': '搜索关键词过长，请缩短后重试',
                'search_no_results_format': '未找到与\'{0}\'相关的资源',
                'search_too_many_requests': '请求过于频繁，请稍后再试',
                'search_service_unavailable_format': '搜索服务暂时不可用，HTTP状态码: {0}',
                'search_timeout_error': '搜索超时，请稍后重试',
                'network_connection_error': '网络连接异常，请检查网络后重试',
                'search_service_temporarily_unavailable': '搜索服务暂时不可用，请稍后重试',
                'search_unknown_error_format': '搜索过程中发生未知错误: {0}',
                'search_results_parse_failed': '搜索结果解析失败，请稍后重试',
                'no_previous_search': '您还没有进行搜索，请先使用搜索指令，例如：\n搜电影名\n百度电影名\nuc电影名\n迅雷电影名',
                'next_page_unknown_error': '处理下一页指令时发生未知错误，请稍后重试',
                'previous_page_unknown_error': '处理上一页指令时发生未知错误，请稍后重试',
                'transfer_failed': '❌ 转存失败：{0}',
                'transfer_service_error': '❌ 转存服务异常，HTTP状态码：{0}',
                'transfer_timeout': '❌ 转存超时，请稍后重试',
                'transfer_process_error': '❌ 转存过程中发生错误：{0}',
                'show_config_error': '显示配置信息时发生错误',
                'unknown_error': '未知错误',
                'invalid_page_number': '❌ 页码超出范围，总页数为{0}',
                'format_search_error': '❌ 格式化搜索结果失败',
                'resource_title_default': '未知标题',
                'resource_link_format': '{0}. {1}\n链接: `{2}`',
                'resource_title_format': '{0}. {1}',
                'quark_transfer_prompt': '💡 回复"转存{0}"可转存到夸克网盘',
                'search_results_header': '🔍 共找到 {0} 个相关资源：',
                'search_results_separator': '─' * 20,
                'search_results_footer': '链接有效期5分钟，过期请重搜',
                'search_results_separator_footer': '─' * 20,
                'search_results_page_info': '📄 第 {0}/{1} 页',
                'search_results_navigation': '💡 回复"上/下"或"0/1"翻页',
                'no_resources_found': '未找到相关资源: {0}\n\n💡 提示：请尝试其他网盘搜索'
            }
        }

        try:
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                    if config:
                        # 合并顶层配置
                        for key, value in config.items():
                            if key == 'messages' and isinstance(value, dict):
                                # 特殊处理messages字典，进行深度合并
                                if 'messages' in default_config:
                                    default_config['messages'].update(value)
                                else:
                                    default_config['messages'] = value
                            else:
                                default_config[key] = value
                        logger.info("配置文件加载成功")
                    else:
                        logger.warning("配置文件为空，使用默认配置")
            else:
                logger.warning("配置文件不存在，使用默认配置")
                # 创建默认配置文件
                with open(config_file, 'w', encoding='utf-8') as f:
                    yaml.dump(default_config, f,
                              default_flow_style=False, allow_unicode=True)
                logger.info("已创建默认配置文件")
        except Exception as e:
            logger.error(f"加载配置文件失败: {str(e)}，使用默认配置")

        return default_config

    async def initialize(self):
        """插件初始化方法"""
        logger.info("心悦搜索插件已加载")
        logger.info(f"基础URL: {self.base_url}")
        logger.info(f"转存功能: {'已启用' if self.enable_transfer else '已禁用'}")
        if self.api_key:
            logger.info("API密钥: 已配置")
        else:
            logger.warning("API密钥: 未配置，转存功能可能无法正常工作")

    def _get_user_session_key(self, event: AstrMessageEvent) -> str:
        """获取用户会话的唯一标识符 - 按用户隔离会话"""
        try:
            # 获取用户ID
            user_id = None
            if hasattr(event, 'message_obj') and event.message_obj:
                if hasattr(event.message_obj, 'sender') and event.message_obj.sender:
                    user_id = event.message_obj.sender.user_id
            
            # 如果成功获取用户ID，组合用户ID和群ID作为会话key
            if user_id:
                # 使用 用户ID@群ID 的格式，确保每个用户在每个群都有独立会话
                group_id = event.message_obj.group_id if hasattr(event.message_obj, 'group_id') else event.session_id
                return f"{user_id}@{group_id}"
            
            # fallback: 如果无法获取用户ID，使用原来的逻辑
            logger.warning("无法获取用户ID，使用unified_msg_origin作为会话key")
            return event.unified_msg_origin
        except Exception as e:
            logger.error(f"获取用户会话key时发生错误: {str(e)}")
            return event.unified_msg_origin

    def _format_reply_with_mention(self, event: AstrMessageEvent, message: str) -> str:
        """格式化回复消息，添加@用户（QQ使用CQ码格式）"""
        # 暂时禁用@功能，因为CQ码在某些情况下显示异常
        # 如果需要启用，取消下面的注释
        return message
        
        # try:
        #     # 获取发送者ID
        #     if hasattr(event, 'message_obj') and event.message_obj:
        #         if hasattr(event.message_obj, 'sender') and event.message_obj.sender:
        #             user_id = event.message_obj.sender.user_id
        #             # QQ使用CQ码格式：[CQ:at,qq=用户ID]
        #             return f"[CQ:at,qq={user_id}] {message}"
        #     
        #     # 如果无法获取用户ID，返回原消息
        #     return message
        # except Exception as e:
        #     logger.error(f"格式化@用户消息时发生错误: {str(e)}")
        #     return message

    def _get_user_id_for_rate_limit(self, event: AstrMessageEvent) -> str:
        """获取用户ID用于限流 - 按用户限流而不是按群限流"""
        try:
            # 获取用户ID
            if hasattr(event, 'message_obj') and event.message_obj:
                if hasattr(event.message_obj, 'sender') and event.message_obj.sender:
                    return str(event.message_obj.sender.user_id)
            
            # fallback: 使用unified_msg_origin
            logger.warning("无法获取用户ID用于限流，使用unified_msg_origin")
            return event.unified_msg_origin
        except Exception as e:
            logger.error(f"获取用户ID用于限流时发生错误: {str(e)}")
            return event.unified_msg_origin

    @filter.regex(r"^搜(\s+|(?![索]).)\S+")
    async def search_resource(self, event: AstrMessageEvent, *args, **kwargs):
        """搜索资源指令：搜资源名称"""
        try:
            # 获取用户会话key（用于会话隔离）
            user_session_key = self._get_user_session_key(event)
            
            # 获取用户ID（用于限流）
            user_id_for_limit = self._get_user_id_for_rate_limit(event)
            
            # 限流检查 - 按用户限流
            if not self.rate_limiter.is_allowed(user_id_for_limit):
                wait_time = self.rate_limiter.get_wait_time(user_id_for_limit)
                yield event.plain_result(f"❌ 请求过于频繁，请{wait_time}秒后再试")
                return

            # 精确提取"搜"后的关键词（支持带空格和不带空格的格式）
            message = event.message_str.strip()
            if message.startswith("搜"):
                keyword = message[1:].strip()  # 去掉"搜"前缀并去除空格
            else:
                yield event.plain_result(self.messages['search_command_format_error'].format(self.search_commands['搜']))
                return

            if not keyword:
                yield event.plain_result(self.messages['search_example_format'].format(self.search_commands['搜'], '电影名'))
                return

            # 立即回复用户，告知正在搜索
            yield event.plain_result(self._format_reply_with_mention(event, self.messages['searching']))

            # 默认使用夸克网盘搜索
            result = await self._search_resources(user_session_key, keyword, is_full_network=False, pan_type=0)
            yield event.plain_result(self._format_reply_with_mention(event, result))
        except Exception as e:
            logger.error(f"搜索资源时发生错误: {str(e)}")
            yield event.plain_result(self.messages['search_error'])

    @filter.regex(r"^找\s*\S+")
    async def local_search(self, event: AstrMessageEvent):
        """本地搜索指令：找资源名称（只查询本地数据库）"""
        try:
            # 获取用户会话key（用于会话隔离）
            user_session_key = self._get_user_session_key(event)
            
            # 获取用户ID（用于限流）
            user_id_for_limit = self._get_user_id_for_rate_limit(event)
            
            # 限流检查 - 按用户限流
            if not self.rate_limiter.is_allowed(user_id_for_limit):
                wait_time = self.rate_limiter.get_wait_time(user_id_for_limit)
                yield event.plain_result(f"❌ 请求过于频繁，请{wait_time}秒后再试")
                return

            # 提取"找"后的关键词
            message = event.message_str.strip()
            if message.startswith("找"):
                keyword = message[1:].strip()
            else:
                yield event.plain_result("指令格式错误，请使用：找资源名称")
                return

            if not keyword:
                yield event.plain_result("请输入要查找的资源名称，例如：找电影名")
                return

            # 立即回复用户
            yield event.plain_result(self._format_reply_with_mention(event, "🔍 正在本地数据库查找，请稍候..."))

            # 调用本地搜索
            result = await self._local_search(keyword)
            yield event.plain_result(self._format_reply_with_mention(event, result))
            
        except Exception as e:
            logger.error(f"本地搜索时发生错误: {str(e)}")
            yield event.plain_result("❌ 搜索过程中发生错误，请稍后重试")

    @filter.regex(r"^百度\s*\S+")
    async def baidu_search(self, event: AstrMessageEvent):
        """百度搜索指令：百度资源名称"""
        try:
            # 获取用户会话key（用于会话隔离）
            user_session_key = self._get_user_session_key(event)
            
            # 获取用户ID（用于限流）
            user_id_for_limit = self._get_user_id_for_rate_limit(event)
            
            # 限流检查 - 按用户限流
            if not self.rate_limiter.is_allowed(user_id_for_limit):
                wait_time = self.rate_limiter.get_wait_time(user_id_for_limit)
                yield event.plain_result(f"❌ 请求过于频繁，请{wait_time}秒后再试")
                return

            # 精确提取"百度"后的关键词（支持带空格和不带空格的格式）
            message = event.message_str.strip()
            if message.startswith("百度"):
                keyword = message[2:].strip()  # 去掉"百度"前缀并去除空格
            else:
                yield event.plain_result(self.messages['baidu_format_error'])
                return

            if not keyword:
                yield event.plain_result(self.messages['baidu_example'].format('电影名'))
                return

            # 立即回复用户，告知正在搜索
            yield event.plain_result(self._format_reply_with_mention(event, self.messages['searching']))

            # 使用百度网盘搜索
            result = await self._search_resources(user_session_key, keyword, is_full_network=False, pan_type=2)
            yield event.plain_result(self._format_reply_with_mention(event, result))
        except Exception as e:
            logger.error(f"百度搜索时发生错误: {str(e)}")
            yield event.plain_result(self.messages['search_error'])

    @filter.regex(r"^uc\s*\S+")
    async def uc_search_lower(self, event: AstrMessageEvent):
        """UC搜索指令：uc资源名称"""
        try:
            # 获取用户会话key（用于会话隔离）
            user_session_key = self._get_user_session_key(event)
            
            # 获取用户ID（用于限流）
            user_id_for_limit = self._get_user_id_for_rate_limit(event)
            
            # 限流检查 - 按用户限流
            if not self.rate_limiter.is_allowed(user_id_for_limit):
                wait_time = self.rate_limiter.get_wait_time(user_id_for_limit)
                yield event.plain_result(f"❌ 请求过于频繁，请{wait_time}秒后再试")
                return

            # 精确提取"uc"后的关键词（支持带空格和不带空格的格式）
            message = event.message_str.strip()
            if message.startswith("uc"):
                keyword = message[2:].strip()  # 去掉"uc"前缀并去除空格
            else:
                yield event.plain_result(self.messages['uc_format_error'])
                return

            if not keyword:
                yield event.plain_result(self.messages['uc_example'].format('电影名'))
                return

            # 立即回复用户，告知正在搜索
            yield event.plain_result(self._format_reply_with_mention(event, self.messages['searching']))

            # 使用UC网盘搜索
            result = await self._search_resources(user_session_key, keyword, is_full_network=False, pan_type=3)
            yield event.plain_result(self._format_reply_with_mention(event, result))
        except Exception as e:
            logger.error(f"UC搜索时发生错误: {str(e)}")
            yield event.plain_result(self.messages['search_error'])

    @filter.regex(r"^UC\s*\S+")
    async def uc_search_upper(self, event: AstrMessageEvent):
        """UC搜索指令：UC资源名称"""
        try:
            # 获取用户会话key（用于会话隔离）
            user_session_key = self._get_user_session_key(event)
            
            # 获取用户ID（用于限流）
            user_id_for_limit = self._get_user_id_for_rate_limit(event)
            
            # 限流检查 - 按用户限流
            if not self.rate_limiter.is_allowed(user_id_for_limit):
                wait_time = self.rate_limiter.get_wait_time(user_id_for_limit)
                yield event.plain_result(f"❌ 请求过于频繁，请{wait_time}秒后再试")
                return

            # 精确提取"UC"后的关键词（支持带空格和不带空格的格式）
            message = event.message_str.strip()
            if message.startswith("UC"):
                keyword = message[2:].strip()  # 去掉"UC"前缀并去除空格
            else:
                yield event.plain_result(self.messages['uc_upper_format_error'])
                return

            if not keyword:
                yield event.plain_result(self.messages['uc_upper_example'].format('电影名'))
                return

            # 立即回复用户，告知正在搜索
            yield event.plain_result(self._format_reply_with_mention(event, self.messages['searching']))

            # 使用UC网盘搜索
            result = await self._search_resources(user_session_key, keyword, is_full_network=False, pan_type=3)
            yield event.plain_result(self._format_reply_with_mention(event, result))
        except Exception as e:
            logger.error(f"UC搜索时发生错误: {str(e)}")
            yield event.plain_result(self.messages['search_error'])

    @filter.regex(r"^迅雷\s*\S+")
    async def xunlei_search(self, event: AstrMessageEvent):
        """迅雷搜索指令：迅雷资源名称"""
        try:
            # 获取用户会话key（用于会话隔离）
            user_session_key = self._get_user_session_key(event)
            
            # 获取用户ID（用于限流）
            user_id_for_limit = self._get_user_id_for_rate_limit(event)
            
            # 限流检查 - 按用户限流
            if not self.rate_limiter.is_allowed(user_id_for_limit):
                wait_time = self.rate_limiter.get_wait_time(user_id_for_limit)
                yield event.plain_result(f"❌ 请求过于频繁，请{wait_time}秒后再试")
                return

            # 精确提取"迅雷"后的关键词（支持带空格和不带空格的格式）
            message = event.message_str.strip()
            if message.startswith("迅雷"):
                keyword = message[2:].strip()  # 去掉"迅雷"前缀并去除空格
            else:
                yield event.plain_result(self.messages['xunlei_format_error'])
                return

            if not keyword:
                yield event.plain_result(self.messages['xunlei_example'].format('电影名'))
                return

            # 立即回复用户，告知正在搜索
            yield event.plain_result(self._format_reply_with_mention(event, self.messages['searching']))

            # 使用迅雷网盘搜索
            result = await self._search_resources(user_session_key, keyword, is_full_network=False, pan_type=4)
            yield event.plain_result(self._format_reply_with_mention(event, result))
        except Exception as e:
            logger.error(f"迅雷搜索时发生错误: {str(e)}")
            yield event.plain_result(self.messages['search_error'])

    @filter.regex(r"^1$")
    async def next_page(self, event: AstrMessageEvent):
        """处理下一页指令（用户输入'1'）"""
        try:
            # 检查分页功能是否启用
            if not self.enable_pagination:
                return
            
            # 获取用户会话key
            user_session_key = self._get_user_session_key(event)

            # 检查用户是否有未完成的搜索会话
            if user_session_key in self.user_sessions and 'results' in self.user_sessions[user_session_key]:
                session_data = self.user_sessions[user_session_key]
                
                # 检查是否有有效的搜索结果
                results = session_data.get('results')
                if not results:
                    # 没有搜索结果，不响应翻页
                    return
                
                current_page = session_data.get('current_page', 1)
                total_pages = session_data.get('total_pages', 1)
                
                # 如果总页数为0或1，说明没有足够的结果需要翻页
                if total_pages <= 1:
                    return
                
                keyword = session_data.get('keyword', '')
                is_full_network = session_data.get('is_full_network', False)
                pan_type = session_data.get('pan_type', 0)

                # 检查是否还有下一页
                if current_page < total_pages:
                    # 立即回复用户，告知正在翻页
                    yield event.plain_result(self._format_reply_with_mention(event, "⏳ 正在翻页，请稍候..."))
                    
                    # 更新页码
                    next_page = current_page + 1
                    self.user_sessions[user_session_key]['current_page'] = next_page

                    # 格式化并返回下一页结果
                    result = await self._format_search_results(user_session_key, results, keyword, is_full_network, next_page)
                    yield event.plain_result(self._format_reply_with_mention(event, result))
                else:
                    yield event.plain_result(self._format_reply_with_mention(event, self.messages['last_page']))
            # 如果没有搜索会话，不响应（避免误触发）
        except Exception as e:
            logger.error(f"处理下一页指令时发生错误: {str(e)}")
            yield event.plain_result(self.messages['next_page_error'])

    @filter.regex(r"^0$")
    async def previous_page(self, event: AstrMessageEvent):
        """处理上一页指令（用户输入'0'）"""
        try:
            # 检查分页功能是否启用
            if not self.enable_pagination:
                return
            
            # 获取用户会话key
            user_session_key = self._get_user_session_key(event)

            # 检查用户是否有未完成的搜索会话
            if user_session_key in self.user_sessions and 'results' in self.user_sessions[user_session_key]:
                session_data = self.user_sessions[user_session_key]
                
                # 检查是否有有效的搜索结果
                results = session_data.get('results')
                if not results:
                    return
                
                current_page = session_data.get('current_page', 1)
                total_pages = session_data.get('total_pages', 1)
                
                # 如果总页数为0或1，说明没有足够的结果需要翻页
                if total_pages <= 1:
                    return
                
                keyword = session_data.get('keyword', '')
                is_full_network = session_data.get('is_full_network', False)
                pan_type = session_data.get('pan_type', 0)

                # 检查是否还有上一页
                if current_page > 1:
                    # 立即回复用户，告知正在翻页
                    yield event.plain_result(self._format_reply_with_mention(event, "⏳ 正在翻页，请稍候..."))
                    
                    # 更新页码
                    previous_page = current_page - 1
                    self.user_sessions[user_session_key]['current_page'] = previous_page

                    # 格式化并返回上一页结果
                    result = await self._format_search_results(user_session_key, results, keyword, is_full_network, previous_page)
                    yield event.plain_result(self._format_reply_with_mention(event, result))
                else:
                    yield event.plain_result(self._format_reply_with_mention(event, self.messages['first_page']))
            # 如果没有搜索会话，不响应
        except Exception as e:
            logger.error(f"处理上一页指令时发生错误: {str(e)}")
            yield event.plain_result(self.messages['previous_page_error'])

    @filter.regex(r"^下$")
    async def next_page_simple(self, event: AstrMessageEvent):
        """处理简单下一页指令（用户输入'下'）"""
        try:
            # 检查分页功能是否启用
            if not self.enable_pagination:
                return
            
            # 获取用户会话key
            user_session_key = self._get_user_session_key(event)

            # 检查用户是否有未完成的搜索会话
            if user_session_key in self.user_sessions and 'results' in self.user_sessions[user_session_key]:
                session_data = self.user_sessions[user_session_key]
                
                # 检查是否有有效的搜索结果
                results = session_data.get('results')
                if not results:
                    return
                
                current_page = session_data.get('current_page', 1)
                total_pages = session_data.get('total_pages', 1)
                
                # 如果总页数为0或1，说明没有足够的结果需要翻页
                if total_pages <= 1:
                    return
                
                keyword = session_data.get('keyword', '')
                is_full_network = session_data.get('is_full_network', False)
                pan_type = session_data.get('pan_type', 0)

                # 检查是否还有下一页
                if current_page < total_pages:
                    # 立即回复用户，告知正在翻页
                    yield event.plain_result(self._format_reply_with_mention(event, "⏳ 正在翻页，请稍候..."))
                    
                    # 更新页码
                    next_page = current_page + 1
                    self.user_sessions[user_session_key]['current_page'] = next_page

                    # 格式化并返回下一页结果
                    result = await self._format_search_results(user_session_key, results, keyword, is_full_network, next_page)
                    yield event.plain_result(self._format_reply_with_mention(event, result))
                else:
                    yield event.plain_result(self._format_reply_with_mention(event, self.messages['last_page']))
            # 如果没有搜索会话，不响应
        except Exception as e:
            logger.error(f"处理下一页指令时发生错误: {str(e)}")
            yield event.plain_result(self.messages['next_page_error'])

    @filter.regex(r"^上$")
    async def previous_page_simple(self, event: AstrMessageEvent):
        """处理简单上一页指令（用户输入'上'）"""
        try:
            # 检查分页功能是否启用
            if not self.enable_pagination:
                return
            
            # 获取用户会话key
            user_session_key = self._get_user_session_key(event)

            # 检查用户是否有未完成的搜索会话
            if user_session_key in self.user_sessions and 'results' in self.user_sessions[user_session_key]:
                session_data = self.user_sessions[user_session_key]
                
                # 检查是否有有效的搜索结果
                results = session_data.get('results')
                if not results:
                    return
                
                current_page = session_data.get('current_page', 1)
                total_pages = session_data.get('total_pages', 1)
                
                # 如果总页数为0或1，说明没有足够的结果需要翻页
                if total_pages <= 1:
                    return
                
                keyword = session_data.get('keyword', '')
                is_full_network = session_data.get('is_full_network', False)
                pan_type = session_data.get('pan_type', 0)

                # 检查是否还有上一页
                if current_page > 1:
                    # 立即回复用户，告知正在翻页
                    yield event.plain_result(self._format_reply_with_mention(event, "⏳ 正在翻页，请稍候..."))
                    
                    # 更新页码
                    previous_page = current_page - 1
                    self.user_sessions[user_session_key]['current_page'] = previous_page

                    # 格式化并返回上一页结果
                    result = await self._format_search_results(user_session_key, results, keyword, is_full_network, previous_page)
                    yield event.plain_result(self._format_reply_with_mention(event, result))
                else:
                    yield event.plain_result(self._format_reply_with_mention(event, self.messages['first_page']))
            else:
                yield event.plain_result(self.messages['no_search_session'])
        except Exception as e:
            logger.error(f"处理上一页指令时发生错误: {str(e)}")
            yield event.plain_result(self.messages['previous_page_error'])

    @filter.regex(r"^转存(\d+)$")
    async def transfer_resource(self, event: AstrMessageEvent):
        """处理转存指令，例如：转存1"""
        try:
            # 检查转存功能是否启用
            if not self.enable_transfer:
                yield event.plain_result(self.messages['transfer_disabled'])
                return

            # 检查API密钥
            if not self.api_key:
                yield event.plain_result(self.messages['api_key_required'])
                return

            # 获取用户会话key
            user_session_key = self._get_user_session_key(event)

            # 提取要转存的资源编号
            match = re.search(r"转存(\d+)", event.get_message_content())
            if not match:
                yield event.plain_result(self.messages['invalid_transfer_command'])
                return

            resource_index = int(match.group(1))

            # 检查用户是否有未完成的搜索会话
            if user_session_key not in self.user_sessions or 'results' not in self.user_sessions[user_session_key]:
                yield event.plain_result(self.messages['no_search_for_transfer'])
                return

            session_data = self.user_sessions[user_session_key]
            current_page = session_data.get('current_page', 1)
            results = session_data['results']

            # 解析搜索结果数据
            result_list = []
            if isinstance(results, str) and session_data.get('is_sse', False):
                # SSE响应，需要重新解析
                parsed_data = self._parse_sse_response(
                    results, session_data.get('keyword', ''), current_page)
                if isinstance(parsed_data, list):
                    result_list = parsed_data
            elif isinstance(results, dict):
                # JSON响应，提取数据
                if 'result' in results:
                    result_list = results['result']
                elif 'data' in results:
                    result_list = results['data']
                elif 'list' in results:
                    result_list = results['list']
            elif isinstance(results, list):
                result_list = results

            if not result_list:
                yield event.plain_result(self.messages['search_expired'])
                return

            # 计算当前页的起始索引
            page_size = 5
            start_index = (current_page - 1) * page_size

            # 检查资源编号是否有效
            if resource_index < 1 or resource_index > len(result_list):
                yield event.plain_result(self.messages['invalid_resource_index'].format(len(result_list)))
                return

            # 获取对应的资源
            target_resource = result_list[start_index + resource_index - 1]
            url = target_resource.get('url', '')
            title = target_resource.get('title', '未知标题')

            if not url:
                yield event.plain_result(self.messages['no_valid_link'])
                return

            # 检查是否为夸克网盘链接
            # 移除只支持夸克网盘的限制，支持所有心悦系统支持的网盘
            supported_domains = ['pan.quark.cn', 'www.alipan.com', 'www.aliyundrive.com',
                                 'pan.baidu.com', 'drive.uc.cn', 'fast.uc.cn', 'pan.xunlei.com']
            is_supported = False
            # 转换URL为小写以处理大小写不一致的情况
            lower_url = url.lower()
            for domain in supported_domains:
                if domain in lower_url:
                    is_supported = True
                    break

            if not is_supported:
                yield event.plain_result("❌ 暂不支持该网盘的转存功能")
                return

            # 发送转存中提示
            yield event.plain_result(self.messages['transferring'].format(title))

            # 添加延迟避免请求过于频繁
            await asyncio.sleep(self.transfer_delay)

            # 调用转存API
            transfer_result = await self._transfer_and_share(url, "")
            if transfer_result['success']:
                result = f"✅ 转存成功\n资源标题：{transfer_result['title']}\n分享链接：{transfer_result['share_url']}"
            else:
                result = f"❌ 转存失败\n{transfer_result.get('error', '未知错误')}"
            yield event.plain_result(result)

        except Exception as e:
            logger.error(f"处理转存指令时发生错误: {str(e)}")
            yield event.plain_result(self.messages['transfer_error'])

    async def _search_resources(self, user_session_key: str, keyword: str, is_full_network: bool = False, pan_type: int = 0, page: int = 1) -> str:
        """搜索资源的核心方法"""
        # 输入验证
        if not keyword or not keyword.strip():
            return self.messages['empty_keyword']

        # 关键词长度限制
        if len(keyword) > 50:
            return self.messages['keyword_too_long']

        retry_count = 0
        while retry_count < self.max_retries:
            try:
                # 构建搜索URL
                if is_full_network:
                    # 全网搜索API
                    url = f"{self.base_url}/api/other/all_search"
                    params = {
                        "title": keyword
                    }
                else:
                    # 普通搜索API
                    url = f"{self.base_url}/api/other/web_search"
                    params = {
                        "title": keyword,
                        "is_type": pan_type,  # 网盘类型
                        "is_show": 1  # 显示网址
                    }

                # 设置请求头，模拟浏览器访问并添加API认证
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                    'Accept': 'text/event-stream',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                    'Referer': f'{self.base_url}/',
                    'Origin': self.base_url
                }
                
                # 如果配置了API密钥，添加到请求头
                if self.api_key:
                    headers['Authorization'] = f'Bearer {self.api_key}'

                # 发起HTTP请求
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=self.search_timeout)) as response:
                        if response.status == 200:
                            # 解析响应
                            try:
                                # 检查响应内容类型
                                content_type = response.headers.get('content-type', '')
                                if 'text/event-stream' in content_type:
                                    # 处理SSE流式响应
                                    text = await response.text()
                                    # 解析SSE响应
                                    parsed_data = self._parse_sse_response(text, keyword, page)
                                    # 保存搜索结果到用户会话（用于分页）
                                    self.user_sessions[user_session_key] = {
                                        'results': text,  # 保存原始SSE响应文本
                                        'keyword': keyword,
                                        'is_full_network': is_full_network,
                                        'pan_type': pan_type,
                                        'current_page': page,
                                        'total_pages': 1,  # 默认值，会在_format_search_results中更新
                                        'is_sse': True  # 标记为SSE响应
                                    }
                                    # 格式化结果并返回
                                    return await self._format_search_results(user_session_key, parsed_data, keyword, is_full_network, page)
                                else:
                                    # 处理标准JSON响应
                                    data = await response.json()
                                    # 保存搜索结果到用户会话（用于分页）
                                    self.user_sessions[user_session_key] = {
                                        'results': data,
                                        'keyword': keyword,
                                        'is_full_network': is_full_network,
                                        'pan_type': pan_type,
                                        'current_page': page,
                                        'total_pages': 1,  # 默认值，会在_format_search_results中更新
                                        'is_sse': False  # 标记为JSON响应
                                    }
                                    return await self._format_search_results(user_session_key, data, keyword, is_full_network, page)
                            except Exception as e:
                                logger.error(
                                    f"解析响应失败: {str(e)}, status={response.status}, content-type={content_type}")
                                return self.messages['parse_search_failed']
                        elif response.status == 404:
                            return self.messages['no_resources_found'].format(keyword)
                        elif response.status == 429:
                            retry_count += 1
                            if retry_count >= self.max_retries:
                                return self.messages['too_many_requests']
                            await asyncio.sleep(2 ** retry_count)  # 指数退避
                            continue
                        else:
                            retry_count += 1
                            if retry_count >= self.max_retries:
                                return self.messages['search_service_unavailable'].format(response.status)
                            await asyncio.sleep(1)
                            continue

            except asyncio.TimeoutError:
                retry_count += 1
                if retry_count >= self.max_retries:
                    return self.messages['search_timeout']
                await asyncio.sleep(2 ** retry_count)  # 指数退避
                continue
            except aiohttp.ClientError as e:
                retry_count += 1
                if retry_count >= self.max_retries:
                    logger.error(f"网络请求错误: {str(e)}")
                    return self.messages['network_error']
                await asyncio.sleep(2 ** retry_count)
                continue
            except Exception as e:
                logger.error(f"搜索过程中发生未知错误: {str(e)}")
                return self.messages['unknown_search_error'].format(str(e))

        return self.messages['search_service_unavailable_temporarily']

    async def _local_search(self, keyword: str) -> str:
        """本地数据库搜索（只查询数据库，不执行全网搜索）"""
        try:
            # 使用 /api/search/index 接口查询本地数据库
            # 该接口默认查询 is_time=0 的永久资源，不会触发全网搜索
            url = f"{self.base_url}/api/search/index"
            params = {
                "title": keyword,
                "page": 1,
                "page_size": 10
            }
            
            logger.info(f"正在本地数据库查找: {keyword}")
            
            # 设置较短的超时时间（5秒），本地数据库查询应该很快
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        # 检查返回数据
                        if data.get('code') == 200 and data.get('data'):
                            result_data = data['data']
                            # getList返回的数据结构：{total_result: ..., items: [...]}
                            if isinstance(result_data, dict) and 'items' in result_data:
                                results = result_data['items']
                            elif isinstance(result_data, dict) and 'list' in result_data:
                                results = result_data['list']
                            elif isinstance(result_data, list):
                                results = result_data
                            else:
                                results = []
                            
                            if len(results) == 0:
                                return f"❌ 本地数据库未找到相关资源: {keyword}\n\n💡 提示：可以尝试使用【搜{keyword}】进行全网搜索"
                            
                            # 格式化结果
                            result_text = f"🔍 本地数据库找到 {len(results)} 个相关资源：\n\n"
                            
                            for i, item in enumerate(results, 1):
                                title = item.get('title', '未知标题')
                                url_link = item.get('url', '')
                                is_time = item.get('is_time', 0)
                                
                                # 识别网盘类型
                                pan_type_name = "未知"
                                if 'quark.cn' in url_link:
                                    pan_type_name = "夸克"
                                elif 'pan.baidu.com' in url_link:
                                    pan_type_name = "百度"
                                elif 'drive.uc.cn' in url_link:
                                    pan_type_name = "UC"
                                elif 'pan.xunlei.com' in url_link:
                                    pan_type_name = "迅雷"
                                elif 'aliyundrive.com' in url_link or 'alipan.com' in url_link:
                                    pan_type_name = "阿里"
                                
                                # 根据是否是临时资源添加不同的图标
                                if is_time == 1:
                                    result_text += f"{i}. 【{pan_type_name}】{title}\n🌐 链接: {url_link}\n\n"
                                else:
                                    result_text += f"{i}. 【{pan_type_name}】{title}\n🔗 链接: {url_link}\n\n"
                            
                            result_text += "─────────────\n"
                            
                            # 检查是否有临时资源
                            has_temp = any(item.get('is_time') == 1 for item in results)
                            if has_temp:
                                result_text += "🌐 资源来源网络，30分钟后删除\n"
                                result_text += "─────────────\n"
                            
                            result_text += f"💡 提示：结果不满意，请输入【搜{keyword}】，进行全网搜索"
                            
                            return result_text
                        else:
                            return f"❌ 本地数据库未找到相关资源\n\n💡 提示：可以尝试使用【搜{keyword}】进行全网搜索"
                    elif response.status == 404:
                        return f"❌ 本地数据库未找到相关资源\n\n💡 提示：可以尝试使用【{keyword}】进行全网搜索"
                    else:
                        return "❌ 搜索服务异常，请稍后重试"
                        
        except asyncio.TimeoutError:
            # 超时可能是因为数据库没结果，接口在执行全网搜索
            # 我们直接返回未找到
            logger.warning(f"本地搜索超时（可能数据库无结果）: {keyword}")
            return f"❌ 本地数据库未找到相关资源: {keyword}\n\n💡 提示：可以尝试使用【搜{keyword}】进行全网搜索"
        except Exception as e:
            logger.error(f"本地搜索失败: {str(e)}")
            return "❌ 搜索失败，请稍后重试"

    async def _full_network_search(self, keyword: str) -> str:
        """全网搜索，依次搜索多种网盘类型"""
        # 输入验证
        if not keyword or not keyword.strip():
            return self.messages['empty_keyword']

        results = []
        success_count = 0

        # 依次搜索夸克、百度、UC、迅雷
        for pan_name, pan_type in self.search_types.items():
            try:
                # 使用统一的会话key
                dummy_key = "dummy"  # 全网搜索不需要保存会话
                result = await self._search_resources(dummy_key, keyword, is_full_network=False, pan_type=pan_type)
                if self.messages['no_resources_found'].format('') not in result and self.messages['search_failed'] not in result and self.messages['search_service_unavailable_temporarily'] not in result:
                    results.append(f"【{pan_name}网盘】\n{result}")
                    success_count += 1
                elif self.messages['search_service_unavailable_temporarily'] in result:
                    # 如果服务不可用，返回错误信息
                    return result

                # 添加延时避免请求过于频繁
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"搜索{pan_name}网盘时发生错误: {str(e)}")
                continue

        if success_count == 0:
            return self.messages['no_resources_found'].format(keyword)

        # 合并结果
        return self.messages['full_network_search_results'].format(keyword) + "\n\n".join(results)

    async def _format_search_results(self, user_session_key: str, data, keyword: str, is_full_network: bool, page: int = 1) -> str:
        """格式化搜索结果，支持分页功能"""
        try:
            # 初始化结果列表
            result_list = []

            # 处理不同格式的响应数据
            if isinstance(data, list):
                result_list = data
            elif isinstance(data, dict):
                # 如果是包含'result'字段的字典
                if 'result' in data:
                    result_list = data['result']
                # 如果是包含'data'字段的字典
                elif 'data' in data:
                    result_list = data['data']
                # 如果是包含'list'字段的字典
                elif 'list' in data:
                    result_list = data['list']
                # 如果响应是{'code': 0, 'msg': 'success', 'data': [...]}格式
                elif 'code' in data and data.get('code') == 0 and 'data' in data:
                    result_list = data['data']
            elif isinstance(data, str):
                # 如果是字符串，可能是SSE响应文本，需要重新解析
                if user_session_key in self.user_sessions:
                    session_data = self.user_sessions[user_session_key]
                    if session_data.get('is_sse', False):
                        # 对于SSE响应，直接解析数据而不是再次调用_parse_sse_response
                        # 因为_parse_sse_response会再次调用此方法导致循环

                        # 分割SSE消息
                        lines = data.strip().split('\n')
                        data_lines = [
                            line for line in lines if line.startswith('data:')]

                        # 提取JSON数据
                        json_data_list = []
                        for data_line in data_lines:
                            # 移除"data:"前缀并解析JSON
                            json_str = data_line[5:].strip()
                            if json_str and json_str != '[DONE]':
                                import json
                                try:
                                    json_data = json.loads(json_str)
                                    json_data_list.append(json_data)
                                except json.JSONDecodeError:
                                    continue

                        # 合并所有数据
                        combined_data = []
                        for json_data in json_data_list:
                            # 直接处理JSON对象，而不是期望它有'data'字段
                            if isinstance(json_data, dict):
                                # 如果是字典且有'url'字段，说明是我们需要的数据项
                                if 'url' in json_data:
                                    combined_data.append(json_data)
                                # 如果有'data'字段且是列表，扩展它
                                elif 'data' in json_data and isinstance(json_data['data'], list):
                                    combined_data.extend(json_data['data'])
                                # 如果是其他字典形式，直接添加
                                else:
                                    combined_data.append(json_data)
                            elif isinstance(json_data, list):
                                combined_data.extend(json_data)

                        result_list = combined_data
            else:
                # 如果data不是列表也不是字典，则直接返回空结果
                return self.messages['no_resources_found'].format(keyword)

            # 如果没有结果，直接返回
            if not result_list:
                return self.messages['no_resources_found'].format(keyword)

            # 分页处理，使用配置中的每页结果数
            page_size = self.results_per_page
            total_results = len(result_list)
            total_pages = (total_results + page_size - 1) // page_size  # 计算总页数

            # 更新用户会话中的总页数
            if user_session_key in self.user_sessions:
                self.user_sessions[user_session_key]['total_pages'] = total_pages

            # 验证页码有效性
            if page < 1 or page > total_pages:
                return self.messages['invalid_page_number'].format(total_pages)

            # 计算当前页的起始和结束索引
            start_index = (page - 1) * page_size
            end_index = min(start_index + page_size, total_results)

            # 获取当前页的结果
            current_page_results = result_list[start_index:end_index]

            # 并发转存并格式化结果（性能优化）
            formatted_results = await self._transfer_and_format_results(current_page_results, start_index)

            # 构造结果文本，使用新的格式
            result_text = self.messages['search_results_header'].format(
                total_results) + '\n\n' + '\n\n'.join(formatted_results)

            # 添加分隔线和页码信息
            result_text += '\n\n' + self.messages['search_results_separator'] + '\n' + self.messages['search_results_footer'] + '\n' + self.messages['search_results_separator_footer']
            
            # 添加网站推广链接
            result_text += '\n' + self.messages['search_results_website_promo'].format(self.base_url) + '\n' + self.messages['search_results_separator_footer']
            
            # 只有启用分页功能时才显示分页信息
            if self.enable_pagination:
                result_text += '\n' + self.messages['search_results_page_info'].format(
                    page, total_pages) + '\n' + self.messages['search_results_navigation']

            return result_text
        except Exception as e:
            logger.error(f"格式化搜索结果时发生错误: {str(e)}")
            return self.messages['format_search_error']

    async def _transfer_and_format_results(self, results: list, start_index: int) -> list:
        """并发转存并格式化结果（性能优化）"""
        if not self.enable_transfer or not self.api_key:
            # 转存未启用，直接返回原始链接
            return self._format_results_without_transfer(results, start_index)

        # 预先获取所有需要的Cookie（避免在并发任务中重复请求）
        pan_types_needed = set()
        for item in results:
            url = item.get('url', '')
            if url:
                pan_type = self._identify_pan_type(url)
                if pan_type in ['quark', 'baidu', 'uc', 'xunlei', 'ali']:
                    pan_types_needed.add(pan_type)
        
        # 并发获取所有需要的Cookie
        await self._prefetch_cookies(pan_types_needed)

        # 创建转存任务
        tasks = []
        for i, item in enumerate(results):
            global_index = start_index + i + 1
            title = item.get('title', self.messages['resource_title_default'])
            url = item.get('url', '')
            
            if url:
                pan_type = self._identify_pan_type(url)
                if pan_type in ['quark', 'baidu', 'uc', 'xunlei', 'ali']:
                    # 创建转存任务
                    task = self._transfer_single_resource(global_index, title, url)
                    tasks.append(task)
                else:
                    # 不支持的网盘类型，直接格式化
                    tasks.append(self._format_single_result(global_index, title, url, None))
            else:
                tasks.append(self._format_single_result(global_index, title, '', None))

        # 并发执行所有转存任务
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 处理结果
        formatted = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"转存任务异常: {str(result)}")
                formatted.append("⚠️ 处理失败")
            else:
                formatted.append(result)
        
        return formatted

    async def _transfer_single_resource(self, index: int, title: str, url: str) -> str:
        """转存单个资源并返回格式化结果"""
        try:
            # 提取密码
            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            pwd_code = query_params.get('pwd', [''])[0]
            
            transfer_result = await self._transfer_and_share(url, pwd_code)
            
            if transfer_result['success']:
                new_title = transfer_result['title']
                new_url = transfer_result['share_url']
                return f"{index}. {new_title}\n✅ 链接: {new_url}"
            else:
                # 转存失败，不返回原始链接
                return f"{index}. {title}\n❌ 转存失败，请尝试搜索其他网盘"
        except Exception as e:
            logger.error(f"转存资源失败: {str(e)}")
            # 转存异常，不返回原始链接
            return f"{index}. {title}\n❌ 转存失败，请尝试搜索其他网盘"

    async def _format_single_result(self, index: int, title: str, url: str, transfer_result) -> str:
        """格式化单个结果"""
        if url:
            return f"{index}. {title}\n链接: {url}"
        return f"{index}. {title}"

    def _format_results_without_transfer(self, results: list, start_index: int) -> list:
        """不转存直接格式化结果"""
        formatted = []
        for i, item in enumerate(results):
            global_index = start_index + i + 1
            title = item.get('title', self.messages['resource_title_default'])
            url = item.get('url', '')
            
            if url:
                formatted.append(f"{global_index}. {title}\n🔗 链接: {url}")
            else:
                formatted.append(f"{global_index}. {title}")
        return formatted

    def _identify_pan_type(self, url: str) -> str:
        """根据URL识别网盘类型"""
        domain_patterns = {
            'quark': ['pan.quark.cn'],
            'ali': ['www.alipan.com', 'www.aliyundrive.com'],
            'baidu': ['pan.baidu.com'],
            'uc': ['drive.uc.cn', 'fast.uc.cn'],
            'xunlei': ['pan.xunlei.com']
        }

        # 转换URL为小写以处理大小写不一致的情况
        lower_url = url.lower()

        for pan_type, patterns in domain_patterns.items():
            for pattern in patterns:
                if pattern in lower_url:
                    return pan_type

        return 'quark'  # 默认返回夸克

    async def _prefetch_cookies(self, pan_types: set):
        """预先并发获取所有需要的Cookie（性能优化）"""
        tasks = []
        for pan_type in pan_types:
            # 检查缓存中是否有Cookie或已过期（5分钟）
            cache_entry = self._cookie_cache.get(pan_type)
            if not cache_entry or (time.time() - cache_entry[1] > 300):  # 5分钟过期
                tasks.append(self._get_actual_cookie_value(pan_type))
        
        # 并发获取所有Cookie
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _get_actual_cookie_value(self, pan_type: str) -> str:
        """从心悦API获取指定网盘的实际Cookie值（带缓存）"""
        # 检查缓存
        cache_entry = self._cookie_cache.get(pan_type)
        if cache_entry and (time.time() - cache_entry[1] < 300):  # 5分钟内有效
            return cache_entry[0]
        
        # 根据网盘类型确定API端点
        api_endpoints = {
            'quark': 'quark',
            'baidu': 'baidu',
            'uc': 'uc',
            'xunlei': 'xunlei',
            'ali': 'ali'
        }

        if pan_type not in api_endpoints:
            logger.warning(f"不支持的网盘类型: {pan_type}")
            return ""

        try:
            async with aiohttp.ClientSession() as session:
                # 使用正确的API路径
                api_url = f"{self.base_url}/api/GetCookie/{api_endpoints[pan_type]}"

                async with session.get(
                    api_url,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get('code') == 200:
                            # 从返回的数据中提取实际的Cookie值
                            cookie_key = f"{pan_type}_cookie"
                            actual_cookie = result.get('data', {}).get(cookie_key, "")
                            if actual_cookie:
                                # 缓存Cookie
                                self._cookie_cache[pan_type] = (actual_cookie, time.time())
                                logger.info(f"✅ 获取{pan_type}网盘Cookie成功")
                                return actual_cookie
                            else:
                                return ""
                        else:
                            return ""
                    else:
                        return ""
        except Exception as e:
            logger.error(f"❌ 获取{pan_type}网盘Cookie异常: {str(e)}")
            return ""

    async def _get_cookie_from_database(self, pan_type: str) -> str:
        """从心悦数据库获取指定网盘的Cookie"""
        # 根据网盘类型确定API端点
        api_endpoints = {
            'quark': 'quark',
            'baidu': 'baidu',
            'uc': 'uc',
            'xunlei': 'xunlei',
            'ali': 'ali'
        }

        if pan_type not in api_endpoints:
            logger.warning(f"不支持的网盘类型: {pan_type}")
            return ""

        try:
            async with aiohttp.ClientSession() as session:
                # 使用正确的API路径
                api_url = f"{self.base_url}/api/GetCookie/{api_endpoints[pan_type]}"

                async with session.get(
                    api_url,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        return "API_SUCCESS"
                    else:
                        return ""
        except Exception as e:
            logger.error(f"❌ 获取{pan_type}网盘Cookie异常: {str(e)}")
            return ""

    def _parse_sse_response(self, text: str, keyword: str, page: int = 1) -> str:
        """解析SSE流式响应"""
        try:
            # 分割SSE消息
            lines = text.strip().split('\n')
            data_lines = [line for line in lines if line.startswith('data:')]

            # 提取JSON数据
            json_data_list = []
            for data_line in data_lines:
                # 移除"data:"前缀并解析JSON
                json_str = data_line[5:].strip()
                if json_str and json_str != '[DONE]':
                    import json
                    try:
                        json_data = json.loads(json_str)
                        json_data_list.append(json_data)
                    except json.JSONDecodeError:
                        continue

            # 如果没有有效数据，返回空列表
            if not json_data_list:
                return []

            # 合并所有数据
            combined_data = []
            for json_data in json_data_list:
                # 直接处理JSON对象，而不是期望它有'data'字段
                if isinstance(json_data, dict):
                    # 如果是字典且有'url'字段，说明是我们需要的数据项
                    if 'url' in json_data:
                        combined_data.append(json_data)
                    # 如果有'data'字段且是列表，扩展它
                    elif 'data' in json_data and isinstance(json_data['data'], list):
                        combined_data.extend(json_data['data'])
                    # 如果是其他字典形式，直接添加
                    else:
                        combined_data.append(json_data)
                elif isinstance(json_data, list):
                    combined_data.extend(json_data)

            # 直接返回解析后的数据列表，让调用者处理格式化
            return combined_data
        except Exception as e:
            logger.error(f"解析SSE响应时发生错误: {str(e)}")
            return []

    async def _transfer_and_share(self, url: str, code: str = "") -> dict:
        """调用心悦转存再分享API，返回包含标题和链接的字典"""
        try:
            # 使用配置文件中的API密钥
            api_key = self.api_key
            if not api_key:
                return {'success': False, 'error': self.messages['api_key_not_configured']}

            # 根据URL识别网盘类型
            pan_type = self._identify_pan_type(url)
            
            # 从心悦数据库获取Cookie
            actual_cookie = ""
            cache_entry = self._cookie_cache.get(pan_type)
            if cache_entry and (time.time() - cache_entry[1] < 300):  # 5分钟内有效
                actual_cookie = cache_entry[0]
            else:
                # 缓存失效，从API获取
                cookie_status = await self._get_cookie_from_database(pan_type)
                if not cookie_status:
                    logger.warning(f"❌ 获取{pan_type}网盘Cookie失败")
                    return {'success': False, 'error': '抱歉，cookie过期，请联系群主！'}
                else:
                    # 获取实际的Cookie值（会自动缓存）
                    actual_cookie = await self._get_actual_cookie_value(pan_type)
                    if not actual_cookie:
                        logger.warning(f"❌ 获取{pan_type}网盘Cookie失败")
                        return {'success': False, 'error': '抱歉，cookie过期，请联系群主！'}

            # 构建转存API请求数据
            transfer_data = {
                'url': url,
                'code': code,
                'expired_type': 2,  # 1为永久资源，2为临时资源
                'isType': 0,  # 0转存并分享，1直接获取资源信息
                'api_key': api_key,  # 使用从配置获取的API密钥
                'isSave': 1  # 添加此参数以保存到数据库
            }

            # 注意：对于迅雷网盘，Cookie应该通过心悦系统的配置机制传递，
            # 而不是通过HTTP请求头。心悦系统会从数据库配置中读取Cookie。
            headers = {
                'Content-Type': 'application/json'
            }

            # 调用心悦转存API
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/open/transfer",
                    json=transfer_data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get('code') == 200:
                            # 转存成功，返回分享信息
                            # 心悦系统会自动将结果保存到数据库（因为isSave=1）
                            return {
                                'success': True,
                                'title': result['data']['title'],
                                'share_url': result['data']['share_url']
                            }
                        else:
                            return {
                                'success': False,
                                'error': result.get('message', self.messages['unknown_error'])
                            }
                    else:
                        return {
                            'success': False,
                            'error': self.messages['transfer_service_error'].format(response.status)
                        }

        except asyncio.TimeoutError:
            return {'success': False, 'error': self.messages['transfer_timeout']}
        except Exception as e:
            logger.error(f"转存过程中发生错误：{str(e)}")
            return {'success': False, 'error': self.messages['transfer_process_error'].format(str(e))}

    @filter.command("使用方法")
    async def show_usage(self, event: AstrMessageEvent):
        """显示机器人使用方法"""
        try:
            usage_info = f"""📖 心悦搜索机器人使用指南

🔍 基础搜索指令：
• 找 + 关键词 → 本地搜索（只查本地数据库，速度快）
  示例：找复仇者联盟

• 搜 + 关键词 → 全网搜索（从外部API搜索）
  示例：搜复仇者联盟

• 百度 + 关键词 → 搜索百度网盘资源
  示例：百度复仇者联盟

• uc/UC + 关键词 → 搜索UC网盘资源
  示例：uc复仇者联盟

• 迅雷 + 关键词 → 搜索迅雷网盘资源
  示例：迅雷复仇者联盟

📄 翻页指令：
• 上 或 0 → 查看上一页
• 下 或 1 → 查看下一页

💡 搜索技巧：
• 优先使用"找"进行快速查询
• 本地无结果时再使用"搜"进行全网搜索
• 关键词尽量简短准确

🔗 链接状态说明：
• ✅ = 转存成功（已转存并生成新链接）
• ❌ = 转存失败（转存失败，请尝试搜索其他网盘）
• 🔗 = 直接分享（未启用转存）
• 🌐 = 临时资源（30分钟后删除）

─────────────
更多资源请访问：{self.base_url}"""

            yield event.plain_result(usage_info)

        except Exception as e:
            logger.error(f"显示使用方法时发生错误: {str(e)}")
            yield event.plain_result("❌ 获取使用方法失败，请稍后重试")

    async def terminate(self):
        """插件销毁方法"""
        logger.info("心悦搜索机器人插件已终止")
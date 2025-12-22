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
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: Dict[str, List[float]] = defaultdict(list)
    def is_allowed(self, user_id: str) -> bool:
        now = time.time()
        self.requests[user_id] = [
            t for t in self.requests[user_id] 
            if now - t < self.window_seconds
        ]
        if len(self.requests[user_id]) >= self.max_requests:
            return False
        self.requests[user_id].append(now)
        return True
    def get_wait_time(self, user_id: str) -> int:
        if not self.requests[user_id]:
            return 0
        oldest = min(self.requests[user_id])
        wait = self.window_seconds - (time.time() - oldest)
        return max(0, int(wait))
@register("astrbot_plugin_xinyue_search", "阿立", "心悦搜索机器人插件", "1.4.0")
class XinyueSearchBotPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        license_token = config.get('license_token', '') if config else ''
        config_qq = config.get('bot_qq', '') if config else ''
        actual_qq = ''
        try:
            if hasattr(context, 'base_config') and context.base_config:
                if hasattr(context.base_config, 'platform_settings'):
                    for platform in context.base_config.platform_settings:
                        if hasattr(platform, 'account') and platform.account:
                            actual_qq = str(platform.account)
                            logger.info(f"✓ 检测到实际运行的机器人QQ号: {actual_qq}")
                            break
            if not actual_qq and hasattr(context, 'config_helper'):
                config_helper = context.config_helper
                if hasattr(config_helper, 'get_platform_config'):
                    platform_config = config_helper.get_platform_config()
                    if platform_config and 'account' in platform_config:
                        actual_qq = str(platform_config['account'])
                        logger.info(f"✓ 检测到实际运行的机器人QQ号: {actual_qq}")
        except Exception as e:
            logger.warning(f"自动获取机器人QQ号失败: {e}")
        bot_qq = ''
        if license_token:
            if not config_qq:
                raise Exception(
                    "❌ 使用正式授权时必须配置 bot_qq\n"
                    "💡 请在配置中填写你的机器人QQ号\n"
                    "💡 如果只是试用，请不要填写 license_token"
                )
            if actual_qq and config_qq != actual_qq:
                raise Exception(
                    f"❌ 配置的QQ号与实际运行的QQ号不一致\n"
                    f"💡 配置的QQ号: {config_qq}\n"
                    f"💡 实际运行的QQ号: {actual_qq}\n"
                    f"💡 请修改配置中的 bot_qq 为实际运行的QQ号"
                )
            bot_qq = config_qq
            logger.info(f"✓ 使用配置的QQ号: {bot_qq}")
        else:
            if actual_qq:
                bot_qq = actual_qq
                if config_qq and config_qq != actual_qq:
                    logger.warning(f"⚠ 配置的QQ号({config_qq})与实际QQ号({actual_qq})不一致，使用实际QQ号")
            elif config_qq:
                bot_qq = config_qq
                logger.info(f"✓ 使用配置的QQ号: {bot_qq}")
            else:
                import hashlib
                plugin_dir = os.path.dirname(__file__)
                bot_qq = hashlib.md5(plugin_dir.encode()).hexdigest()[:10]
                logger.warning(f"⚠ 无法获取机器人QQ号，使用临时标识: {bot_qq}")
                logger.warning("💡 建议在配置中手动设置 bot_qq 以获得更好的体验")
        from .license_validator import LicenseValidator
        if not license_token:
            logger.info("未配置授权Token，进入试用模式...")
            try:
                validator = LicenseValidator(None)
                valid, msg = validator.validate_trial(bot_qq)
                if not valid:
                    raise Exception(f"❌ {msg}\n💡 联系开发者获取授权Token以继续使用")
                logger.info(f"✓ {msg}")
                self._license_valid = True
                self._license_validator = validator
            except Exception as e:
                raise Exception(f"❌ 试用验证失败: {str(e)}")
        else:
            logger.info("正在验证授权Token...")
            try:
                validator = LicenseValidator(license_token)
                valid, msg = validator.validate(bot_qq)
                if not valid:
                    raise Exception(f"❌ 授权验证失败: {msg}\n💡 请联系开发者获取或续费授权")
                logger.info(f"✓ {msg}")
                license_info = validator.get_license_info()
                if license_info:
                    user_info = license_info.get('user_info', '未知')
                    plan_type = license_info.get('plan_type', '未知')
                    expire_time = license_info.get('expire_time', '未知')
                    logger.info("📋 授权用户: {}".format(user_info))
                    logger.info("📦 套餐类型: {}".format(plan_type))
                    logger.info("📅 到期时间: {}".format(expire_time))
                self._license_valid = True
                self._license_validator = validator
            except Exception as e:
                raise Exception(f"❌ 授权验证异常: {str(e)}")
        if config is None:
            config = {}
        self.config = {
            'base_url': config.get('api_url', 'https://aliso.vip').rstrip('/'),
            'api_key': config.get('api_key', ''),
            'max_retries': config.get('max_retries', 3),
            'search_timeout': config.get('timeout', 10),
            'transfer_timeout': config.get('transfer_timeout', 30),
            'results_per_page': config.get('max_results', 5),
            'enable_transfer': config.get('enable_transfer', True),
            'transfer_delay': config.get('transfer_delay', 1),
            'log_level': 'INFO',
            'log_format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            'search_types': {
                '夸克': 0,
                '百度': 2,
                'UC': 3,
                '迅雷': 4
            },
            'search_commands': {
                '搜': '夸克',
                '百度': '百度',
                'uc': 'UC',
                'UC': 'UC',
                '迅雷': '迅雷'
            },
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
        self.base_url = self.config['base_url']
        self.api_key = self.config['api_key']
        self.max_retries = self.config['max_retries']
        self.search_timeout = self.config['search_timeout']
        self.transfer_timeout = self.config['transfer_timeout']
        self.results_per_page = self.config['results_per_page']
        self.enable_transfer = self.config['enable_transfer']
        self.enable_pagination = config.get('enable_pagination', True)
        self.transfer_delay = self.config['transfer_delay']
        self.search_types = self.config['search_types']
        self.search_commands = self.config['search_commands']
        self.messages = self.config['messages']
        self.rate_limiter = RateLimiter(max_requests=10, window_seconds=60)
        self.user_sessions: Dict[str, Dict] = {}
        self._cookie_cache: Dict[str, tuple] = {}
    def _load_config(self) -> dict:
        config_file = os.path.join(os.path.dirname(__file__), 'config.yaml')
        default_config = {
            'base_url': 'https://aliso.vip',
            'api_key': '',
            'max_retries': 3,
            'search_timeout': 10,
            'transfer_timeout': 30,
            'results_per_page': 5,
            'enable_transfer': True,
            'transfer_delay': 1,
            'log_level': 'INFO',
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
                        for key, value in config.items():
                            if key == 'messages' and isinstance(value, dict):
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
                with open(config_file, 'w', encoding='utf-8') as f:
                    yaml.dump(default_config, f,
                              default_flow_style=False, allow_unicode=True)
                logger.info("已创建默认配置文件")
        except Exception as e:
            logger.error(f"加载配置文件失败: {str(e)}，使用默认配置")
        return default_config
    async def initialize(self):
        logger.info("心悦搜索插件已加载")
        logger.info(f"基础URL: {self.base_url}")
        logger.info(f"转存功能: {'已启用' if self.enable_transfer else '已禁用'}")
        if self.api_key:
            logger.info("API密钥: 已配置")
    def _check_license(self):
        if not hasattr(self, '_license_valid'):
            raise Exception("❌ 授权未初始化，请重启插件")
        if not self._license_valid:
            raise Exception("❌ 授权已失效，请联系开发者")
        else:
            logger.warning("API密钥: 未配置，转存功能可能无法正常工作")
    def _get_user_session_key(self, event: AstrMessageEvent) -> str:
        try:
            user_id = None
            if hasattr(event, 'message_obj') and event.message_obj:
                if hasattr(event.message_obj, 'sender') and event.message_obj.sender:
                    user_id = event.message_obj.sender.user_id
            if user_id:
                group_id = event.message_obj.group_id if hasattr(event.message_obj, 'group_id') else event.session_id
                return f"{user_id}@{group_id}"
            logger.warning("无法获取用户ID，使用unified_msg_origin作为会话key")
            return event.unified_msg_origin
        except Exception as e:
            logger.error(f"获取用户会话key时发生错误: {str(e)}")
            return event.unified_msg_origin
    def _format_reply_with_mention(self, event: AstrMessageEvent, message: str) -> str:
        return message
    def _get_user_id_for_rate_limit(self, event: AstrMessageEvent) -> str:
        try:
            if hasattr(event, 'message_obj') and event.message_obj:
                if hasattr(event.message_obj, 'sender') and event.message_obj.sender:
                    return str(event.message_obj.sender.user_id)
            logger.warning("无法获取用户ID用于限流，使用unified_msg_origin")
            return event.unified_msg_origin
        except Exception as e:
            logger.error(f"获取用户ID用于限流时发生错误: {str(e)}")
            return event.unified_msg_origin
    @filter.regex(r"^搜(\s+|(?![索]).)\S+")
    async def search_resource(self, event: AstrMessageEvent, *args, **kwargs):
        try:
            user_session_key = self._get_user_session_key(event)
            user_id_for_limit = self._get_user_id_for_rate_limit(event)
            if not self.rate_limiter.is_allowed(user_id_for_limit):
                wait_time = self.rate_limiter.get_wait_time(user_id_for_limit)
                yield event.plain_result(f"❌ 请求过于频繁，请{wait_time}秒后再试")
                return
            message = event.message_str.strip()
            if message.startswith("搜"):
                keyword = message[1:].strip()
            else:
                yield event.plain_result(self.messages['search_command_format_error'].format(self.search_commands['搜']))
                return
            if not keyword:
                yield event.plain_result(self.messages['search_example_format'].format(self.search_commands['搜'], '电影名'))
                return
            yield event.plain_result(self._format_reply_with_mention(event, self.messages['searching']))
            result = await self._search_resources(user_session_key, keyword, is_full_network=False, pan_type=0)
            yield event.plain_result(self._format_reply_with_mention(event, result))
        except Exception as e:
            logger.error(f"搜索资源时发生错误: {str(e)}")
            yield event.plain_result(self.messages['search_error'])
    @filter.regex(r"^找\s*\S+")
    async def local_search(self, event: AstrMessageEvent):
        try:
            user_session_key = self._get_user_session_key(event)
            user_id_for_limit = self._get_user_id_for_rate_limit(event)
            if not self.rate_limiter.is_allowed(user_id_for_limit):
                wait_time = self.rate_limiter.get_wait_time(user_id_for_limit)
                yield event.plain_result(f"❌ 请求过于频繁，请{wait_time}秒后再试")
                return
            message = event.message_str.strip()
            if message.startswith("找"):
                keyword = message[1:].strip()
            else:
                yield event.plain_result("指令格式错误，请使用：找资源名称")
                return
            if not keyword:
                yield event.plain_result("请输入要查找的资源名称，例如：找电影名")
                return
            yield event.plain_result(self._format_reply_with_mention(event, "🔍 正在本地数据库查找，请稍候..."))
            result = await self._local_search(keyword)
            yield event.plain_result(self._format_reply_with_mention(event, result))
        except Exception as e:
            logger.error(f"本地搜索时发生错误: {str(e)}")
            yield event.plain_result("❌ 搜索过程中发生错误，请稍后重试")
    @filter.regex(r"^百度\s*\S+")
    async def baidu_search(self, event: AstrMessageEvent):
        try:
            user_session_key = self._get_user_session_key(event)
            user_id_for_limit = self._get_user_id_for_rate_limit(event)
            if not self.rate_limiter.is_allowed(user_id_for_limit):
                wait_time = self.rate_limiter.get_wait_time(user_id_for_limit)
                yield event.plain_result(f"❌ 请求过于频繁，请{wait_time}秒后再试")
                return
            message = event.message_str.strip()
            if message.startswith("百度"):
                keyword = message[2:].strip()
            else:
                yield event.plain_result(self.messages['baidu_format_error'])
                return
            if not keyword:
                yield event.plain_result(self.messages['baidu_example'].format('电影名'))
                return
            yield event.plain_result(self._format_reply_with_mention(event, self.messages['searching']))
            result = await self._search_resources(user_session_key, keyword, is_full_network=False, pan_type=2)
            yield event.plain_result(self._format_reply_with_mention(event, result))
        except Exception as e:
            logger.error(f"百度搜索时发生错误: {str(e)}")
            yield event.plain_result(self.messages['search_error'])
    @filter.regex(r"^uc\s*\S+")
    async def uc_search_lower(self, event: AstrMessageEvent):
        try:
            user_session_key = self._get_user_session_key(event)
            user_id_for_limit = self._get_user_id_for_rate_limit(event)
            if not self.rate_limiter.is_allowed(user_id_for_limit):
                wait_time = self.rate_limiter.get_wait_time(user_id_for_limit)
                yield event.plain_result(f"❌ 请求过于频繁，请{wait_time}秒后再试")
                return
            message = event.message_str.strip()
            if message.startswith("uc"):
                keyword = message[2:].strip()
            else:
                yield event.plain_result(self.messages['uc_format_error'])
                return
            if not keyword:
                yield event.plain_result(self.messages['uc_example'].format('电影名'))
                return
            yield event.plain_result(self._format_reply_with_mention(event, self.messages['searching']))
            result = await self._search_resources(user_session_key, keyword, is_full_network=False, pan_type=3)
            yield event.plain_result(self._format_reply_with_mention(event, result))
        except Exception as e:
            logger.error(f"UC搜索时发生错误: {str(e)}")
            yield event.plain_result(self.messages['search_error'])
    @filter.regex(r"^UC\s*\S+")
    async def uc_search_upper(self, event: AstrMessageEvent):
        try:
            user_session_key = self._get_user_session_key(event)
            user_id_for_limit = self._get_user_id_for_rate_limit(event)
            if not self.rate_limiter.is_allowed(user_id_for_limit):
                wait_time = self.rate_limiter.get_wait_time(user_id_for_limit)
                yield event.plain_result(f"❌ 请求过于频繁，请{wait_time}秒后再试")
                return
            message = event.message_str.strip()
            if message.startswith("UC"):
                keyword = message[2:].strip()
            else:
                yield event.plain_result(self.messages['uc_upper_format_error'])
                return
            if not keyword:
                yield event.plain_result(self.messages['uc_upper_example'].format('电影名'))
                return
            yield event.plain_result(self._format_reply_with_mention(event, self.messages['searching']))
            result = await self._search_resources(user_session_key, keyword, is_full_network=False, pan_type=3)
            yield event.plain_result(self._format_reply_with_mention(event, result))
        except Exception as e:
            logger.error(f"UC搜索时发生错误: {str(e)}")
            yield event.plain_result(self.messages['search_error'])
    @filter.regex(r"^迅雷\s*\S+")
    async def xunlei_search(self, event: AstrMessageEvent):
        try:
            user_session_key = self._get_user_session_key(event)
            user_id_for_limit = self._get_user_id_for_rate_limit(event)
            if not self.rate_limiter.is_allowed(user_id_for_limit):
                wait_time = self.rate_limiter.get_wait_time(user_id_for_limit)
                yield event.plain_result(f"❌ 请求过于频繁，请{wait_time}秒后再试")
                return
            message = event.message_str.strip()
            if message.startswith("迅雷"):
                keyword = message[2:].strip()
            else:
                yield event.plain_result(self.messages['xunlei_format_error'])
                return
            if not keyword:
                yield event.plain_result(self.messages['xunlei_example'].format('电影名'))
                return
            yield event.plain_result(self._format_reply_with_mention(event, self.messages['searching']))
            result = await self._search_resources(user_session_key, keyword, is_full_network=False, pan_type=4)
            yield event.plain_result(self._format_reply_with_mention(event, result))
        except Exception as e:
            logger.error(f"迅雷搜索时发生错误: {str(e)}")
            yield event.plain_result(self.messages['search_error'])
    @filter.regex(r"^1$")
    async def next_page(self, event: AstrMessageEvent):
        try:
            if not self.enable_pagination:
                return
            user_session_key = self._get_user_session_key(event)
            if user_session_key in self.user_sessions and 'results' in self.user_sessions[user_session_key]:
                session_data = self.user_sessions[user_session_key]
                results = session_data.get('results')
                if not results:
                    return
                current_page = session_data.get('current_page', 1)
                total_pages = session_data.get('total_pages', 1)
                if total_pages <= 1:
                    return
                keyword = session_data.get('keyword', '')
                is_full_network = session_data.get('is_full_network', False)
                pan_type = session_data.get('pan_type', 0)
                if current_page < total_pages:
                    yield event.plain_result(self._format_reply_with_mention(event, "⏳ 正在翻页，请稍候..."))
                    next_page = current_page + 1
                    self.user_sessions[user_session_key]['current_page'] = next_page
                    result = await self._format_search_results(user_session_key, results, keyword, is_full_network, next_page)
                    yield event.plain_result(self._format_reply_with_mention(event, result))
                else:
                    yield event.plain_result(self._format_reply_with_mention(event, self.messages['last_page']))
        except Exception as e:
            logger.error(f"处理下一页指令时发生错误: {str(e)}")
            yield event.plain_result(self.messages['next_page_error'])
    @filter.regex(r"^0$")
    async def previous_page(self, event: AstrMessageEvent):
        try:
            if not self.enable_pagination:
                return
            user_session_key = self._get_user_session_key(event)
            if user_session_key in self.user_sessions and 'results' in self.user_sessions[user_session_key]:
                session_data = self.user_sessions[user_session_key]
                results = session_data.get('results')
                if not results:
                    return
                current_page = session_data.get('current_page', 1)
                total_pages = session_data.get('total_pages', 1)
                if total_pages <= 1:
                    return
                keyword = session_data.get('keyword', '')
                is_full_network = session_data.get('is_full_network', False)
                pan_type = session_data.get('pan_type', 0)
                if current_page > 1:
                    yield event.plain_result(self._format_reply_with_mention(event, "⏳ 正在翻页，请稍候..."))
                    previous_page = current_page - 1
                    self.user_sessions[user_session_key]['current_page'] = previous_page
                    result = await self._format_search_results(user_session_key, results, keyword, is_full_network, previous_page)
                    yield event.plain_result(self._format_reply_with_mention(event, result))
                else:
                    yield event.plain_result(self._format_reply_with_mention(event, self.messages['first_page']))
        except Exception as e:
            logger.error(f"处理上一页指令时发生错误: {str(e)}")
            yield event.plain_result(self.messages['previous_page_error'])
    @filter.regex(r"^下$")
    async def next_page_simple(self, event: AstrMessageEvent):
        try:
            if not self.enable_pagination:
                return
            user_session_key = self._get_user_session_key(event)
            if user_session_key in self.user_sessions and 'results' in self.user_sessions[user_session_key]:
                session_data = self.user_sessions[user_session_key]
                results = session_data.get('results')
                if not results:
                    return
                current_page = session_data.get('current_page', 1)
                total_pages = session_data.get('total_pages', 1)
                if total_pages <= 1:
                    return
                keyword = session_data.get('keyword', '')
                is_full_network = session_data.get('is_full_network', False)
                pan_type = session_data.get('pan_type', 0)
                if current_page < total_pages:
                    yield event.plain_result(self._format_reply_with_mention(event, "⏳ 正在翻页，请稍候..."))
                    next_page = current_page + 1
                    self.user_sessions[user_session_key]['current_page'] = next_page
                    result = await self._format_search_results(user_session_key, results, keyword, is_full_network, next_page)
                    yield event.plain_result(self._format_reply_with_mention(event, result))
                else:
                    yield event.plain_result(self._format_reply_with_mention(event, self.messages['last_page']))
        except Exception as e:
            logger.error(f"处理下一页指令时发生错误: {str(e)}")
            yield event.plain_result(self.messages['next_page_error'])
    @filter.regex(r"^上$")
    async def previous_page_simple(self, event: AstrMessageEvent):
        try:
            if not self.enable_pagination:
                return
            user_session_key = self._get_user_session_key(event)
            if user_session_key in self.user_sessions and 'results' in self.user_sessions[user_session_key]:
                session_data = self.user_sessions[user_session_key]
                results = session_data.get('results')
                if not results:
                    return
                current_page = session_data.get('current_page', 1)
                total_pages = session_data.get('total_pages', 1)
                if total_pages <= 1:
                    return
                keyword = session_data.get('keyword', '')
                is_full_network = session_data.get('is_full_network', False)
                pan_type = session_data.get('pan_type', 0)
                if current_page > 1:
                    yield event.plain_result(self._format_reply_with_mention(event, "⏳ 正在翻页，请稍候..."))
                    previous_page = current_page - 1
                    self.user_sessions[user_session_key]['current_page'] = previous_page
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
        try:
            if not self.enable_transfer:
                yield event.plain_result(self.messages['transfer_disabled'])
                return
            if not self.api_key:
                yield event.plain_result(self.messages['api_key_required'])
                return
            user_session_key = self._get_user_session_key(event)
            match = re.search(r"转存(\d+)", event.get_message_content())
            if not match:
                yield event.plain_result(self.messages['invalid_transfer_command'])
                return
            resource_index = int(match.group(1))
            if user_session_key not in self.user_sessions or 'results' not in self.user_sessions[user_session_key]:
                yield event.plain_result(self.messages['no_search_for_transfer'])
                return
            session_data = self.user_sessions[user_session_key]
            current_page = session_data.get('current_page', 1)
            results = session_data['results']
            result_list = []
            if isinstance(results, str) and session_data.get('is_sse', False):
                parsed_data = self._parse_sse_response(
                    results, session_data.get('keyword', ''), current_page)
                if isinstance(parsed_data, list):
                    result_list = parsed_data
            elif isinstance(results, dict):
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
            page_size = 5
            start_index = (current_page - 1) * page_size
            if resource_index < 1 or resource_index > len(result_list):
                yield event.plain_result(self.messages['invalid_resource_index'].format(len(result_list)))
                return
            target_resource = result_list[start_index + resource_index - 1]
            url = target_resource.get('url', '')
            title = target_resource.get('title', '未知标题')
            if not url:
                yield event.plain_result(self.messages['no_valid_link'])
                return
            supported_domains = ['pan.quark.cn', 'www.alipan.com', 'www.aliyundrive.com',
                                 'pan.baidu.com', 'drive.uc.cn', 'fast.uc.cn', 'pan.xunlei.com']
            is_supported = False
            lower_url = url.lower()
            for domain in supported_domains:
                if domain in lower_url:
                    is_supported = True
                    break
            if not is_supported:
                yield event.plain_result("❌ 暂不支持该网盘的转存功能")
                return
            yield event.plain_result(self.messages['transferring'].format(title))
            await asyncio.sleep(self.transfer_delay)
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
        self._check_license()
        if not keyword or not keyword.strip():
            return self.messages['empty_keyword']
        if len(keyword) > 50:
            return self.messages['keyword_too_long']
        retry_count = 0
        while retry_count < self.max_retries:
            try:
                if is_full_network:
                    url = f"{self.base_url}/api/other/all_search"
                    params = {
                        "title": keyword
                    }
                else:
                    url = f"{self.base_url}/api/other/web_search"
                    params = {
                        "title": keyword,
                        "is_type": pan_type,
                        "is_show": 1
                    }
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                    'Accept': 'text/event-stream',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                    'Referer': 'https://aliso.vip/',
                    'Origin': 'https://aliso.vip'
                }
                if self.api_key:
                    headers['Authorization'] = f'Bearer {self.api_key}'
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=self.search_timeout)) as response:
                        if response.status == 200:
                            try:
                                content_type = response.headers.get('content-type', '')
                                if 'text/event-stream' in content_type:
                                    text = await response.text()
                                    parsed_data = self._parse_sse_response(text, keyword, page)
                                    self.user_sessions[user_session_key] = {
                                        'results': text,
                                        'keyword': keyword,
                                        'is_full_network': is_full_network,
                                        'pan_type': pan_type,
                                        'current_page': page,
                                        'total_pages': 1,
                                        'is_sse': True
                                    }
                                    return await self._format_search_results(user_session_key, parsed_data, keyword, is_full_network, page)
                                else:
                                    data = await response.json()
                                    self.user_sessions[user_session_key] = {
                                        'results': data,
                                        'keyword': keyword,
                                        'is_full_network': is_full_network,
                                        'pan_type': pan_type,
                                        'current_page': page,
                                        'total_pages': 1,
                                        'is_sse': False
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
                            await asyncio.sleep(2 ** retry_count)
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
                await asyncio.sleep(2 ** retry_count)
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
        self._check_license()
        try:
            url = f"{self.base_url}/api/search/index"
            params = {
                "title": keyword,
                "page": 1,
                "page_size": 10
            }
            logger.info(f"正在本地数据库查找: {keyword}")
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('code') == 200 and data.get('data'):
                            result_data = data['data']
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
                            result_text = f"🔍 本地数据库找到 {len(results)} 个相关资源：\n\n"
                            for i, item in enumerate(results, 1):
                                title = item.get('title', '未知标题')
                                url_link = item.get('url', '')
                                is_time = item.get('is_time', 0)
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
                                if is_time == 1:
                                    result_text += f"{i}. 【{pan_type_name}】{title}\n🌐 链接: {url_link}\n\n"
                                else:
                                    result_text += f"{i}. 【{pan_type_name}】{title}\n🔗 链接: {url_link}\n\n"
                            result_text += "─────────────\n"
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
            logger.warning(f"本地搜索超时（可能数据库无结果）: {keyword}")
            return f"❌ 本地数据库未找到相关资源: {keyword}\n\n💡 提示：可以尝试使用【搜{keyword}】进行全网搜索"
        except Exception as e:
            logger.error(f"本地搜索失败: {str(e)}")
            return "❌ 搜索失败，请稍后重试"
    async def _full_network_search(self, keyword: str) -> str:
        self._check_license()
        if not keyword or not keyword.strip():
            return self.messages['empty_keyword']
        results = []
        success_count = 0
        for pan_name, pan_type in self.search_types.items():
            try:
                dummy_key = "dummy"
                result = await self._search_resources(dummy_key, keyword, is_full_network=False, pan_type=pan_type)
                if self.messages['no_resources_found'].format('') not in result and self.messages['search_failed'] not in result and self.messages['search_service_unavailable_temporarily'] not in result:
                    results.append(f"【{pan_name}网盘】\n{result}")
                    success_count += 1
                elif self.messages['search_service_unavailable_temporarily'] in result:
                    return result
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"搜索{pan_name}网盘时发生错误: {str(e)}")
                continue
        if success_count == 0:
            return self.messages['no_resources_found'].format(keyword)
        return self.messages['full_network_search_results'].format(keyword) + "\n\n".join(results)
    async def _format_search_results(self, user_session_key: str, data, keyword: str, is_full_network: bool, page: int = 1) -> str:
        try:
            result_list = []
            if isinstance(data, list):
                result_list = data
            elif isinstance(data, dict):
                if 'result' in data:
                    result_list = data['result']
                elif 'data' in data:
                    result_list = data['data']
                elif 'list' in data:
                    result_list = data['list']
                elif 'code' in data and data.get('code') == 0 and 'data' in data:
                    result_list = data['data']
            elif isinstance(data, str):
                if user_session_key in self.user_sessions:
                    session_data = self.user_sessions[user_session_key]
                    if session_data.get('is_sse', False):
                        lines = data.strip().split('\n')
                        data_lines = [
                            line for line in lines if line.startswith('data:')]
                        json_data_list = []
                        for data_line in data_lines:
                            json_str = data_line[5:].strip()
                            if json_str and json_str != '[DONE]':
                                import json
                                try:
                                    json_data = json.loads(json_str)
                                    json_data_list.append(json_data)
                                except json.JSONDecodeError:
                                    continue
                        combined_data = []
                        for json_data in json_data_list:
                            if isinstance(json_data, dict):
                                if 'url' in json_data:
                                    combined_data.append(json_data)
                                elif 'data' in json_data and isinstance(json_data['data'], list):
                                    combined_data.extend(json_data['data'])
                                else:
                                    combined_data.append(json_data)
                            elif isinstance(json_data, list):
                                combined_data.extend(json_data)
                        result_list = combined_data
            else:
                return self.messages['no_resources_found'].format(keyword)
            if not result_list:
                return self.messages['no_resources_found'].format(keyword)
            page_size = self.results_per_page
            total_results = len(result_list)
            total_pages = (total_results + page_size - 1) // page_size
            if user_session_key in self.user_sessions:
                self.user_sessions[user_session_key]['total_pages'] = total_pages
            if page < 1 or page > total_pages:
                return self.messages['invalid_page_number'].format(total_pages)
            start_index = (page - 1) * page_size
            end_index = min(start_index + page_size, total_results)
            current_page_results = result_list[start_index:end_index]
            formatted_results = await self._transfer_and_format_results(current_page_results, start_index)
            result_text = self.messages['search_results_header'].format(
                total_results) + '\n\n' + '\n\n'.join(formatted_results)
            result_text += '\n\n' + self.messages['search_results_separator'] + '\n' + self.messages['search_results_footer'] + '\n' + self.messages['search_results_separator_footer']
            result_text += '\n' + self.messages['search_results_website_promo'].format(self.base_url) + '\n' + self.messages['search_results_separator_footer']
            if self.enable_pagination:
                result_text += '\n' + self.messages['search_results_page_info'].format(
                    page, total_pages) + '\n' + self.messages['search_results_navigation']
            return result_text
        except Exception as e:
            logger.error(f"格式化搜索结果时发生错误: {str(e)}")
            return self.messages['format_search_error']
    async def _transfer_and_format_results(self, results: list, start_index: int) -> list:
        if not self.enable_transfer or not self.api_key:
            return self._format_results_without_transfer(results, start_index)
        pan_types_needed = set()
        for item in results:
            url = item.get('url', '')
            if url:
                pan_type = self._identify_pan_type(url)
                if pan_type in ['quark', 'baidu', 'uc', 'xunlei', 'ali']:
                    pan_types_needed.add(pan_type)
        await self._prefetch_cookies(pan_types_needed)
        tasks = []
        for i, item in enumerate(results):
            global_index = start_index + i + 1
            title = item.get('title', self.messages['resource_title_default'])
            url = item.get('url', '')
            if url:
                pan_type = self._identify_pan_type(url)
                if pan_type in ['quark', 'baidu', 'uc', 'xunlei', 'ali']:
                    task = self._transfer_single_resource(global_index, title, url)
                    tasks.append(task)
                else:
                    tasks.append(self._format_single_result(global_index, title, url, None))
            else:
                tasks.append(self._format_single_result(global_index, title, '', None))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        formatted = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"转存任务异常: {str(result)}")
                formatted.append("⚠️ 处理失败")
            else:
                formatted.append(result)
        return formatted
    async def _transfer_single_resource(self, index: int, title: str, url: str) -> str:
        try:
            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            pwd_code = query_params.get('pwd', [''])[0]
            transfer_result = await self._transfer_and_share(url, pwd_code)
            if transfer_result['success']:
                new_title = transfer_result['title']
                new_url = transfer_result['share_url']
                return f"{index}. {new_title}\n✅ 链接: {new_url}"
            else:
                return f"{index}. {title}\n❌ 转存失败，请尝试搜索其他网盘"
        except Exception as e:
            logger.error(f"转存资源失败: {str(e)}")
            return f"{index}. {title}\n❌ 转存失败，请尝试搜索其他网盘"
    async def _format_single_result(self, index: int, title: str, url: str, transfer_result) -> str:
        if url:
            return f"{index}. {title}\n链接: {url}"
        return f"{index}. {title}"
    def _format_results_without_transfer(self, results: list, start_index: int) -> list:
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
        domain_patterns = {
            'quark': ['pan.quark.cn'],
            'ali': ['www.alipan.com', 'www.aliyundrive.com'],
            'baidu': ['pan.baidu.com'],
            'uc': ['drive.uc.cn', 'fast.uc.cn'],
            'xunlei': ['pan.xunlei.com']
        }
        lower_url = url.lower()
        for pan_type, patterns in domain_patterns.items():
            for pattern in patterns:
                if pattern in lower_url:
                    return pan_type
        return 'quark'
    async def _prefetch_cookies(self, pan_types: set):
        tasks = []
        for pan_type in pan_types:
            cache_entry = self._cookie_cache.get(pan_type)
            if not cache_entry or (time.time() - cache_entry[1] > 300):
                tasks.append(self._get_actual_cookie_value(pan_type))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    async def _get_actual_cookie_value(self, pan_type: str) -> str:
        cache_entry = self._cookie_cache.get(pan_type)
        if cache_entry and (time.time() - cache_entry[1] < 300):
            return cache_entry[0]
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
                api_url = f"{self.base_url}/api/GetCookie/{api_endpoints[pan_type]}"
                async with session.get(
                    api_url,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get('code') == 200:
                            cookie_key = f"{pan_type}_cookie"
                            actual_cookie = result.get('data', {}).get(cookie_key, "")
                            if actual_cookie:
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
        try:
            lines = text.strip().split('\n')
            data_lines = [line for line in lines if line.startswith('data:')]
            json_data_list = []
            for data_line in data_lines:
                json_str = data_line[5:].strip()
                if json_str and json_str != '[DONE]':
                    import json
                    try:
                        json_data = json.loads(json_str)
                        json_data_list.append(json_data)
                    except json.JSONDecodeError:
                        continue
            if not json_data_list:
                return []
            combined_data = []
            for json_data in json_data_list:
                if isinstance(json_data, dict):
                    if 'url' in json_data:
                        combined_data.append(json_data)
                    elif 'data' in json_data and isinstance(json_data['data'], list):
                        combined_data.extend(json_data['data'])
                    else:
                        combined_data.append(json_data)
                elif isinstance(json_data, list):
                    combined_data.extend(json_data)
            return combined_data
        except Exception as e:
            logger.error(f"解析SSE响应时发生错误: {str(e)}")
            return []
    async def _transfer_and_share(self, url: str, code: str = "") -> dict:
        try:
            api_key = self.api_key
            if not api_key:
                return {'success': False, 'error': self.messages['api_key_not_configured']}
            pan_type = self._identify_pan_type(url)
            actual_cookie = ""
            cache_entry = self._cookie_cache.get(pan_type)
            if cache_entry and (time.time() - cache_entry[1] < 300):
                actual_cookie = cache_entry[0]
            else:
                cookie_status = await self._get_cookie_from_database(pan_type)
                if not cookie_status:
                    logger.warning(f"❌ 获取{pan_type}网盘Cookie失败")
                    return {'success': False, 'error': '抱歉，cookie过期，请联系群主！'}
                else:
                    actual_cookie = await self._get_actual_cookie_value(pan_type)
                    if not actual_cookie:
                        logger.warning(f"❌ 获取{pan_type}网盘Cookie失败")
                        return {'success': False, 'error': '抱歉，cookie过期，请联系群主！'}
            transfer_data = {
                'url': url,
                'code': code,
                'expired_type': 2,
                'isType': 0,
                'api_key': api_key,
                'isSave': 1
            }
            headers = {
                'Content-Type': 'application/json'
            }
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
        logger.info("心悦搜索机器人插件已终止")
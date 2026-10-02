"""AstrBot 今日运势插件 (GoodLuck).

为用户生成一份确定性的"今日运势"：分数、等级、宜做、忌做、幸运数字/颜色/方位。

设计要点：
- 同一个用户在同一天内多次查询，结果完全一致（种子 = 用户标识 + 日期）。
- 完全本地计算，不依赖任何第三方库、不调用 LLM、无需 API Key。
"""

import hashlib
import random
from datetime import datetime

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

# ---------------------------------------------------------------------------
# 等级划分：按分数从高到低匹配，首个满足 score >= threshold 的即为该等级。
# 注意：全文件不使用 emoji。部分客户端（如 QQ/NapCat）的字体缺少 emoji 字形，
# 会把 emoji 渲染成豆腐块，因此统一改用纯文字与 CJK 标点。
# ---------------------------------------------------------------------------
LUCK_LEVELS = [
    (90, "大吉", "运气爆棚！今天你就是天选之人，想到什么就大胆去做吧。"),
    (75, "中吉", "整体顺遂，做事比较有手感，适合推进计划中的事情。"),
    (60, "小吉", "平稳偏上的一天，稳稳当当做事就会有不错的收获。"),
    (40, "平", "平平淡淡才是真，按部就班、不折腾就是最好的策略。"),
    (20, "小凶", "稍有波折，遇事多留个心眼，凡事缓一缓再决定。"),
    (0, "大凶", "今天运势低迷，宜苟住、宜摸鱼、宜早点睡觉。"),
]

# 宜：适合做的事情。
GOOD_ACTS = [
    "出门散步晒晒太阳",
    "和朋友聊聊天",
    "整理房间和桌面",
    "学习一项新技能",
    "完成拖延已久的小事",
    "尝试一家没吃过的店",
    "给家人打个电话",
    "早睡早起养精神",
    "做一次运动出一身汗",
    "看书或者看电影",
    "复盘最近的工作与生活",
    "写写日记记录心情",
    "收拾一下自己的形象",
    "开启一个新计划",
    "给自己买一杯喜欢的饮料",
    "处理积压的待办事项",
    "向喜欢的人表达心意",
    "来一场说走就走的短途出行",
    "认真吃一顿好饭",
    "把手机放下去发会儿呆",
    "主动夸奖身边的人",
    "整理手机相册和文件",
]

# 忌：不适合做的事情。
BAD_ACTS = [
    "熬夜追剧打游戏",
    "冲动消费买大件",
    "和人争吵较劲",
    "做重大决定",
    "借钱给别人或者借钱",
    "空腹猛灌咖啡奶茶",
    "反复纠结过去的事",
    "轻信陌生人的承诺",
    "在情绪上头时发消息",
    "赌运气做投机的事",
    "把工作拖到最后一刻",
    "点一份超辣的外卖",
    "边走路边刷手机",
    "在群里口嗨抬杠",
    "临时改行程放人鸽子",
    "一次性清空存款",
    "跟风参与不熟悉的项目",
    "深夜emo发朋友圈",
    "把话说得太满",
    "硬撑着不休息",
]

# 今日建议：一句话总结。
ADVICES = [    "慢慢来，比较快。",
    "把注意力放回自己身上。",
    "少想一点，多做一点。",
    "该拒绝的时候就拒绝。",
    "先完成，再完美。",
    "保持好奇心，好运藏在细节里。",
    "别急，机会留给有准备的人。",
    "今天也要好好吃饭、好好睡觉。",
    "把复杂的事情拆成小事来做。",
    "多喝水，多抬头看看天。",
    "相信自己的判断，不用太在意别人的眼光。",
    "允许自己什么都不做，这也是一种充电。",
]

# 幸运颜色候选。
LUCKY_COLORS = [
    "白色",
    "黑色",
    "灰色",
    "红色",
    "橙色",
    "黄色",
    "绿色",
    "青色",
    "蓝色",
    "紫色",
    "粉色",
    "棕色",
    "米白",
    "雾霾蓝",
]

LUCKY_DIRECTIONS = [
    "正东",
    "正南",
    "正西",
    "正北",
    "东南",
    "西南",
    "东北",
    "西北",
]

GOOD_HOURS = [
    "7:00 - 9:00",
    "9:00 - 11:00",
    "11:00 - 13:00",
    "13:00 - 15:00",
    "15:00 - 17:00",
    "17:00 - 19:00",
    "19:00 - 21:00",
    "21:00 - 23:00",
]


class GoodLuckPlugin(Star):
    """今日运势插件：一条指令，看透今天。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        """初始化插件。

        Args:
            context: AstrBot 传入的插件上下文。
            config: 由 ``_conf_schema.json`` 解析出的插件配置，可能为 None。
        """
        super().__init__(context)
        self.config = config or {}

    def _daily_rng(self, seed_text: str) -> random.Random:
        """构造“当天”的随机数发生器。

        种子由 ``用户标识 + 当日日期`` 经 sha256 派生，保证：
        同一用户在同一自然日内多次调用得到完全一致的结果。

        Args:
            seed_text: 用户唯一标识（或退化后的会话标识）。

        Returns:
            已固定种子的 ``random.Random`` 实例。
        """
        day = datetime.now().strftime("%Y-%m-%d")
        digest = hashlib.sha256(f"goodluck::{seed_text}::{day}".encode()).digest()
        return random.Random(int.from_bytes(digest, "big"))

    def _score_bar(self, score: int) -> str:
        """把 0~100 的分数渲染成 20 格的进度条。

        Args:
            score: 运势分数，取值 0~100。

        Returns:
            形如 ``████████████░░░░░░░░`` 的字符串。
        """
        filled = max(1, min(20, round(score / 5)))
        return "█" * filled + "░" * (20 - filled)

    async def _render_fortune(self, event: AstrMessageEvent) -> tuple[str, str]:
        """生成一条完整的今日运势文本。

        Args:
            event: 当前消息事件，用于取用户标识与昵称。

        Returns:
            二元组 ``(运势文本, 发送者 ID)``。
        """
        # 部分平台 sender_id 可能为空，此时退化用 unified_msg_origin，保证种子稳定。
        sender_id = event.get_sender_id()
        rng = self._daily_rng(sender_id or event.unified_msg_origin)

        # 依次抽取各类数据：顺序固定，因此同一用户当天结果可复现。
        score = rng.randint(0, 100)
        # LUCK_LEVELS 按阈值降序排列，取第一个满足 score >= threshold 的等级。
        for threshold, name, comment in LUCK_LEVELS:
            if score >= threshold:
                level_name, level_comment = name, comment
                break
        goods = rng.sample(GOOD_ACTS, 3)
        bads = rng.sample(BAD_ACTS, 3)
        advice = rng.choice(ADVICES)
        lucky_number = rng.randint(0, 99)
        lucky_color = rng.choice(LUCKY_COLORS)
        lucky_direction = rng.choice(LUCKY_DIRECTIONS)
        good_hour = rng.choice(GOOD_HOURS)

        now = datetime.now()
        name = event.get_sender_name() or "你"
        good_lines = "\n".join(f"   ✓ {item}" for item in goods)
        bad_lines = "\n".join(f"   ✗ {item}" for item in bads)

        # 纯文字模板：不使用 emoji，避免客户端字体缺字形时显示成豆腐块。
        return (
            f"【今日运势】{now:%Y年%m月%d日}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"用户：{name}\n"
            f"运势指数：{score} / 100  [{level_name}]\n"
            f"   {self._score_bar(score)}\n"
            f"{level_comment}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"【今日宜】\n{good_lines}\n"
            f"【今日忌】\n{bad_lines}\n"
            f"今日建议：{advice}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"幸运数字：{lucky_number}\n"
            f"幸运颜色：{lucky_color}\n"
            f"幸运方位：{lucky_direction}\n"
            f"黄金时段：{good_hour}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"每日一测，结果当天固定，明天再来看看吧~"
        ), sender_id

    @filter.command("今日运势", alias={"今日运气", "gl"})
    async def today_fortune(self, event: AstrMessageEvent):
        """查看你今天的运势（分数 / 宜忌 / 幸运数字颜色方位）。

        同一天内重复查询，结果保持一致。

        Args:
            event: 当前消息事件。

        Yields:
            一条包含完整运势的纯文本消息。
        """
        try:
            text, sender_id = await self._render_fortune(event)
        except Exception as exc:  # noqa: BLE001 - 兜底，避免单次异常影响插件整体
            logger.error(f"goodluck: 生成今日运势失败: {exc}", exc_info=True)
            yield event.plain_result("今天的运势正在路上，等会儿再来试试吧~")
            return

        reply_with_at = self.config.get("reply_with_at", True)
        if event.get_group_id() and reply_with_at and sender_id:
            yield event.chain_result([Comp.At(qq=sender_id), Comp.Plain(f"\n{text}")])
        else:
            yield event.plain_result(text)

        # 阻止事件继续传播，避免再被 LLM 等后续流程处理一遍。
        event.stop_event()

    async def terminate(self):
        """插件被卸载或禁用时调用，此处无需清理任何资源。"""
        return None

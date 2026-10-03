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
# 每条点评都写成两句：前半句交代今天的状态，后半句给一句能照着做的建议。
# 注意：全文件不使用 emoji。部分客户端（如 QQ/NapCat）的字体缺少 emoji 字形，
# 会把 emoji 渲染成豆腐块，因此统一改用纯文字与 CJK 标点。
# ---------------------------------------------------------------------------
LUCK_LEVELS = [
    (
        90,
        "大吉",
        "运气爆棚，今天你就是天选之人。想做的事尽管放手去做，"
        "胆子可以比平时大一点，很多平时推不动的环节今天会走得意外顺畅。",
    ),
    (
        75,
        "中吉",
        "整体顺遂，做事有手感，适合推进计划里一直想做却还没开始的事。"
        "与人沟通会比较顺畅，开口之前不用反复打腹稿。",
    ),
    (
        60,
        "小吉",
        "平稳偏上的一天，稳稳当当做事就会有不错的收获。"
        "不适合冒险，但把该做的做完，晚上回头看会很踏实。",
    ),
    (
        40,
        "平",
        "平平淡淡才是真，按部就班、不折腾就是今天最好的策略。"
        "没有什么特别好的，也没有什么特别坏的，把节奏稳住就行。",
    ),
    (
        20,
        "小凶",
        "稍有波折，遇事多留个心眼，凡事缓一缓再决定。"
        "今天容易被小意外打乱节奏，重要的事尽量挪到明天。",
    ),
    (
        0,
        "大凶",
        "今天运势低迷，宜苟住、宜摸鱼、宜早点睡觉。"
        "别硬着头皮做决定，也别跟自己较劲，把今天当成充电的一天就好。",
    ),
]

# 宜：适合做的事情。写得具体一点，用户看完就知道该怎么落地。
GOOD_ACTS = [
    "出门散步晒晒太阳，顺便把脑子放空",
    "约上朋友聊聊天，能见面就别只发消息",
    "把房间和桌面收拾干净，运气偏爱整洁的地方",
    "学一项新技能，哪怕今天只学个开头",
    "把拖延很久的那件小事一口气做完",
    "去一家没吃过的店，点一道平时不会点的菜",
    "给家里人打个电话，随便聊两句都好",
    "早睡早起，把精神养回来",
    "认真运动一次，出一身汗",
    "看一本书或者一部电影，中途别看手机",
    "复盘最近的工作和生活，写下来会更清楚",
    "写写日记，把心里堆着的东西倒出来",
    "收拾一下自己的形象，剪个头发也好",
    "开启一个新计划，先把第一步迈出去",
    "给自己买一杯喜欢的饮料",
    "把积压的待办清单清一清",
    "向喜欢的人表达心意，别再自己猜了",
    "来一场说走就走的短途出行",
    "认真吃一顿好饭，不赶时间的那种",
    "把手机放下去发会儿呆",
    "主动夸奖身边的人，别吝啬那几句话",
    "整理手机相册和文件，把没用的删掉",
    "把衣柜里一年没穿过的衣服处理掉",
    "去菜市场或者超市逛逛，看看新鲜的东西",
    "早起一次，看看清晨的街道是什么样",
    "和许久没联系的老朋友叙叙旧",
    "把一直想读的那本书翻开，先读两页",
    "认真听别人把话说完，别急着插嘴",
    "学一道新菜谱，给自己做顿饭",
    "把电脑桌面和下载文件夹归类整理",
    "去公园或者江边走走，吹吹风",
    "给自己定一个本周就能完成的小目标",
    "把闹钟调早十分钟，出门从容一点",
    "练习一次只做一件事，不中途切来切去",
    "把想说的话在脑子里组织好再说出口",
    "把攒下的零钱存起来，看着数字变大",
    "试着拒绝一件自己不想做的事",
    "泡一杯热茶，慢慢喝完",
    "把今天遇到的一件小事记下来",
    "把一直想学的那首歌学会",
    "给房间添一盆好养的绿植",
    "把收藏夹里存着的文章翻出来看看",
]

# 忌：不适合做的事情。每条都带上原因，比单纯说"不要做"更容易被记住。
BAD_ACTS = [
    "熬夜追剧打游戏，明天一定会后悔",
    "冲动消费买大件，先丢进购物车放一夜",
    "和人争吵较劲，赢了也伤感情",
    "做重大决定，今天不适合拍板",
    "借钱给别人，或者开口找人借钱",
    "空腹猛灌咖啡奶茶，胃会抗议",
    "反复纠结已经过去的事",
    "轻信陌生人的承诺，尤其是要你掏钱的",
    "在情绪上头的时候发消息",
    "赌运气做投机的事，别加杠杆",
    "把工作拖到最后一刻才动手",
    "点一份超辣的外卖挑战自己的胃",
    "边走路边刷手机",
    "在群里口嗨抬杠，容易引战",
    "临时改行程放人鸽子",
    "一次性把存款清空",
    "跟风参与完全陌生的项目",
    "深夜发伤感朋友圈，第二天会想删",
    "把话说得太满，给自己留点余地",
    "硬撑着不休息，身体会记账",
    "为了合群去参加不想去的局",
    "在气头上做需要长期承担的决定",
    "过度承诺自己其实做不到的事",
    "拿别人的标准衡量自己的生活",
    "把情绪发泄在最亲近的人身上",
    "通宵赶工，效率其实并不高",
    "一边吃饭一边处理工作",
    "为了省小钱最后花了大钱",
    "在公开场合揭别人的短",
    "反复刷手机看有没有新消息",
    "把日程排得太满，一点缓冲都不留",
    "在困得睁不开眼的时候硬撑",
    "因为一次失误就全盘否定自己",
    "和别人比较工资、房子和感情",
    "把重要的事交给自己不放心的人",
    "相信天上掉馅饼的好事",
]

# 今日建议：一句话总结。语气偏温和，不对用户下命令。
ADVICES = [
    "慢慢来，比较快。",
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
    "情绪来了先放一放，等它自己过去。",
    "把能做好的那件小事做到最好。",
    "别用别人的节奏打乱自己的步伐。",
    "累了就休息，不用给自己找理由。",
    "今天做的每件小事，都在给明天铺路。",
    "与其焦虑未来，不如把眼前这步走稳。",
    "对人真诚一点，对自己宽容一点。",
    "想不通的事就先不想，睡一觉再说。",
    "把时间花在能积累的事情上。",
    "遇到问题先问自己：最坏能坏到哪去。",
    "把今天过好，就是对明天最好的准备。",
    "不必事事都赢，留点力气给重要的事。",
    "说出口的话，先在心里过一遍。",
    "别怕慢，怕的是原地打转。",
    "认真对待每一个小的承诺。",
    "给自己留一点没有安排的时间。",
    "心情不好的时候，先照顾身体。",
    "你不需要向任何人证明什么。",
]

# 幸运颜色候选：常见颜色打底，掺一些传统色名，读起来更有味道。
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
    "月白",
    "竹青",
    "胭脂",
    "黛蓝",
    "杏黄",
    "藕荷",
]

# 幸运方位候选：八个方位已经穷尽，不再增删。
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

# 黄金时段候选：只取 7:00 之后的时段，避免把半夜推给用户；
# 带上十二时辰的名字，和整体运势的调性更搭。
GOOD_HOURS = [
    "辰时（7:00 - 9:00）",
    "巳时（9:00 - 11:00）",
    "午时（11:00 - 13:00）",
    "未时（13:00 - 15:00）",
    "申时（15:00 - 17:00）",
    "酉时（17:00 - 19:00）",
    "戌时（19:00 - 21:00）",
    "亥时（21:00 - 23:00）",
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

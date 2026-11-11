"""LinguaCraft —— AI 英语剧本生成器（流式打字机 + 动效 + 卡片排版版）"""
import hashlib
import html as html_lib
import json
import os
import re
import textwrap
from datetime import datetime
from urllib.parse import quote

import requests
import streamlit as st

try:
    from audio_recorder_streamlit import audio_recorder
    HAS_RECORDER = True
except ImportError:  # 未安装时不影响其他功能，跟读面板内给出提示
    HAS_RECORDER = False

# ---------- 页面基础配置 ----------
st.set_page_config(
    page_title="LinguaCraft",
    page_icon="🎭",
    layout="centered",
    initial_sidebar_state="expanded",
)

# ---------- 支持的服务商（均为 OpenAI 兼容接口） ----------
PROVIDERS = {
    "DeepSeek（推荐）": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    "智谱 AI": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "key_url": "https://open.bigmodel.cn/usercenter/apikeys",
    },
    "通义千问（阿里）": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "key_url": "https://bailian.console.aliyun.com/",
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_url": "https://platform.openai.com/api-keys",
    },
    "自定义（OpenAI 兼容）": {
        "base_url": "",
        "model": "",
        "key_url": "",
    },
}

LEVEL_PROMPTS = {
    "初级": "CEFR A1-A2 水平：使用最常见的基础词汇和简短句子，语速友好，适合刚开口的学习者",
    "中级": "CEFR B1-B2 水平：日常流利表达，适度使用短语和从句",
    "高级": "CEFR C1-C2 水平：使用地道惯用语、习语和复杂句式，贴近母语者",
}

# ---------- DiceBear 头像（adventurer 动漫风格） ----------
# 参数值均已通过官方 schema.json + API 实测验证
AVATAR_FILE = "avatars.json"  # 角色装扮持久化文件

MALE_HAIRS = [f"short{i:02d}" for i in range(1, 20)]    # 男生发型池
FEMALE_HAIRS = [f"long{i:02d}" for i in range(1, 27)]   # 女生发型池
EYES_ALL = [f"variant{i:02d}" for i in range(1, 27)]
MOUTH_ALL = [f"variant{i:02d}" for i in range(1, 31)]
BROWS_ALL = [f"variant{i:02d}" for i in range(1, 16)]
GLASSES_ALL = [f"variant{i:02d}" for i in range(1, 6)]
FEATURES_ALL = ["freckles", "blush", "birthmark", "mustache"]

HAIR_COLOR_PALETTE = {  # 发色色板（hex 不带 #）
    "黑色": "2f2a26", "深棕": "5a3d2b", "栗棕": "8a5a2b", "亚麻金": "d9b380",
    "浅金": "ecd9a8", "银白": "dcdce4", "樱粉": "f0a8c4", "天蓝": "7aa5e0",
    "薄荷绿": "8fd0b2", "香芋紫": "a88fe0", "红棕": "b45a4a",
}
SKIN_COLOR_PALETTE = {  # 肤色色板
    "白皙": "ffe6d3", "自然": "f8d9b9", "小麦": "eec1a2",
    "古铜": "d19a6b", "深肤色": "a97b5c",
}
FEATURE_LABELS = {"freckles": "雀斑", "blush": "腮红", "birthmark": "痣", "mustache": "小胡子"}


def load_avatars() -> dict:
    """读取本地保存的角色装扮 {名字: 配置}。"""
    if os.path.exists(AVATAR_FILE):
        try:
            with open(AVATAR_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001  文件损坏时从空开始
            return {}
    return {}


def save_avatars(data: dict) -> None:
    with open(AVATAR_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def build_avatar_url(cfg: dict) -> str:
    """根据装扮配置拼 DiceBear adventurer URL。"""
    params = {
        "seed": "linguacraft",
        "hair": cfg["hair"],
        "hairColor": HAIR_COLOR_PALETTE[cfg["hair_color"]],
        "skinColor": SKIN_COLOR_PALETTE[cfg["skin"]],
        "eyes": cfg["eyes"],
        "mouth": cfg["mouth"],
    }
    if cfg["eyebrows"]:
        params["eyebrows"] = cfg["eyebrows"]
        params["eyebrowsProbability"] = "100"
    if cfg["glasses"]:
        params["glasses"] = cfg["glasses"]
        params["glassesProbability"] = "100"
    if cfg["feature"]:
        params["features"] = cfg["feature"]
        params["featuresProbability"] = "100"
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"https://api.dicebear.com/7.x/adventurer/svg?{query}"


def describe_avatar(cfg: dict) -> str:
    """把装扮配置转为自然语言人物描述，供剧本生成使用。"""
    parts = [cfg["hair_color"] + ("长发" if cfg["hair"].startswith("long") else "短发") + "的" + cfg["gender"]]
    if cfg["glasses"]:
        parts.append("戴眼镜")
    if cfg["feature"]:
        parts.append("有" + FEATURE_LABELS[cfg["feature"]])
    return "，".join(parts)


def _random_avatar() -> None:
    """「🎲 随机捏人」回调：在下次运行前写入随机装扮（on_click 时机早于控件创建，安全）。"""
    import random

    ss = st.session_state
    pool = MALE_HAIRS if ss.get("av_gender", "男生") == "男生" else FEMALE_HAIRS
    ss.av_hair = random.choice(pool)
    ss.av_hair_color = random.choice(list(HAIR_COLOR_PALETTE))
    ss.av_eyes = random.choice(EYES_ALL)
    ss.av_mouth = random.choice(MOUTH_ALL)
    ss.av_eyebrows = random.choice(["默认"] + BROWS_ALL)
    ss.av_glasses = random.choice(["无"] + GLASSES_ALL)
    ss.av_feature = random.choice(["无"] + FEATURES_ALL)
    ss.av_skin = random.choice(list(SKIN_COLOR_PALETTE))


def _load_avatar_cfg(cfg: dict) -> None:
    """「📂 加载装扮」回调：把已保存角色的装扮回填到各选择器。"""
    ss = st.session_state
    ss.av_gender = cfg["gender"]
    ss.av_hair = cfg["hair"]
    ss.av_hair_color = cfg["hair_color"]
    ss.av_eyes = cfg["eyes"]
    ss.av_mouth = cfg["mouth"]
    ss.av_eyebrows = cfg["eyebrows"] or "默认"
    ss.av_glasses = cfg["glasses"] or "无"
    ss.av_feature = cfg["feature"] or "无"
    ss.av_skin = cfg["skin"]


def _clear_chat() -> None:
    """清空角色扮演对话历史（切角色 / 手动清空 / 生成新剧本时触发）。"""
    st.session_state.pop("chat_history", None)


def _clear_vocab() -> None:
    """清空生词本，并重置剧本中对应勾选控件（del 后 checkbox 回到默认未勾选态）。"""
    ss = st.session_state
    for k in [k for k in list(ss.keys()) if k.startswith("vocab_ck_")]:
        del ss[k]
    ss.pop("vocab", None)


# ---------- 🎧 环境音效沉浸模式（场景关键词 → 环境音） ----------
# 说明：原 Pixabay CDN 直链中「咖啡馆」「白噪音」两条已失效（403），因此改为本地音效文件：
#   ambience/cafe.wav    咖啡馆环境音（本地合成：人声嘈杂底 + 杯碟碰撞）
#   ambience/airport.mp3 机场环境音（原 https://cdn.pixabay.com/audio/2022/01/18/audio_d0a13f69d2.mp3 已下载到本地）
#   ambience/office.wav  办公室白噪音（本地合成：空调低鸣 + 稀疏键盘声）
# 如需换回在线 URL，把 value 改成 "https://...mp3" 即可（st.audio 原生支持 URL）。
SCENE_AUDIO_MAP = {
    # 咖啡馆环境音（原失效链接：https://cdn.pixabay.com/audio/2022/03/15/audio_224c8b8f14.mp3）
    ("星巴克", "咖啡", "点单", "cafe", "coffee"): "ambience/cafe.wav",
    # 机场环境音
    ("机场", "旅行", "airport", "airplane"): "ambience/airport.mp3",
    # 办公室白噪音（原失效链接：https://cdn.pixabay.com/audio/2022/07/14/audio_2414f70803.mp3）
    ("面试", "办公室", "office", "interview"): "ambience/office.wav",
}
SCENE_AUDIO_LABELS = {
    "ambience/cafe.wav": "咖啡馆环境音",
    "ambience/airport.mp3": "机场环境音",
    "ambience/office.wav": "办公室白噪音",
}


def match_scene_audio(scene: str) -> str | None:
    """根据场景名称匹配环境音：遍历 SCENE_AUDIO_MAP，场景包含任一关键词即返回对应音源。

    返回本地文件路径或 URL；音源文件不存在或未匹配到关键词时返回 None（默认不播放）。
    """
    if not scene:
        return None
    for keywords, source in SCENE_AUDIO_MAP.items():
        if any(kw in scene for kw in keywords):
            if source.startswith("http") or os.path.exists(source):
                return source
    return None


# ---------- 在线语音合成（edge-tts，微软神经语音） ----------
TTS_CACHE_DIR = "tts_cache"
TTS_VOICES = ["en-US-AriaNeural", "en-US-GuyNeural"]  # 角色A=Aria（女声），角色B=Guy（男声）


def tts_speak(text: str, voice: str = TTS_VOICES[0]) -> str:
    """用 edge_tts.Communicate 把英文文本合成 MP3 并返回路径；同文本+同声库命中缓存不重复合成。"""
    os.makedirs(TTS_CACHE_DIR, exist_ok=True)
    key = hashlib.md5(f"{voice}|{text}".encode("utf-8")).hexdigest()[:16]
    mp3 = os.path.join(TTS_CACHE_DIR, f"{key}.mp3")
    if os.path.exists(mp3):
        return mp3

    import asyncio

    import edge_tts  # 懒加载：未安装时不影响应用其他功能

    async def _gen() -> None:
        await edge_tts.Communicate(text, voice).save(mp3)

    asyncio.run(_gen())
    return mp3


# ---------- 🖼️ 分享海报合成（Pillow） ----------
POSTER_SIZE = (1080, 1920)          # 统一竖版画布
POSTER_COLOR = "#333333"            # 正文深灰（浅色背景上清晰可读）
POSTER_FOOTER_COLOR = "#999999"     # 页脚浅灰
POSTER_TITLE_CORE = "我的专属AI英语剧本"
POSTER_FOOTER = "Generated by LinguaCraft AI"
POSTER_BG_LABELS = {"bg.png": "🌌 蓝紫星空（默认）", "bg2.png": "📚 阅读时光"}
POSTER_CARD_FILL = (255, 255, 255, 200)  # 半透明白色卡片（RGBA，透明度 200）
POSTER_CARD_MARGIN = 80            # 卡片左右边距
POSTER_CARD_RADIUS = 36            # 卡片圆角
POSTER_CARD_TOP = 380              # 卡片顶部 y
POSTER_CARD_PAD = 44               # 卡片内边距
POSTER_AVATAR_SIZE = 100           # 圆形头像尺寸
POSTER_TEXT_X = 80 + 44 + 100 + 36  # 文本起点 x = 卡片边距 + 内边距 + 头像 + 间距 = 260
EN_WRAP, ZH_WRAP = 40, 25          # textwrap 换行宽度：英文 40 字符 / 中文 25 字
# 英文 30px 时 40 字符最长行约 641px < 696px 文本区可用宽度（34px 会到 736px 超界）
EN_LH, ZH_LH, ZH_GAP, BLOCK_GAP = 46, 38, 14, 48  # 英文/中文行高、中英间距、块间距
# 块高（含 100px 头像）+ 48 间距 → 每句画完到下一句至少推进约 150px，行距舒展
MAX_CARD_BOTTOM = 1500  # 对话卡片下边缘安全线：超出则 5→4→3 回合递减，仍超则自动缩字号
VOCAB_WRAP = 32         # 词汇板块换行宽度（24px 字号下 CJK 约 768px < 848px 可用宽度）


def _load_font(size: int, bold: bool = False):
    """加载中文字体：标题粗体优先 msyhbd.ttc，常规用 msyh.ttc，均失败回退 simhei.ttf。"""
    candidates = (
        ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc",
         "C:/Windows/Fonts/simhei.ttf"] if bold
        else ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"]
    )
    for path in candidates:
        try:
            from PIL import ImageFont
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001  单个字体缺失/损坏时尝试下一个
            continue
    raise RuntimeError("未找到可用的中文字体（尝试过 msyhbd.ttc / msyh.ttc / simhei.ttf）")


def _placeholder_avatar(name: str, size: int = POSTER_AVATAR_SIZE):
    """头像加载失败时的兜底：角色名首字 + 浅蓝圆形。"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size - 1, size - 1), fill=(124, 156, 204, 255))
    f = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", int(size * 0.44))
    ch = (name or "?").strip()[:1] or "?"
    d.text((size / 2, size / 2), ch, font=f, fill="white", anchor="mm")
    return img


_AVATAR_CACHE = {}  # 会话级头像缓存 {(来源, 尺寸): Image}：避免同一头像重复下载/解码


def _load_avatar_image(source, speaker: str = "", size: int = POSTER_AVATAR_SIZE):
    """加载头像并裁成圆形。source 支持两种格式：
    ① 本地文件路径（如 avatar.png）→ PIL.Image.open；
    ② http(s) URL → requests.get 拉取字节流 → io.BytesIO → Image.open
      （DiceBear 的 /svg 接口会自动换成 /png，Pillow 无法直接解码 SVG）。
    加载失败回退首字母圆形头像，不让海报生成中断。"""
    key = (source, size)
    if key in _AVATAR_CACHE:
        return _AVATAR_CACHE[key]

    import io

    from PIL import Image, ImageDraw, ImageOps
    try:
        if isinstance(source, str) and source.startswith(("http://", "https://")):
            if "/svg" in source:
                source = source.replace("/svg", "/png")
            resp = requests.get(source, timeout=15)
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        else:
            img = Image.open(source).convert("RGBA")
        img = ImageOps.fit(img, (size, size), Image.LANCZOS)  # 居中裁成正方形
        # 4 倍尺寸画圆再缩小，圆边更平滑
        mask = Image.new("L", (size * 4, size * 4), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size * 4, size * 4), fill=255)
        img.putalpha(mask.resize((size, size), Image.LANCZOS))
    except Exception:  # noqa: BLE001  网络断开 / 文件不存在 / 格式不支持
        img = _placeholder_avatar(speaker, size)
    _AVATAR_CACHE[key] = img
    return img


def _draw_poster_title(draw, canvas_w: int, y: int) -> None:
    """居中绘制大标题：✨ 用 Windows 彩色 emoji 字体（seguiemj）混排；
    雅黑没有 U+2728 字形，直接画会变成方框。emoji 字体不可用时回退 ★。"""
    f_core = _load_font(50, bold=True)
    segments = []  # [文本, 字体, 是否彩色emoji]
    try:
        from PIL import ImageFont
        f_emoji = ImageFont.truetype("C:/Windows/Fonts/seguiemj.ttf", 50)
        draw.textlength("✨", font=f_emoji)  # 探测 emoji 字体宽度可用性
        segments = [("✨ ", f_emoji, True),
                    (POSTER_TITLE_CORE, f_core, False),
                    (" ✨", f_emoji, True)]
    except Exception:  # noqa: BLE001  无 seguiemj 或渲染失败 → ★ 兜底（雅黑有该字形）
        segments = [("★ ", f_core, False),
                    (POSTER_TITLE_CORE, f_core, False),
                    (" ★", f_core, False)]
    total = sum(draw.textlength(t, font=f) for t, f, _ in segments)
    x = (canvas_w - total) / 2
    for t, f, colored in segments:
        # 锚点用 "la"（左上）：x 由上面的累加逻辑管理，不能再让每段各自居中
        if colored:
            draw.text((x, y), t, font=f, embedded_color=True, anchor="la")
        else:
            draw.text((x, y), t, font=f, fill=POSTER_COLOR, anchor="la")
        x += draw.textlength(t, font=f)


def _draw_emoji_heading(draw, x: int, y: int, emoji: str, text: str,
                        size: int, fill: str = POSTER_COLOR) -> None:
    """左对齐绘制「emoji + 文字」标题：emoji 用彩色字体（如 📚），缺失时省略 emoji 只画文字。"""
    f_core = _load_font(size, bold=True)
    try:
        from PIL import ImageFont
        f_e = ImageFont.truetype("C:/Windows/Fonts/seguiemj.ttf", size)
        draw.textlength(emoji, font=f_e)  # 探测可用性
        pad = draw.textlength(emoji + " ", font=f_e)
        draw.text((x, y), emoji + " ", font=f_e, embedded_color=True, anchor="la")
        draw.text((x + pad, y), text, font=f_core, fill=fill, anchor="la")
    except Exception:  # noqa: BLE001  无彩色 emoji 字体 → 只画文字
        draw.text((x, y), text, font=f_core, fill=fill, anchor="la")


def generate_poster(scene: str, level: str, dialogue: list, avatars: dict = None,
                    vocab: list = None, bg_path: str = "bg.png",
                    out_path: str = "share_card.png") -> str:
    """合成 1080x1920 竖版分享海报并保存，返回输出路径。

    dialogue: [[说话人, 英文, 中文], ...]，取前 4~5 个回合；
    avatars: {说话人: 本地路径或 URL}；vocab: 重点词汇词条列表（最多取 2 条）。
    排版（自上而下）：150px 大标题 → 250px 场景/难度 → 380px 起自适应高度的
    半透明圆角对话卡片 → 卡片下方 50px 词汇板块卡片 → 距底 50px 页脚。
    安全策略：对话卡片下边缘超过 1500px 时按 5→4→3 回合递减，仍超高则自动缩小字号。
    """
    if not os.path.exists(bg_path):
        raise FileNotFoundError(f"背景图不存在：{bg_path}")
    try:
        from PIL import Image, ImageDraw, ImageOps
    except ImportError as e:
        raise RuntimeError("缺少 Pillow 库，请先运行：pip install pillow") from e

    avatars = avatars or {}
    vocab = [v for v in (vocab or []) if v and v.strip()][:2]
    canvas = ImageOps.fit(Image.open(bg_path).convert("RGBA"), POSTER_SIZE)
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size

    f_meta = _load_font(30)
    f_vt = _load_font(30, bold=True)   # 词汇板块标题
    f_vi = _load_font(24)              # 词汇词条（比对话小一号）
    f_foot = _load_font(25)

    # ① 大标题（距顶 150px，居中，粗体 + 彩色 ✨ 混排）
    _draw_poster_title(draw, w, 150)
    # ② 场景 / 难度（距顶 250px，居中）
    draw.text((w / 2, 250), f"场景：{scene} | 难度：{level}", font=f_meta,
              fill=POSTER_COLOR, anchor="ma")

    # ③ 预排版对话：每位说话人的英文/中文分别 textwrap.fill 换行
    #    英文宽度 40、中文宽度 25；textwrap 对无空格的中文默认逐字断行
    rows = [r for r in dialogue[:5] if r and len(r) >= 2 and r[1] and r[1].strip()]

    def _build(sub_rows, scale=1.0):
        """按给定回合数与字号缩放系数排版，返回 blocks 及卡片下边缘。"""
        f_en = _load_font(max(20, round(30 * scale)))
        f_zh = _load_font(max(16, round(26 * scale)))
        en_lh = max(34, round(EN_LH * scale))
        zh_lh = max(28, round(ZH_LH * scale))
        blocks = []
        for row in sub_rows:
            speaker, en = row[0], row[1]
            zh = row[2] if len(row) > 2 else ""  # 兼容缺翻译的行
            en_lines = textwrap.fill(en.strip().strip('“”"'), width=EN_WRAP).splitlines()
            zh_lines = (textwrap.fill(f"（{zh.strip()}）", width=ZH_WRAP).splitlines()
                        if zh and zh.strip() else [])
            text_h = len(en_lines) * en_lh + (ZH_GAP if zh_lines else 0) + len(zh_lines) * zh_lh
            blocks.append({"speaker": speaker, "en": en_lines, "zh": zh_lines,
                           "height": max(POSTER_AVATAR_SIZE, text_h), "text_h": text_h})
        card_y1 = (POSTER_CARD_TOP + POSTER_CARD_PAD * 2
                   + sum(b["height"] for b in blocks) + BLOCK_GAP * max(0, len(blocks) - 1))
        return blocks, card_y1, f_en, f_zh, en_lh, zh_lh

    # 高度自适应 + 安全降级：5→4→3 回合递减；3 回合仍超 1500px 则缩字号（0.85 → 0.75）
    chosen = _build(rows[:5])
    for n in (5, 4, 3):
        chosen = _build(rows[:n])
        if chosen[1] <= MAX_CARD_BOTTOM:
            break
    if chosen[1] > MAX_CARD_BOTTOM:
        for scale in (0.85, 0.75):
            chosen = _build(rows[:3], scale)
            if chosen[1] <= MAX_CARD_BOTTOM:
                break
    blocks, card_y1, f_en, f_zh, en_lh, zh_lh = chosen

    # ④ 对话卡片（半透明圆角，高度按内容自适应）：先画在透明 overlay 再合成
    card_x0, card_x1 = POSTER_CARD_MARGIN, w - POSTER_CARD_MARGIN
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle(
        (card_x0, POSTER_CARD_TOP, card_x1, card_y1), radius=POSTER_CARD_RADIUS,
        fill=POSTER_CARD_FILL)

    # ⑤ 词汇板块卡片：对话卡片正下方留 50px，高度同样自适应
    def _vocab_layout(items):
        lines = [textwrap.fill(v.strip(), width=VOCAB_WRAP).splitlines() for v in items]
        height = (36 * 2 + 44 + 20 + sum(len(ls) * 34 for ls in lines)
                  + 16 * max(0, len(lines) - 1))
        return lines, height

    v_items, vocab_y0, v_lines, vocab_y1 = vocab[:], card_y1 + 50, None, None
    if v_items:
        v_lines, vocab_h = _vocab_layout(v_items)
        while v_items and vocab_y0 + vocab_h > h - 130:  # 不得压住底部签名
            v_items = v_items[:-1]
            v_lines, vocab_h = _vocab_layout(v_items)
        if v_items:
            vocab_y1 = vocab_y0 + vocab_h
            ImageDraw.Draw(overlay).rounded_rectangle(
                (card_x0, vocab_y0, card_x1, vocab_y1),
                radius=POSTER_CARD_RADIUS, fill=POSTER_CARD_FILL)
    canvas = Image.alpha_composite(canvas, overlay)
    draw = ImageDraw.Draw(canvas)

    # ⑥ 逐块绘制对话：圆形头像在左（文本 x=260 已留位），英文在上、中文在下；
    #    每句画完 y 推进「块高 + 48px 间距」（≥148px），行距舒展绝不越界
    y = POSTER_CARD_TOP + POSTER_CARD_PAD
    for b in blocks:
        avatar_y = y + (b["height"] - POSTER_AVATAR_SIZE) // 2
        avatar = _load_avatar_image(avatars.get(b["speaker"]), b["speaker"])
        canvas.paste(avatar, (card_x0 + POSTER_CARD_PAD, avatar_y), avatar)
        ty = y + (b["height"] - b["text_h"]) // 2
        for ln in b["en"]:
            draw.text((POSTER_TEXT_X, ty), ln, font=f_en, fill=POSTER_COLOR, anchor="la")
            ty += en_lh
        ty += ZH_GAP
        for ln in b["zh"]:
            draw.text((POSTER_TEXT_X, ty), ln, font=f_zh, fill=POSTER_COLOR, anchor="la")
            ty += zh_lh
        y += b["height"] + BLOCK_GAP

    # ⑦ 词汇板块内容：📚 标题 + 词条（小一号字体，逐行绘制）
    if v_items and v_lines:
        vx = card_x0 + 36
        vy = vocab_y0 + 36
        _draw_emoji_heading(draw, vx, vy, "📚", "重点词汇", 30)
        vy += 44 + 20
        for lines in v_lines:
            for ln in lines:
                draw.text((vx, vy), ln, font=f_vi, fill=POSTER_COLOR, anchor="la")
                vy += 34
            vy += 16

    # ⑧ 页脚（距底部 50px，绝对底部，浅灰小字）
    draw.text((w / 2, h - 50), POSTER_FOOTER, font=f_foot,
              fill=POSTER_FOOTER_COLOR, anchor="mb")

    canvas.convert("RGB").save(out_path)
    return out_path

SYSTEM_PROMPT = """你是一位专业英语教学剧本编剧。根据用户给定的场景和难度，生成一段英语情景对话剧本。
要求：
1. 对话双方各 5-8 轮，自然贴合场景；
2. 严格按照要求的难度控制用词和句式；
3. 每句英文台词后附中文翻译；
4. 对话结束后，列出 5 个本剧本中的关键表达（英文 + 中文释义 + 一句用法示例）。
输出格式：
【剧本标题】
【人物】
【对话】
A: ... （中文翻译）
B: ... （中文翻译）
【关键表达】
1. ...
"""

RP_SYSTEM_PROMPT = """你是 LinguaCraft 的英语陪练伙伴。下方提供一段已生成的英语情景剧本，用户将扮演剧本中除「{ai_role}」以外的角色，你全程只扮演「{ai_role}」，与用户即兴对戏。
要求：
1. 严格遵守剧本的人物设定、场景与英语难度，不要跳出角色，不要复述剧本原文，也不要输出旁白、标题或解释性文字；
2. 每次回复 1-3 句英文台词，自然推进剧情；英文之后另起一行附上中文翻译；
3. 用户可能用中文或英文发言，你始终以「{ai_role}」的身份用英文回应（必要时先用一句简短英文接住对方的话）；
4. 若用户想结束或转换话题，可以自然收尾，但仍保持角色口吻。"""

# ---------- 会话状态 ----------
if "api_key" not in st.session_state:
    st.session_state.api_key = ""
if "just_done" not in st.session_state:
    st.session_state.just_done = False
if "entered" not in st.session_state:
    st.session_state.entered = False  # 是否已通过欢迎页进入主界面
if "history" not in st.session_state:
    st.session_state.history = []  # 生成历史：[{scene, level, time, result, meta}]，最新在前
if "vocab" not in st.session_state:
    st.session_state.vocab = []  # 生词本：勾选收藏的关键表达（全局累积，跨剧本保留）


# ---------- 流式调用大模型 ----------
def _stream_completion(url: str, headers: dict, payload: dict):
    """POST OpenAI 兼容接口（stream=True），逐块 yield 文本增量；配合 st.write_stream 实现打字机效果。"""
    with requests.post(url, headers=headers, json=payload, stream=True, timeout=(15, 300)) as resp:
        resp.raise_for_status()
        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            # 手动按 UTF-8 解码，避免部分网关不返回 charset 时被误解码
            line = raw_line.decode("utf-8", errors="replace")
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
            content = delta.get("content")
            if content:
                yield content


def stream_llm(base_url: str, model: str, api_key: str, scene: str, level: str, persona: str = ""):
    """生成剧本：构建 messages 后走公共 SSE 流式通道。"""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    user_content = f"场景：{scene}\n难度：{level}（{LEVEL_PROMPTS[level]}）"
    if persona:
        user_content += (
            f"\n我的角色形象：{persona}。"
            "请把我设定为对话中的一方（角色名可自拟，贴合场景），结合形象特点自然融入剧情，"
            "不要生硬介绍外貌。"
        )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.8,
        "stream": True,
    }
    yield from _stream_completion(url, headers, payload)


def stream_chat(base_url: str, model: str, api_key: str,
                script_text: str, history: list, ai_role: str):
    """角色扮演：把整段剧本作为上下文，模型扮演 ai_role 与用户继续对戏（流式）。"""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    messages = [{
        "role": "system",
        "content": RP_SYSTEM_PROMPT.format(ai_role=ai_role or "对方角色")
        + "\n\n【剧本原文】\n" + script_text,
    }]
    # 对话历史：让模型记住之前对戏内容，多轮衔接自然
    for m in history:
        messages.append({"role": m["role"], "content": m["content"]})
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.8,
        "stream": True,
    }
    yield from _stream_completion(url, headers, payload)


# ---------- 剧本解析 ----------
_TR_CN = re.compile(r"[（(]([^()（）]*[\u4e00-\u9fa5][^()（）]*)[)）]\s*$")


def parse_script(text: str) -> dict:
    """把 LLM 输出解析为 {title, characters, dialogue, expressions}。解析失败返回空 dict，走原始渲染。"""
    title, characters, expressions = "", [], []
    dialogue = []  # [speaker, en, zh]
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m_head = re.match(r"^【(剧本标题|标题|人物|角色|对话|关键表达|表达)】[:：]?(.*)$", line)
        if m_head:
            name, rest = m_head.group(1), m_head.group(2).strip()
            if name in ("剧本标题", "标题"):
                section, title = "title", rest
            elif name in ("人物", "角色"):
                section = "people"
                if rest:
                    characters.append(rest)
            elif name == "对话":
                section = "dialogue"
            else:
                section = "expr"
                if rest:
                    expressions.append(rest)
            continue
        if section == "title" and not title:
            title = line
        elif section == "people":
            characters.append(line)
        elif section == "dialogue":
            m = re.match(r"^([^：:]{1,12})[：:]\s*(.+)$", line)
            if m:
                speaker, body = m.group(1).strip(), m.group(2).strip()
                zh = ""
                mzh = _TR_CN.search(body)
                if mzh:
                    zh = mzh.group(1).strip()
                    body = _TR_CN.sub("", body).strip()
                dialogue.append([speaker, body, zh])
            elif dialogue and not re.match(r"^[\u4e00-\u9fa5]", line):
                dialogue[-1][1] += " " + line
        elif section == "expr":
            expressions.append(re.sub(r"^[\d]+[.、)．]\s*", "", line))
    if not dialogue:
        return {}
    return {
        "title": title or "英语情景剧本",
        "characters": characters,
        "dialogue": dialogue,
        "expressions": expressions,
    }


def _voice_map(parsed: dict) -> dict:
    """按出场顺序给每个说话人分配声库（A=Aria 女声，B=Guy 男声，更多角色轮换）。"""
    order = []
    for s, _, _ in parsed.get("dialogue") or []:
        if s not in order:
            order.append(s)
    return {name: TTS_VOICES[i % len(TTS_VOICES)] for i, name in enumerate(order)}


def split_en_zh(text: str) -> tuple:
    """把对戏回复拆成 (英文, 中文翻译)；无翻译时第二项为空串。"""
    text = text.strip()
    m = re.search(r"[（(]([^()（）]*[\u4e00-\u9fa5][^()（）]*)[)）]\s*$", text, re.S)
    if m:
        return text[: m.start()].strip(), m.group(1).strip()
    return text, ""


# ---------- 气泡渲染 ----------
BUBBLE_CSS = """
<style>
/* ===== 页面整体：浅蓝渐变背景 ===== */
.stApp {
    background: linear-gradient(180deg, #E8F2FC 0%, #F3F8FE 55%, #FAFCFF 100%);
}
[data-testid="stHeader"] { background: transparent; }

/* ===== 结果卡片：白底 + 阴影（作用于 st.container(border=True)） ===== */
[data-testid="stVerticalBlockBorderWrapper"] {
    background: #FFFFFF;
    border: 1px solid #E3EAF4 !important;
    border-radius: 16px;
    box-shadow: 0 6px 24px rgba(30, 60, 110, 0.10);
    padding: 18px 22px !important;
}

/* ===== 头部 ===== */
.hero-title {
    text-align: center; font-size: 2.6rem; font-weight: 800;
    background: linear-gradient(90deg, #4F8EF7, #9B59B6);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    margin-bottom: 0.2rem;
}
.hero-sub { text-align: center; color: #64748B; font-size: 1rem; margin-bottom: 1.2rem; }
.feature-card {
    background: #FFFFFF; border: 1px solid #E3EAF4; border-radius: 12px;
    padding: 1.1rem 1.2rem; height: 100%;
    box-shadow: 0 4px 14px rgba(30, 60, 110, 0.07);
}

/* ===== 对话气泡 ===== */
.lc-dialogue { display: flex; flex-direction: column; gap: 14px; margin: 10px 0 6px; }
.lc-line { display: flex; flex-direction: column; }
/* 气泡块与其朗读按钮之间收紧间距 */
[data-testid="stMarkdownContainer"]:has(> div > .lc-line) { margin-bottom: -14px; }
[data-testid="stMarkdownContainer"]:has(.lc-speaker) { margin-bottom: 0; }
.lc-line.right { align-items: flex-end; }
.lc-bubble {
    max-width: 82%; border-radius: 14px; padding: 11px 16px;
    background: #EEF3FB; border: 1px solid #D9E4F5;
}
.lc-line.right .lc-bubble { background: #E9F7EF; border-color: #D4EEDF; }
.lc-speaker { font-size: 12px; font-weight: 700; color: #4F8EF7; margin-bottom: 3px; letter-spacing: .5px; }
.lc-line.right .lc-speaker { color: #2F9E63; }
.lc-en { font-size: 15px; color: #1F2937; line-height: 1.65; }
.lc-zh { font-size: 13px; color: #8A94A6; margin-top: 5px; line-height: 1.55; }
.lc-people { display: flex; flex-wrap: wrap; gap: 8px; margin: 4px 0 10px; }
.lc-chip {
    background: #F1F5F9; border: 1px solid #E2E8F0; border-radius: 16px;
    padding: 4px 14px; font-size: 13px; color: #475569;
}
.lc-kv { display: flex; gap: 8px; align-items: baseline; margin-bottom: 2px; }
</style>
"""

st.markdown(BUBBLE_CSS, unsafe_allow_html=True)


# ---------- 欢迎页（Landing Page） ----------
if not st.session_state.entered:
    # 仅在欢迎页注入：渐变蓝紫按钮 + 欢迎页专属样式（进入主界面后不再生效）
    st.markdown(
        """
        <style>
        /* 欢迎页隐藏空侧边栏，主界面不受影响 */
        [data-testid="stSidebar"] { display: none !important; }
        [data-testid="stAppViewBlockContainer"] { padding: 2rem 1rem 1rem; max-width: 760px; }

        /* 居中大容器 */
        .lc-landing {
            display: flex; flex-direction: column; align-items: center;
            justify-content: center; text-align: center;
            min-height: 62vh; padding-top: 1.5rem;
        }
        .lc-landing h1 {
            font-size: 3rem; font-weight: 800; margin: 1rem 0 0.2rem;
            background: linear-gradient(90deg, #4F8EF7, #9B59B6);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .lc-landing p.lc-sub { color: #808A9B; font-size: 1.15rem; margin: 0 0 2.2rem; }

        /* 渐变蓝紫色圆角大按钮 */
        .lc-landing .stButton > button {
            background: linear-gradient(135deg, #4F8EF7 0%, #7C5CF0 55%, #9B59B6 100%);
            color: #FFFFFF; font-size: 1.15rem; font-weight: 700;
            padding: 0.85rem 3.2rem; border: none; border-radius: 999px;
            box-shadow: 0 8px 24px rgba(101, 88, 235, 0.35);
            cursor: pointer; transition: all .25s ease;
        }
        .lc-landing .stButton > button:hover {
            transform: translateY(-2px) scale(1.03);
            box-shadow: 0 12px 30px rgba(101, 88, 235, 0.45);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.container():
        st.markdown('<div class="lc-landing">', unsafe_allow_html=True)

        # ① 顶部居中封面图（宽 400）；图片缺失时优雅降级为 emoji 占位，不报错
        if os.path.exists("cover.png"):
            st.image("cover.png", width=400)
        else:
            st.markdown(
                '<div style="font-size:5rem;line-height:1;">🎭</div>',
                unsafe_allow_html=True,
            )

        # ② HTML 居中大标题 + 灰色副标题
        st.markdown(
            """
            <h1>LinguaCraft</h1>
            <p class="lc-sub">你的 24 小时贴身 AI 英语外教</p>
            """,
            unsafe_allow_html=True,
        )

        # ③ 圆角渐变蓝紫按钮；④ 点击后记录状态并切换到主界面
        if st.button("✨ 立即开启 AI 剧本"):
            st.session_state.entered = True
            st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

    st.stop()  # 欢迎页阶段：不渲染下方主界面


def render_script(parsed: dict) -> None:
    """按聊天气泡样式渲染解析后的剧本。"""
    esc = html_lib.escape
    st.subheader(f"🎬 {esc(parsed['title'])}")
    if parsed["characters"]:
        chips = "".join(f'<span class="lc-chip">👤 {esc(c)}</span>' for c in parsed["characters"][:6])
        st.markdown(f'<div class="lc-people">{chips}</div>', unsafe_allow_html=True)

    # 声库映射：按出场顺序，角色A=Aria女声，角色B=Guy男声（更多角色轮换）
    voice_of = _voice_map(parsed)

    # 每条对话单独渲染：气泡 HTML + 下方朗读按钮（按钮无法嵌入 HTML，须逐条穿插）
    for i, (speaker, en, zh) in enumerate(parsed["dialogue"]):
        side = "right" if i % 2 else "left"
        zh_html = f'<div class="lc-zh">{esc(zh)}</div>' if zh else ""
        st.markdown(
            f'<div class="lc-line {side}"><div class="lc-bubble">'
            f'<div class="lc-speaker">{esc(speaker)}</div>'
            f'<div class="lc-en">{esc(en)}</div>{zh_html}</div></div>',
            unsafe_allow_html=True,
        )
        # 🔊 朗读按钮：与气泡同侧对齐，按角色声库合成英文语音并在页面内播放
        if side == "left":
            btn_col, _ = st.columns([2, 3])
        else:
            _, btn_col = st.columns([3, 2])
        if btn_col.button("🔊 播放语音", key=f"tts_{i}", use_container_width=True):
            try:
                mp3_path = tts_speak(en, voice_of[speaker])
                st.audio(mp3_path, format="audio/mp3", autoplay=True)
            except Exception as e:  # noqa: BLE001
                st.error(f"语音合成失败：{e}（edge-tts 需联网访问微软服务；也可运行 `pip install edge-tts`）")

        # 🎤 跟读录音：先听上方原声 → 展开录音跟读 → 播放对比（临时录音，不保存本地）
        with st.expander("🎤 点击展开跟我读"):
            if not HAS_RECORDER:
                st.warning("录音组件未安装，请运行：pip install audio-recorder-streamlit")
            else:
                # key=f"rec_{i}" 固定组件身份：Streamlit 重跑时录音状态不丢（Rerun 关键）
                # 停止说话约 0.8 秒后自动结束录音（pause_threshold 默认值）
                audio_bytes = audio_recorder(
                    text="点击麦克风开始录音",
                    recording_color="#e8b62c",
                    neutral_color="#6aa36f",
                    icon_size="2x",
                    key=f"rec_{i}",
                )
                if audio_bytes is not None and len(audio_bytes) > 44:
                    # >44 字节 = WAV 头之外有实际音频数据（空录音只有头）
                    st.success("录音成功！你可以点击上方播放对比。")
                    st.audio(audio_bytes, format="audio/wav")

    if parsed["expressions"]:
        st.markdown("**✨ 关键表达**（勾选 ⭐ 收进生词本）")
        vocab = st.session_state.vocab
        for item in parsed["expressions"][:8]:
            # 勾选框 key 用内容哈希：同一表达在不同剧本中勾选状态一致，不随轮次错位
            key = "vocab_ck_" + hashlib.md5(item.encode("utf-8")).hexdigest()[:10]
            col_ck, col_txt = st.columns([1, 9])
            checked = col_ck.checkbox(
                "收藏", value=item in vocab, key=key,
                label_visibility="collapsed",
            )
            col_txt.markdown(
                f'<div class="lc-kv" style="padding-top:4px"><span>🔹</span>'
                f'<span>{esc(item)}</span></div>',
                unsafe_allow_html=True,
            )
            # 勾选状态与生词本双向同步（vocab 非控件 key，可直接修改）
            if checked and item not in vocab:
                vocab.append(item)
            elif not checked and item in vocab:
                vocab.remove(item)


# ---------- 页头 ----------
st.markdown('<div class="hero-title">🎭 LinguaCraft</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="hero-sub">LinguaCraft —— AI 英语剧本生成器<br>'
    '还没开口就紧张？输入场景，一键生成专属英语情景剧本，'
    '让你在真实语境里告别「开口难」。</div>',
    unsafe_allow_html=True,
)

st.divider()

# ---------- 📖 我的专属生词本（页面顶部） ----------
with st.expander(f"📖 我的专属生词本（{len(st.session_state.vocab)} 个）"):
    if st.session_state.vocab:
        esc = html_lib.escape
        for w in st.session_state.vocab:
            st.markdown(
                f'<div class="lc-kv"><span>🔹</span><span>{esc(w)}</span></div>',
                unsafe_allow_html=True,
            )
        st.button("🗑 清空生词本", on_click=_clear_vocab)
    else:
        st.caption("暂无收藏。生成剧本后，在「✨ 关键表达」中勾选即可加入生词本～")

# ---------- 侧边栏 ----------
with st.sidebar:
    st.header("⚙️ 剧本配置")

    scene = st.text_input(
        "🎯 对话场景",
        value="在星巴克点一杯咖啡",
        help="描述你想练习的日常情景，越具体效果越好",
    )

    level = st.selectbox(
        "📈 英语难度",
        ["初级", "中级", "高级"],
        help="初级：基础词汇短句；中级：日常流利表达；高级：地道惯用语与复杂句式",
    )

    st.divider()
    st.header("👤 我的形象")

    # ---- 捏人控件（widget key 用于「加载装扮」回填） ----
    gender = st.radio("🚻 性别", ["男生", "女生"], horizontal=True, key="av_gender")
    hair_pool = MALE_HAIRS if gender == "男生" else FEMALE_HAIRS
    # 性别切换后旧发型可能不在新池子里（如女生长发切到男生），先回填合法默认值防报错
    if st.session_state.get("av_hair") not in hair_pool:
        st.session_state.av_hair = hair_pool[0]
    st.selectbox(
        "💇 发型", hair_pool,
        format_func=lambda v: f"风格 {int(v[-2:])}",
        key="av_hair",
    )
    st.selectbox("🎨 发色", list(HAIR_COLOR_PALETTE), key="av_hair_color")
    st.selectbox("👀 眼睛", EYES_ALL, format_func=lambda v: f"眼型 {int(v[-2:])}", key="av_eyes")
    st.selectbox("👄 嘴巴", MOUTH_ALL, format_func=lambda v: f"嘴型 {int(v[-2:])}", key="av_mouth")
    st.selectbox(
        "✏️ 眉毛", ["默认"] + BROWS_ALL,
        format_func=lambda v: "默认（随机）" if v == "默认" else f"眉型 {int(v[-2:])}",
        key="av_eyebrows",
    )
    st.selectbox("👓 眼镜", ["无"] + GLASSES_ALL,
                 format_func=lambda v: "无" if v == "无" else f"款 {int(v[-2:])}", key="av_glasses")
    st.selectbox("✨ 特征", ["无"] + FEATURES_ALL,
                 format_func=lambda v: "无" if v == "无" else FEATURE_LABELS[v], key="av_feature")
    st.selectbox("🫶 肤色", list(SKIN_COLOR_PALETTE), key="av_skin")

    # ---- 组装当前装扮（get 带默认值：裸导入测试 / 会话首次运行均安全） ----
    current_cfg = {
        "gender": st.session_state.get("av_gender", "男生"),
        "hair": st.session_state.get("av_hair", MALE_HAIRS[0]),
        "hair_color": st.session_state.get("av_hair_color", "黑色"),
        "eyes": st.session_state.get("av_eyes", EYES_ALL[0]),
        "mouth": st.session_state.get("av_mouth", MOUTH_ALL[0]),
        "eyebrows": None if st.session_state.get("av_eyebrows", "默认") == "默认"
                    else st.session_state["av_eyebrows"],
        "glasses": None if st.session_state.get("av_glasses", "无") == "无"
                   else st.session_state["av_glasses"],
        "feature": None if st.session_state.get("av_feature", "无") == "无"
                   else st.session_state["av_feature"],
        "skin": st.session_state.get("av_skin", "自然"),
    }

    # 实时预览：任一选择变化 → 脚本重跑 → 头像即时刷新
    avatar_url = build_avatar_url(current_cfg)
    col_preview, col_random = st.columns([3, 2])
    with col_preview:
        try:
            st.image(avatar_url, width=180)
        except Exception:  # noqa: BLE001  断网 / API 不可达时降级提示
            st.warning("头像加载失败：DiceBear API 需要联网访问，当前网络可能不通。")
    with col_random:
        st.button("🎲 随机捏人", on_click=_random_avatar, use_container_width=True)
    st.checkbox(
        "🎭 把我的形象写进剧本",
        value=True,
        help="勾选后，AI 会把当前角色形象设定为对话中的一方，自然融入剧情",
        key="av_use_persona",
    )

    # ---- 角色衣柜：创建 / 保存 / 加载 / 删除 ----
    st.subheader("🎭 角色衣柜")
    saved = load_avatars()

    if saved:
        picked = st.selectbox("已保存的角色", list(saved.keys()), key="av_pick")
        col_load, col_del = st.columns(2)
        # 加载必须用 on_click 回调：直接在按钮体内改 session_state 会因
        # 「控件已实例化后不可修改」而抛 StreamlitAPIException
        col_load.button(
            "📂 加载装扮", on_click=_load_avatar_cfg, args=(saved[picked],),
            use_container_width=True,
        )
        if col_del.button("🗑 删除", use_container_width=True):
            del saved[picked]
            save_avatars(saved)
            st.rerun()
    else:
        st.caption("还没有保存的角色，创建第一个吧～")

    new_name = st.text_input("新角色名称", placeholder="例如：小陆 / 学长版我")
    if st.button("💾 保存当前装扮", use_container_width=True):
        if not new_name.strip():
            st.warning("先给角色起个名字～")
        elif new_name.strip() in saved:
            st.warning("这个名字已存在，换个名字或先删除旧角色。")
        else:
            saved[new_name.strip()] = current_cfg
            save_avatars(saved)
            st.success(f"角色「{new_name.strip()}」已保存（本地 {AVATAR_FILE}）")
            st.rerun()

    st.divider()
    st.header("🔑 API 配置")

    provider_name = st.selectbox("服务商", list(PROVIDERS.keys()))
    provider = PROVIDERS[provider_name]

    st.session_state.api_key = st.text_input(
        "API Key",
        value=st.session_state.api_key,
        type="password",
        help="Key 仅保存在本次会话内存中，不会上传或落盘",
    )

    if provider_name == "自定义（OpenAI 兼容）":
        base_url = st.text_input("Base URL", value="https://api.example.com/v1")
        model = st.text_input("模型名称", value="your-model")
    else:
        base_url = provider["base_url"]
        model = st.text_input("模型名称", value=provider["model"])
        if provider["key_url"]:
            st.caption(f"还没有 Key？[点这里免费申请]({provider['key_url']})")

    st.divider()
    generate_btn = st.button("🚀 一键生成剧本", use_container_width=True, type="primary")

    # ---------- 🕘 生成历史（点击回看之前的剧本） ----------
    with st.expander(f"🕘 历史记录（{len(st.session_state.history)}）"):
        if not st.session_state.history:
            st.caption("生成成功后会自动记录场景、难度和时间，点击可回看剧本。")
        else:
            for idx, h in enumerate(st.session_state.history):
                scene_short = h["scene"][:14] + ("…" if len(h["scene"]) > 14 else "")
                if st.button(
                    f"↩️ {h['time']} · {scene_short} · {h['level']}",
                    key=f"hist_{idx}", use_container_width=True,
                    help=h["scene"],
                ):
                    # 回看历史剧本：恢复 result 并重置对戏（生词本保留，跨剧本累积）
                    st.session_state.result = h["result"]
                    st.session_state.result_meta = h["meta"]
                    st.session_state.result_scene = h["scene"]
                    st.session_state.result_level = h["level"]
                    st.session_state.chat_history = []
                    st.session_state.pop("rp_ai_role", None)
                    st.rerun()
            if st.button("🗑 清空历史", key="hist_clear", use_container_width=True):
                st.session_state.history = []
                st.rerun()

# ---------- 主区域 ----------
col_feature, col_tip = st.columns([3, 2])

with col_feature:
    st.subheader("📖 生成结果")

    if generate_btn:
        if not scene.strip():
            st.warning("请先在左侧填写对话场景～")
        elif not st.session_state.api_key.strip():
            st.error(
                "缺少 API Key。请在左侧边栏「API 配置」填入你的 Key（可点击申请链接免费获取），"
                "Key 只保存在当前会话，刷新后需要重新输入。"
            )
        elif not base_url.strip() or not model.strip():
            st.error("请填写完整的 Base URL 和模型名称。")
        else:
            # 形象描述：勾选「把我的形象写进剧本」时作为人物设定传给 AI
            persona = describe_avatar(current_cfg) if st.session_state.get("av_use_persona", True) else ""
            try:
                with st.container(border=True):
                    st.caption("🎬 AI 编剧已就位，正在为你敲剧本... ✍️")
                    slot = st.empty()
                    with slot:
                        full_text = st.write_stream(
                            stream_llm(
                                base_url, model, st.session_state.api_key.strip(),
                                scene.strip(), level, persona,
                            )
                        )
                st.session_state.result = full_text
                st.session_state.result_meta = (
                    f"场景：{scene}　|　难度：{level}　|　模型：{model}"
                    + (f"　|　形象：{persona}" if persona else "")
                )
                st.session_state.result_scene = scene.strip()  # 海报/元数据用
                st.session_state.result_level = level
                st.session_state.just_done = True
                # 🕘 记录生成历史：场景 + 难度 + 时间 + 完整剧本（最新在前，最多保留 20 条）
                st.session_state.history.insert(0, {
                    "scene": scene.strip(),
                    "level": level,
                    "time": datetime.now().strftime("%m-%d %H:%M"),
                    "result": full_text,
                    "meta": st.session_state.result_meta,
                })
                del st.session_state.history[20:]
                st.session_state.chat_history = []  # 新剧本 → 清空角色扮演对戏历史
                st.session_state.pop("rp_ai_role", None)
                st.rerun()  # 干净重渲染为气泡排版
            except requests.exceptions.Timeout:
                st.error("请求超时。请检查网络后重试。")
            except requests.exceptions.HTTPError as e:
                code = e.response.status_code
                hint = "Key 无效或未充值" if code == 401 else "请检查 Key 权限、模型名称或稍后重试"
                st.error(f"接口返回错误 {code}：{hint}\n\n`{e.response.text[:300]}`")
            except Exception as e:  # noqa: BLE001
                st.error(f"生成失败：{e}")

    if "result" in st.session_state:
        # 生成完成的瞬间放气球 🎈
        if st.session_state.pop("just_done"):
            st.balloons()
        with st.container(border=True):
            st.caption(st.session_state.result_meta)

            # ---------- 🎧 环境音效沉浸模式（对话气泡上方） ----------
            # 用 result_scene 而非当前侧边栏输入，保证「回看历史剧本」时音效也正确对应
            amb_scene = st.session_state.get("result_scene", scene.strip())
            amb_url = match_scene_audio(amb_scene)
            if amb_url:
                amb_label = SCENE_AUDIO_LABELS.get(amb_url, "环境音")
                st.info(f"🎧 环境音效已就绪（{amb_label}），点击下方播放，沉浸式练习吧！")
                # 本地 WAV / MP3 文件均可直接播放；loop=True 循环播放不打断练习
                amb_format = "audio/wav" if amb_url.endswith(".wav") else "audio/mp3"
                try:
                    st.audio(amb_url, format=amb_format, loop=True)
                except TypeError:  # 旧版 Streamlit 不支持 loop 参数
                    st.audio(amb_url, format=amb_format)

            parsed = parse_script(st.session_state.result)
            if parsed:
                render_script(parsed)
            else:
                st.markdown(st.session_state.result)
        dl_clicked = st.download_button(
            "📥 下载剧本（Markdown）",
            data=st.session_state.result,
            file_name="linguacraft_script.md",
            mime="text/markdown",
            use_container_width=True,
        )
        if dl_clicked:
            st.snow()  # 下载成功 ❄️ 飘雪
        with st.expander("📋 查看纯文本 / 复制"):
            st.code(st.session_state.result, language=None)

        # ---------- 🖼️ 生成分享海报（Pillow 合成） ----------
        st.divider()
        poster_bg = "bg.png"
        bg_files = [p for p in POSTER_BG_LABELS if os.path.exists(p)]
        if len(bg_files) > 1:
            poster_bg = st.radio(
                "海报背景", bg_files,
                format_func=lambda p: POSTER_BG_LABELS.get(p, p),
                horizontal=True, key="poster_bg",
            )
        elif bg_files:
            poster_bg = bg_files[0]

        # 对话提取：前 4~5 个回合（最多 5 句话，海报排版会自动做安全降级）
        poster_lines = (parsed.get("dialogue") or [])[:5] if parsed else []

        # 我的形象贴给哪个角色（其余角色自动生成 DiceBear 头像）
        if poster_lines:
            poster_speakers = []
            for r in poster_lines:
                if r[0] not in poster_speakers:
                    poster_speakers.append(r[0])
            pick_options = poster_speakers + ["都不用"]
            if st.session_state.get("poster_me") not in pick_options:
                st.session_state.poster_me = poster_speakers[0]
            st.selectbox(
                "🙋 我的形象贴给哪个角色", pick_options, key="poster_me",
                help="选中的角色使用左侧「我的形象」头像；其余角色按名字自动生成头像",
            )

        if st.button("🖼️ 生成分享海报", use_container_width=True):
            if not poster_lines:
                st.error("剧本解析失败，无法提取对话内容，暂不能生成海报。")
            else:
                my_pick = st.session_state.get("poster_me", "")
                avatars = {}
                for row in poster_lines:
                    speaker = row[0]
                    if speaker in avatars:
                        continue
                    if speaker == my_pick:
                        # 我的形象（DiceBear SVG URL，合成时自动转 PNG 拉取）
                        avatars[speaker] = avatar_url
                    else:
                        # 其余角色：按角色名生成确定性 DiceBear 头像（PNG 接口）
                        avatars[speaker] = ("https://api.dicebear.com/7.x/adventurer/png?seed="
                                            + quote(speaker))
                p_scene = st.session_state.get("result_scene", scene.strip() or "日常情景")
                p_level = st.session_state.get("result_level", level)
                p_vocab = [e for e in (parsed.get("expressions") or [])[:2]] if parsed else []
                try:
                    out = generate_poster(p_scene, p_level, poster_lines, avatars,
                                          vocab=p_vocab, bg_path=poster_bg)
                    st.image(out, use_container_width=True)
                    with open(out, "rb") as f:
                        st.download_button(
                            "📥 下载海报 PNG", data=f,
                            file_name="linguacraft_poster.png", mime="image/png",
                            use_container_width=True,
                        )
                except Exception as e:  # noqa: BLE001  字体缺失 / Pillow 未安装等
                    st.error(f"海报生成失败：{e}")

        # ---------- 🎭 角色扮演：以剧本为上下文继续对戏 ----------
        st.divider()
        st.subheader("🎭 角色扮演 · 和剧本角色继续对话")
        st.caption(
            "输入你的台词，AI 会扮演剧本中的另一个角色与你即兴对戏；"
            "对话历史保存在本次会话中，生成新剧本会自动清空。"
        )

        ss = st.session_state
        if "chat_history" not in ss:
            ss.chat_history = []

        # AI 扮演谁：从剧本说话人中选（默认第二个出场角色）；剧本解析失败时用通用兜底
        speakers = []
        for s, _, _ in (parsed.get("dialogue") or []) if parsed else []:
            if s not in speakers:
                speakers.append(s)
        if ss.get("rp_ai_role") not in speakers:
            ss.rp_ai_role = speakers[1] if len(speakers) > 1 else (speakers[0] if speakers else "")

        col_role, col_clear = st.columns([3, 1])
        if speakers:
            with col_role:
                ai_role = st.selectbox(
                    "🤖 AI 扮演的角色", speakers,
                    key="rp_ai_role", on_change=_clear_chat,
                    help="切换角色将开始一段新的对戏",
                )
        else:
            with col_role:
                ai_role = "对方角色"
                st.caption("🤖 AI 将扮演剧本中的对方角色")
        col_clear.button("🧹 清空对话", on_click=_clear_chat, use_container_width=True)

        voice_map = _voice_map(parsed) if parsed else {}
        ai_voice = voice_map.get(ai_role)

        # 历史消息：st.chat_message 渲染；AI 回复附翻译与朗读按钮
        for i, m in enumerate(ss.chat_history):
            if m["role"] == "user":
                with st.chat_message("user", avatar=avatar_url):
                    st.markdown(m["content"])
            else:
                with st.chat_message("assistant", avatar="🎭"):
                    en, zh = split_en_zh(m["content"])
                    st.markdown(en)
                    if zh:
                        st.caption(f"（{zh}）")
                    if ai_voice:
                        if st.button("🔊 播放语音", key=f"chat_tts_{i}"):
                            try:
                                mp3_path = tts_speak(" ".join(en.split()), ai_voice)
                                st.audio(mp3_path, format="audio/mp3", autoplay=True)
                            except Exception as e:  # noqa: BLE001
                                st.error(f"语音合成失败：{e}")

        # 输入框（固定在列底部）
        user_line = st.chat_input("输入你的台词...")
        if user_line:
            if not st.session_state.api_key.strip():
                st.error("角色扮演需要 API Key，请在左侧边栏「🔑 API 配置」中填写。")
            else:
                ss.chat_history.append({"role": "user", "content": user_line})
                with st.chat_message("user", avatar=avatar_url):
                    st.markdown(user_line)
                with st.chat_message("assistant", avatar="🎭"):
                    try:
                        reply = st.write_stream(
                            stream_chat(
                                base_url, model, st.session_state.api_key.strip(),
                                st.session_state.result, list(ss.chat_history), ai_role,
                            )
                        )
                    except requests.exceptions.Timeout:
                        st.error("请求超时，请检查网络后重试。")
                    except requests.exceptions.HTTPError as e:
                        code = e.response.status_code
                        st.error(f"接口返回错误 {code}：`{e.response.text[:200]}`")
                    except Exception as e:  # noqa: BLE001
                        st.error(f"对话失败：{e}")
                    else:
                        if reply and reply.strip():
                            ss.chat_history.append({"role": "assistant", "content": reply.strip()})
                        st.rerun()  # 重渲染为带翻译与朗读按钮的整洁历史消息
    else:
        st.markdown(
            '<div class="feature-card">👈 请在左侧边栏选择场景与难度，填好 API Key 后'
            '点击 <b>🚀 一键生成剧本</b> 开始体验。</div>',
            unsafe_allow_html=True,
        )

with col_tip:
    st.subheader("✨ 为什么用 LinguaCraft")
    st.markdown(
        """
        <div class="feature-card">
        🗣️ <b>情景实战</b><br>围绕真实场景编写对话<br><br>
        🎚️ <b>难度可控</b><br>三级难度自由切换<br><br>
        🧠 <b>告别紧张</b><br>练熟剧本再开口
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---------- 页脚 ----------
st.divider()
st.caption("© 2026 LinguaCraft · 流式打字机生成 · 角色扮演对戏 · 已接入 DeepSeek / 智谱 / 通义 / OpenAI 兼容接口")

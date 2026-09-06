"""Parse standardized emotion tags from model replies for local sticker lookup."""

from __future__ import annotations

import re

STICKER_TAG_PATTERN = re.compile(r"【([^【】]{1,16})】")
ALLOWED_STICKER_TAGS: frozenset[str] = frozenset(
    {
        "开心",
        "委屈",
        "狗头",
        "无语",
        "大笑",
        "emo",
        "可爱",
        "生气",
        "惊讶",
        "晚安",
    }
)
STICKER_PROMPT_DIRECTIVE = (
    "根据对话情绪，可在回复中适时输出一个标准化表情标记，"
    "格式必须是【关键词】，例如【开心】【委屈】【狗头】【无语】【大笑】【emo】【可爱】。"
    "不要直接贴图，不要输出图片 URL 或 Markdown 图片；表情包由后端根据标记检索本地素材。"
    "不要每句都加标记；冷静或无关对话可省略。"
)


def extract_sticker_tags(text: str) -> tuple[str, tuple[str, ...]]:
    tags: list[str] = []
    for match in STICKER_TAG_PATTERN.finditer(text):
        tag = match.group(1).strip()
        if tag in ALLOWED_STICKER_TAGS and tag not in tags:
            tags.append(tag)
    cleaned = STICKER_TAG_PATTERN.sub("", text)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, tuple(tags)

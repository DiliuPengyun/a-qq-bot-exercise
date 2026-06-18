"""第一类理性记忆 known_facts.xml：加载 / 保存 / 增量合并。

文件格式：
<facts>
  <fact spoken_by="group_xxx:123456">123456 喜欢打篮球</fact>
  <fact spoken_by="">群里约定每周五晚上打游戏</fact>
</facts>
"""

import os
import xml.etree.ElementTree as ET

from config import KNOWN_FACTS_FILE

_TAG_FACTS = "facts"
_TAG_FACT = "fact"
_ATTR_SPOKEN_BY = "spoken_by"
_ATTR_OLD = "old"


# ── 读写 ────────────────────────────────────────────────


def load_known_facts() -> str:
    """读取 known_facts.xml 全文，不存在返回空字符串。"""
    if os.path.exists(KNOWN_FACTS_FILE):
        try:
            with open(KNOWN_FACTS_FILE, encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            print(f"[KnownFacts] 读取失败: {e}")
    return ""


def save_known_facts(content: str) -> None:
    with open(KNOWN_FACTS_FILE, "w", encoding="utf-8") as f:
        f.write(content)


# ── 增量合并 ────────────────────────────────────────────


def _parse_facts(xml_str: str) -> list[tuple[str, str]]:
    """解析 <facts> 下的 <fact> 列表，返回 [(spoken_by, text), ...]"""
    if not xml_str.strip():
        return []
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return []
    if root.tag != _TAG_FACTS:
        # 可能是 <root> 包裹的
        root = root.find(_TAG_FACTS) or root
    result: list[tuple[str, str]] = []
    for fact in root.findall(_TAG_FACT):
        spoken_by = fact.get(_ATTR_SPOKEN_BY, "")
        text = (fact.text or "").strip()
        if text:
            result.append((spoken_by, text))
    return result


def _serialize_facts(facts: list[tuple[str, str]]) -> str:
    """把 [(spoken_by, text), ...] 序列化为 XML 字符串。"""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f"<{_TAG_FACTS}>"]
    for spoken_by, text in facts:
        # XML 转义
        safe_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe_attr = spoken_by.replace('"', "&quot;").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f'  <{_TAG_FACT} {_ATTR_SPOKEN_BY}="{safe_attr}">{safe_text}</{_TAG_FACT}>')
    lines.append(f"</{_TAG_FACTS}>")
    return "\n".join(lines)


def _validate_spoken_by(spoken_by: str, allowed: set[str]) -> bool:
    """校验 spoken_by 中的每个 person_id 是否合法。空字符串允许。"""
    if not spoken_by.strip():
        return True
    parts = [p.strip() for p in spoken_by.split(",") if p.strip()]
    if not parts:
        return True
    return all(p in allowed for p in parts)


def merge_known_facts(
    model_output: str, allowed_person_ids: set[str]
) -> str | None:
    """解析模型输出，与当前 known_facts.xml 合并，返回新 XML 字符串。

    返回 None 表示解析失败或校验失败（需走 repair）。
    """
    # 解析模型输出
    try:
        root = ET.fromstring(f"<root>{model_output}</root>")
    except ET.ParseError:
        return None

    first_class = root.find("第一类")
    if first_class is None:
        # 没有第一类段，保留旧文件不变
        return load_known_facts() or _serialize_facts([])

    # 加载当前事实列表
    current_facts = _parse_facts(load_known_facts())

    # 处理 <新增>
    add_elem = first_class.find("新增")
    if add_elem is not None:
        for fact in add_elem.findall(_TAG_FACT):
            spoken_by = fact.get(_ATTR_SPOKEN_BY, "")
            text = (fact.text or "").strip()
            if not text:
                continue
            if not _validate_spoken_by(spoken_by, allowed_person_ids):
                print(f"[KnownFacts] 新增事实来源不合法: spoken_by={spoken_by}, text={text[:40]}")
                continue
            current_facts.append((spoken_by, text))

    # 处理 <修改>
    mod_elem = first_class.find("修改")
    if mod_elem is not None:
        for fact in mod_elem.findall(_TAG_FACT):
            spoken_by = fact.get(_ATTR_SPOKEN_BY, "")
            old_text = fact.get(_ATTR_OLD, "").strip()
            new_text = (fact.text or "").strip()
            if not new_text or not old_text:
                continue
            if not _validate_spoken_by(spoken_by, allowed_person_ids):
                print(f"[KnownFacts] 修改事实来源不合法: spoken_by={spoken_by}")
                continue
            # 按 (spoken_by, old_text) 定位
            found = False
            for i, (sb, t) in enumerate(current_facts):
                if sb == spoken_by and t == old_text:
                    current_facts[i] = (spoken_by, new_text)
                    found = True
                    break
            if not found:
                print(f"[KnownFacts] 修改定位失败: spoken_by={spoken_by}, old={old_text[:40]}")

    # 处理 <删除>
    del_elem = first_class.find("删除")
    if del_elem is not None:
        for fact in del_elem.findall(_TAG_FACT):
            spoken_by = fact.get(_ATTR_SPOKEN_BY, "")
            text = (fact.text or "").strip()
            if not text:
                continue
            # 按 (spoken_by, text) 定位删除
            current_facts = [
                (sb, t) for sb, t in current_facts
                if not (sb == spoken_by and t == text)
            ]

    return _serialize_facts(current_facts)


# ── 注入 system prompt ──────────────────────────────────


def inject_known_facts() -> str:
    """读取 known_facts.xml 返回给 system prompt 注入用。"""
    content = load_known_facts()
    if not content:
        return ""
    return f"\n=== 第一类理性记忆 ===\n{content}"

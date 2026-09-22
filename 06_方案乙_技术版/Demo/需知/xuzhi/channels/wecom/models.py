"""入站消息模型：把企微 aibot_msg_callback 归一化成 Inbound。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Inbound:
    req_id: str
    msgid: str
    aibotid: str
    chattype: str                 # single | group
    chatid: Optional[str]
    userid: Optional[str]
    msgtype: str                  # text | mixed | image | file | voice | video | ...
    text: str                     # 抽出的文字（mixed 会把图片位置标成 [image]）
    attachments: List[Dict[str, Any]] = field(default_factory=list)  # 原样保留图片/文件等条目
    quote: Optional[Dict[str, Any]] = None
    response_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)
    received_at: float = field(default_factory=time.time)

    @property
    def session_key(self) -> str:
        """群聊按 群+人，单聊按 人。"""
        if self.chattype == "group" and self.chatid:
            return f"group:{self.chatid}:{self.userid}"
        return f"single:{self.userid}"

    @classmethod
    def from_frame(cls, frame: Dict[str, Any]) -> "Inbound":
        body = frame.get("body", {}) or {}
        msgtype = body.get("msgtype", "")
        text, attachments = _extract_text_and_attachments(body, msgtype)
        return cls(
            req_id=(frame.get("headers") or {}).get("req_id", ""),
            msgid=body.get("msgid", ""),
            aibotid=body.get("aibotid", ""),
            chattype=body.get("chattype", "single"),
            chatid=body.get("chatid"),
            userid=(body.get("from") or {}).get("userid"),
            msgtype=msgtype,
            text=text,
            attachments=attachments,
            quote=body.get("quote"),
            response_url=body.get("response_url"),
            raw=frame,
        )


def _extract_text_and_attachments(body: Dict[str, Any], msgtype: str):
    attachments: List[Dict[str, Any]] = []
    if msgtype == "text":
        return (body.get("text") or {}).get("content", ""), attachments
    if msgtype == "mixed":
        parts: List[str] = []
        for item in (body.get("mixed") or {}).get("msg_item", []) or []:
            t = item.get("msgtype")
            if t == "text":
                parts.append((item.get("text") or {}).get("content", ""))
            else:
                parts.append(f"[{t}]")
                attachments.append(item)
        return "\n".join(p for p in parts if p), attachments
    if msgtype == "voice":
        # 官方：语音会转写成文字放在 voice.content
        return (body.get("voice") or {}).get("content", ""), attachments
    # image / file / video 等：正文为空，条目进 attachments
    if msgtype in body:
        attachments.append({"msgtype": msgtype, **(body.get(msgtype) or {})})
    return "", attachments

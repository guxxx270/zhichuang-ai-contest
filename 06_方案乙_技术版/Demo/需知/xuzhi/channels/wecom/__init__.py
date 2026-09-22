"""需知 · 企业微信入口。

同事在企微里把一堆话发给机器人，需知当场跑完「隐盾 → 问清 → 写单 → 估量 → 定架」，
把"要和业务确认什么 / 多少工时 / 需要 IT 拍板什么"直接回到聊天里。

走企微智能机器人的 **长连接** 模式：服务主动连出去，内网无需公网回调地址。
协议见 https://developer.work.weixin.qq.com/document/path/101463
"""
from .config import WecomConfig, load_wecom_config

__all__ = ["WecomConfig", "load_wecom_config"]

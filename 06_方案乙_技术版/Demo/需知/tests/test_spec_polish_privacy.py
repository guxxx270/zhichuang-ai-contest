"""写单润色：业务答复里的手机号、账号进模型前也要脱敏，润色结果再还原。"""
from __future__ import annotations

from xuzhi.pipeline import analyze
from xuzhi.pipeline.spec import build_spec


class EchoLLM:
    """把收到的正文原样返回，记录出站内容。"""
    mode = "api"
    model = "echo"
    last_model = "echo"
    note = ""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.current_purpose = ""

    def chat(self, system: str, user: str, temperature: float = 0.1, json_mode: bool = False, purpose: str = "") -> str:
        self.sent.append(user)
        return user


def test_business_answers_are_redacted_before_polish_and_restored_after():
    a = analyze("净值日报能不能加一列较上一交易日变动，风险指标也放进去。", llm_polish=False)
    q = a.questions[0]
    q.answer = "以结算后为准，有疑问打王经理 13812345678，资金账号 8800123456"
    llm = EchoLLM()

    spec = build_spec(a.card, a.questions, a.architecture.coverage_line, llm)

    assert llm.sent, "应调用一次写单润色"
    outbound = "\n".join(llm.sent)
    assert "13812345678" not in outbound and "8800123456" not in outbound
    assert "<手机_1>" in outbound
    assert "13812345678" in spec.business_md, "润色结果应还原成原文给业务看"

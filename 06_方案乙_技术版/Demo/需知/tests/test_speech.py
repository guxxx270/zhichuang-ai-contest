import pytest

from xuzhi.asr import correct_domain_speech, transcribe_audio
from xuzhi.speech import append_dictation


def test_append_dictation():
    assert append_dictation("", "净值日报加一列对标指数") == "净值日报加一列对标指数"
    assert append_dictation("已有", "对标指数") == "已有\n对标指数"
    assert append_dictation("对标指数", "对标指数") == "对标指数"
    assert append_dictation("  ", "") == ""
    # 框里删光后再口述：只留下新内容，不把已删文字拼回来
    assert append_dictation("", "新的一句") == "新的一句"
    assert append_dictation("还留着", "新的一句") == "还留着\n新的一句"


def test_correct_domain_speech():
    assert correct_domain_speech("净值日报加一列对标只是") == "净值日报加一列对标指数"
    assert "沪深300" in correct_domain_speech("对一下沪深三百")
    assert correct_domain_speech("净 值 日 报") == "净值日报"
    assert correct_domain_speech("  ") == ""


def test_transcribe_rejects_empty():
    with pytest.raises(ValueError, match="没有录音数据"):
        transcribe_audio(b"", api_key="sk")
    with pytest.raises(ValueError, match="没有语音识别 Key"):
        transcribe_audio(b"xx", api_key="")

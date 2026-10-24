"""内存中的模型后台任务：不访问 UI、Memory 或业务台账。

调用方为每个任务创建专用 LLM；网络客户端不可深拷贝或在任务间复用。
需求快照在启动前深拷贝。任务结果只在完成后发布，UI 再按自己的版本号决定是否应用。
"""
from __future__ import annotations

import threading
import time
import uuid
from copy import deepcopy
from typing import Callable

from .llm import LLM
from .pipeline import Analysis, enhance_analysis
from .pipeline.estimate import Estimate, estimate


class _Discarded(Exception):
    pass


class ModelJob:
    def __init__(self, kind: str, work: Callable[[Callable[[str], None]], Analysis | Estimate], llm: LLM) -> None:
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.done = threading.Event()
        self._lock = threading.Lock()
        self._status = "running"
        self._stage = "等待模型处理"
        self._started = time.monotonic()
        self._finished: float | None = None
        self._result: Analysis | Estimate | None = None
        self._error = ""
        self._discarded = False
        self._thread = threading.Thread(target=self._run, args=(work, llm), name=f"xuzhi-{kind}-{self.id[:8]}", daemon=True)
        self._thread.start()

    def _set_stage(self, stage: str) -> None:
        with self._lock:
            if self._discarded:
                raise _Discarded()
            self._stage = stage

    def _run(self, work, llm) -> None:
        try:
            result = work(self._set_stage)
            with self._lock:
                if self._discarded:
                    raise _Discarded()
                self._result = result
                self._status = "completed"
                self._stage = "处理完成"
        except _Discarded:
            with self._lock:
                self._status = "failed"
                self._stage = "已忽略旧任务"
                self._result = None
        except Exception as error:
            with self._lock:
                self._status = "failed"
                self._stage = "模型处理失败"
                # 不暴露异常文本：可能包含网关 URL、认证头或 API Key。
                self._error = ("模型请求超时，快速结果已保留，可稍后重试。" if isinstance(error, TimeoutError)
                               else "模型后台处理失败，快速结果已保留。请检查模型配置或稍后重试。")
                self._result = None
        finally:
            if getattr(llm, "_job_owned_audit", False):
                try:
                    llm.audit.conn.close()
                except Exception:
                    pass
            with self._lock:
                self._finished = time.monotonic()
            self.done.set()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id, "kind": self.kind, "status": self._status, "stage": self._stage,
                "elapsed": max(0.0, (self._finished or time.monotonic()) - self._started),
                "result": deepcopy(self._result), "error": self._error, "discarded": self._discarded,
            }

    def wait(self, timeout: float | None = None) -> bool:
        return self.done.wait(timeout)

    def discard(self) -> None:
        """不中断正在进行的请求；该请求返回后，后续阶段停止发起模型调用。"""
        with self._lock:
            self._discarded = True


def start_analysis_job(
    seed: Analysis, llm: LLM, *, mobile: bool | None = None, client_view: bool | None = None,
) -> ModelJob:
    owned = deepcopy(seed)

    def work(onstage):
        return enhance_analysis(owned, llm, onstage=onstage, mobile=mobile, client_view=client_view)

    return ModelJob("analysis", work, llm)


def start_estimate_job(seed: Analysis, llm: LLM) -> ModelJob:
    owned = deepcopy(seed)

    def work(onstage):
        onstage("模型估时")
        result = estimate(owned.card, owned.redacted_text, owned.questions, llm=llm, drafts=owned.drafts, repos=owned.repos)
        onstage("模型估时完成")
        return result

    return ModelJob("estimate", work, llm)

"""统一日志系统"""

import logging
import os
import json
from datetime import datetime
from typing import Any, Dict, Optional


class Logger:
    _instance = None

    def __init__(self, log_dir: str = "output/logs", level: int = logging.INFO):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

        self._logger = logging.getLogger("rwkv_novel")
        self._logger.setLevel(level)

        if not self._logger.handlers:
            fh = logging.FileHandler(
                os.path.join(log_dir, f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"),
                encoding='utf-8'
            )
            fh.setFormatter(logging.Formatter(
                '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
            ))
            self._logger.addHandler(fh)

            ch = logging.StreamHandler()
            ch.setFormatter(logging.Formatter(
                '[%(levelname)s] %(message)s'
            ))
            self._logger.addHandler(ch)

    @classmethod
    def get(cls, log_dir: str = "output/logs") -> 'Logger':
        if cls._instance is None:
            cls._instance = cls(log_dir)
        return cls._instance

    def info(self, msg: str, **kwargs):
        self._logger.info(msg, extra=kwargs)

    def warning(self, msg: str, **kwargs):
        self._logger.warning(msg, extra=kwargs)

    def error(self, msg: str, **kwargs):
        self._logger.error(msg, extra=kwargs)

    def debug(self, msg: str, **kwargs):
        self._logger.debug(msg, extra=kwargs)

    def log_agent_call(
        self,
        agent_type: str,
        task: str,
        prompt_summary: str,
        sampling: Dict,
        result_summary: str,
        elapsed_ms: float,
    ):
        """记录Agent调用详情"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "agent": agent_type,
            "task": task,
            "prompt_summary": prompt_summary[:200],
            "sampling": sampling,
            "result_summary": result_summary[:200],
            "elapsed_ms": round(elapsed_ms, 1),
        }
        log_path = os.path.join(self.log_dir, "agent_calls.jsonl")
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def log_state_change(
        self,
        chapter_id: int,
        changes: Dict,
        conflict: Optional[Dict] = None,
    ):
        """记录世界状态变更"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "chapter_id": chapter_id,
            "changes": changes,
            "conflict": conflict,
        }
        log_path = os.path.join(self.log_dir, "state_changes.jsonl")
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

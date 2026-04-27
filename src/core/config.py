"""管线配置模块 - 解析 pipeline.config.json 并提供类型安全的配置访问"""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum


class AutonomyLevel(Enum):
    FULL_AUTO = "full_auto"
    SUGGESTED = "suggested"
    MUST_CONFIRM = "must_confirm"


@dataclass
class SamplingParams:
    temperature: float = 1.0
    top_p: float = 0.1
    top_k: int = 50
    alpha_presence: float = 0.0
    alpha_frequency: float = 0.0
    alpha_decay: float = 0.996
    max_tokens: int = 2048

    def validate(self):
        assert 0 < self.temperature <= 2.0, f"temperature {self.temperature} out of range (0, 2.0]"
        assert 0 <= self.top_p <= 1.0, f"top_p {self.top_p} out of range [0, 1.0]"
        assert self.top_k > 0, f"top_k {self.top_k} must be positive"
        assert self.max_tokens > 0, f"max_tokens {self.max_tokens} must be positive"


@dataclass
class APIConfig:
    base_url: str = "http://localhost:8000"
    api_key: str = ""
    model: str = "rwkv7-g1c-13.3b"
    model_path: str = ""
    vocab_path: str = ""
    port: int = 8000
    password: str = ""


@dataclass
class ConcurrencyConfig:
    max_batch_size: int = 960
    retry_max: int = 3
    retry_delay_ms: int = 1000

    def __post_init__(self):
        assert 1 <= self.max_batch_size <= 960, \
            f"max_batch_size {self.max_batch_size} out of range [1, 960]"


@dataclass
class PathsConfig:
    context_dir: str = "context"
    output_dir: str = "output"
    states_dir: str = "states"
    project_root: str = ""


@dataclass
class ReviewConfig:
    max_rewrite_attempts: int = 3


@dataclass
class PipelineConfig:
    api: APIConfig = field(default_factory=APIConfig)
    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    state_files: Dict[str, str] = field(default_factory=lambda: {
        "editor_planning": "states/editor_planning.st",
        "writer_novel": "states/writer_novel.st",
        "roleplay": "states/roleplay.st",
        "reviewer_factcheck": "states/reviewer_factcheck.st",
        "reviewer_narrative": "states/reviewer_narrative.st",
    })
    sampling: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
        "outline_gen": {"temperature": 1.0, "top_p": 0.1},
        "volume_gen": {"temperature": 1.2, "top_p": 0.15},
        "chapter_outline": {"temperature": 1.0, "top_p": 0.1},
        "chapter_content": {"temperature": 1.4, "top_p": 0.3},
        "roleplay": {"temperature": 1.3, "top_p": 0.25},
        "fact_check": {"temperature": 1.0, "top_p": 0.2},
        "narrative_review": {"temperature": 1.0, "top_p": 0.2},
    })
    autonomy: Dict[str, List[str]] = field(default_factory=lambda: {
        "full_auto": ["search_web", "query_world_state", "check_narrative_consistency",
                       "format_checker", "save_content"],
        "suggested": ["propose_state_change"],
        "must_confirm": ["resolve_conflict"],
    })
    paths: PathsConfig = field(default_factory=PathsConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)

    def get_sampling(self, task_type: str) -> SamplingParams:
        """按任务类型获取采样参数"""
        if task_type in self.sampling:
            params = self.sampling[task_type]
            return SamplingParams(
                temperature=params.get("temperature", 1.0),
                top_p=params.get("top_p", 0.1),
                top_k=params.get("top_k", 50),
                alpha_presence=params.get("alpha_presence", 0.0),
                alpha_frequency=params.get("alpha_frequency", 0.0),
                alpha_decay=params.get("alpha_decay", 0.996),
                max_tokens=params.get("max_tokens", 2048),
            )
        return SamplingParams()

    def get_state_file(self, agent_type: str) -> Optional[str]:
        """获取Agent对应的State文件路径"""
        return self.state_files.get(agent_type)

    def get_tool_autonomy(self, tool_name: str) -> AutonomyLevel:
        """获取工具的自主权级别"""
        for level, tools in self.autonomy.items():
            if tool_name in tools:
                return AutonomyLevel(level)
        return AutonomyLevel.FULL_AUTO

    def resolve_path(self, relative_path: str) -> str:
        """将相对路径解析为绝对路径"""
        if os.path.isabs(relative_path):
            return relative_path
        return os.path.join(self.paths.project_root, relative_path)


def _expand_env_vars(value: Any) -> Any:
    """递归替换字符串中的 ${ENV_VAR} 环境变量"""
    if isinstance(value, str):
        def replacer(match):
            env_var = match.group(1)
            result = os.environ.get(env_var, "")
            if not result:
                import warnings
                warnings.warn(f"Environment variable {env_var} not set")
            return result
        return re.sub(r'\$\{(\w+)\}', replacer, value)
    elif isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_expand_env_vars(v) for v in value]
    return value


def load_config(config_path: str) -> PipelineConfig:
    """从JSON文件加载管线配置"""
    with open(config_path, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    raw = _expand_env_vars(raw)
    project_root = os.path.dirname(os.path.abspath(config_path))

    config = PipelineConfig()

    # API
    if "api" in raw:
        api = raw["api"]
        config.api = APIConfig(
            base_url=api.get("base_url", config.api.base_url),
            api_key=api.get("api_key", config.api.api_key),
            model=api.get("model", config.api.model),
            model_path=api.get("model_path", config.api.model_path),
            vocab_path=api.get("vocab_path", config.api.vocab_path),
            port=api.get("port", config.api.port),
            password=api.get("password", config.api.password),
        )

    # Concurrency
    if "concurrency" in raw:
        cc = raw["concurrency"]
        config.concurrency = ConcurrencyConfig(
            max_batch_size=cc.get("max_batch_size", 960),
            retry_max=cc.get("retry_max", 3),
            retry_delay_ms=cc.get("retry_delay_ms", 1000),
        )

    # State files
    if "state_files" in raw:
        config.state_files = raw["state_files"]

    # Sampling
    if "sampling" in raw:
        config.sampling = raw["sampling"]

    # Autonomy
    if "autonomy" in raw:
        config.autonomy = raw["autonomy"]

    # Paths
    if "paths" in raw:
        p = raw["paths"]
        config.paths = PathsConfig(
            context_dir=p.get("context_dir", "context"),
            output_dir=p.get("output_dir", "output"),
            states_dir=p.get("states_dir", "states"),
            project_root=project_root,
        )
    else:
        config.paths.project_root = project_root

    # Review
    if "review" in raw:
        config.review = ReviewConfig(
            max_rewrite_attempts=raw["review"].get("max_rewrite_attempts", 3),
        )

    return config

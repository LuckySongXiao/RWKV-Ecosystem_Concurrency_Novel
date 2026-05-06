"""RWKV 模型服务管理器

自动检测、启动和管理 RWKV Lightning 推理服务。

重要: 使用全局单例 _global_manager 保持服务进程的生命周期，
避免局部变量被垃圾回收导致 __del__ 提前停止服务。
"""

import os
import subprocess
import time
import requests
import sys
import re
from typing import Dict, List, Optional
from pathlib import Path


class RWKVServiceManager:
    """RWKV 模型服务管理器"""

    def __init__(self, project_root: str = None):
        self.project_root = project_root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.lightning_dir = os.path.join(self.project_root, "rwkv_lightning_libtorch_win")
        self.model_dir = os.path.join(self.project_root, "rwkv_models")
        self.model_path: Optional[str] = None
        self.vocab_path = os.path.join(self.lightning_dir, "rwkv_vocab_v20230424.txt")
        self.server_exe = os.path.join(self.lightning_dir, "rwkv_lightning.exe")
        self.port = 8000
        self.process: Optional[subprocess.Popen] = None
        self._started_by_us = False

    def scan_available_models(self) -> List[Dict]:
        """扫描 rwkv_models 目录，返回可用模型列表

        Returns:
            模型信息列表，每项包含:
            - name: 文件名
            - path: 完整路径
            - size_mb: 文件大小(MB)
            - format: 格式 (st/pt/pt​h)
            - size_label: 参数规模标签 (如 13.3b, 2.9b)
            - version: 模型版本 (如 g1c, g1f)
            - ctx: 上下文长度
            - date: 日期
            - is_recommended: 是否推荐（.st 格式优先）
        """
        if not os.path.exists(self.model_dir):
            return []

        models = []
        for fname in os.listdir(self.model_dir):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in ('.st', '.pth', '.pt'):
                continue

            fpath = os.path.join(self.model_dir, fname)
            if not os.path.isfile(fpath):
                continue

            size_bytes = os.path.getsize(fpath)
            size_mb = round(size_bytes / (1024 * 1024), 1)

            info = {
                "name": fname,
                "path": fpath,
                "size_mb": size_mb,
                "format": ext.lstrip('.'),
                "size_label": "",
                "version": "",
                "ctx": "",
                "date": "",
                "is_recommended": ext == '.st',
                "has_st": False,
                "st_path": None,
            }

            m = re.search(r'(\d+\.?\d*b)', fname)
            if m:
                info["size_label"] = m.group(1)

            m = re.search(r'(g\d+[a-z]?)', fname)
            if m:
                info["version"] = m.group(1)

            m = re.search(r'ctx(\d+)', fname)
            if m:
                info["ctx"] = m.group(1)

            m = re.search(r'(\d{8})', fname)
            if m:
                info["date"] = m.group(1)

            if ext in ('.pth', '.pt'):
                st_path = self._find_corresponding_st(fpath)
                if st_path:
                    info["has_st"] = True
                    info["st_path"] = st_path

            models.append(info)

        models.sort(key=lambda m: (not m["is_recommended"], m["size_mb"]), reverse=False)

        if models:
            has_st = any(m["format"] == "st" for m in models)
            for m in models:
                m["is_recommended"] = m["format"] == "st" if has_st else m == models[-1]

        return models

    def _find_corresponding_st(self, pth_path: str) -> Optional[str]:
        """查找与 .pth 文件同名的 .st 文件

        例如 rwkv7-g1f-2.9b-20260420-ctx8192.pth
        查找 rwkv7-g1f-2.9b-20260420-ctx8192.st
        """
        base = os.path.splitext(pth_path)[0]
        st_path = base + ".st"
        if os.path.exists(st_path):
            return st_path
        return None

    def _ensure_conversion_deps(self, convert_script: str):
        """检测并安装模型转换所需的依赖

        convert_model.py 需要: torch, safetensors
        convert_safetensors.py 需要: torch, safetensors, numpy
        """
        script_name = os.path.basename(convert_script)
        required = ["torch", "safetensors"]
        if "safetensors" in script_name and "convert_model" not in script_name:
            required.append("numpy")

        missing = []
        for pkg in required:
            try:
                __import__(pkg)
            except ImportError:
                missing.append(pkg)

        if not missing:
            return

        print(f"[INFO] 检测到缺失依赖: {', '.join(missing)}，正在自动安装...")
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install"] + missing,
                capture_output=True,
                text=True,
                timeout=300,
            )
            print(f"[INFO] 依赖安装完成: {', '.join(missing)}")
        except Exception as e:
            print(f"[WARNING] 自动安装依赖失败: {e}")
            print(f"[INFO] 请手动安装: pip install {' '.join(missing)}")

    def _convert_pth_to_st(self, pth_path: str) -> tuple[bool, str]:
        """将 .pth 模型转换为 .st (safetensors) 格式

        优先使用 convert_safetensors.py（做RWKV权重变换 + float16转换），
        其次使用 convert_model.py（基础转换，也支持bfloat16→float16）。
        转换前自动检测并安装缺失的依赖。

        Args:
            pth_path: .pth 文件路径

        Returns:
            (成功与否, .st文件路径或错误信息)
        """
        st_path = os.path.splitext(pth_path)[0] + ".st"

        convert_script = os.path.join(self.project_root, "scripts", "convert_safetensors.py")
        if not os.path.exists(convert_script):
            convert_script = os.path.join(self.project_root, "scripts", "convert_model.py")
        if not os.path.exists(convert_script):
            return False, "未找到模型转换脚本 (scripts/convert_model.py 或 scripts/convert_safetensors.py)"

        self._ensure_conversion_deps(convert_script)

        print(f"[INFO] 正在转换模型: {os.path.basename(pth_path)} -> {os.path.basename(st_path)}")
        print(f"[INFO] 转换脚本: {convert_script}")
        print(f"[INFO] 大模型转换可能需要数分钟，请耐心等待...")

        try:
            result = subprocess.run(
                [sys.executable, convert_script, "--input", pth_path, "--output", st_path],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=1800,
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip() or result.stdout.strip()
                print(f"[ERROR] 模型转换失败:\n{error_msg}")
                if os.path.exists(st_path):
                    try:
                        os.remove(st_path)
                    except Exception:
                        pass
                return False, f"模型转换失败: {error_msg[:200]}"

            if not os.path.exists(st_path):
                return False, "转换完成但未生成 .st 文件"

            st_size_mb = os.path.getsize(st_path) / (1024 * 1024)
            print(f"[INFO] 模型转换完成! 输出文件: {st_path} ({st_size_mb:.1f} MB)")
            return True, st_path

        except subprocess.TimeoutExpired:
            print(f"[ERROR] 模型转换超时 (30分钟)")
            return False, "模型转换超时 (30分钟)"
        except Exception as e:
            print(f"[ERROR] 模型转换异常: {e}")
            return False, f"模型转换异常: {e}"

    def _ensure_st_model(self, model_path: str) -> tuple[str, Optional[str]]:
        """确保模型是 .st 格式，如需要则自动转换

        Args:
            model_path: 原始模型路径

        Returns:
            (最终使用的模型路径, 转换状态消息)
            转换状态消息为 None 表示无需转换
        """
        ext = os.path.splitext(model_path)[1].lower()

        if ext == '.st':
            return model_path, None

        if ext in ('.pth', '.pt'):
            st_path = self._find_corresponding_st(model_path)
            if st_path:
                print(f"[INFO] 找到对应的 .st 文件: {os.path.basename(st_path)}，无需转换")
                return st_path, f"已自动选择 .st 格式: {os.path.basename(st_path)}"

            print(f"[INFO] 未找到对应的 .st 文件，需要将 .{ext} 转换为 .st 格式")
            success, result = self._convert_pth_to_st(model_path)
            if success:
                return result, f"模型已自动转换: {os.path.basename(result)}"
            else:
                return model_path, f"自动转换失败: {result}，将尝试使用原始 .{ext} 文件"

        return model_path, None

    def set_model(self, model_path: str):
        """设置要使用的模型路径"""
        self.model_path = model_path

    def get_current_model(self) -> Optional[str]:
        """获取当前使用的模型路径"""
        return self.model_path

    def is_service_running(self) -> bool:
        """检查 RWKV 服务是否正在运行
        
        检测策略：先检查端口监听（轻量快速），再尝试HTTP探测
        """
        if self._is_port_listening():
            return True
        try:
            endpoints = [
                f"http://localhost:{self.port}/health",
                f"http://localhost:{self.port}/v1/chat/completions",
            ]
            for endpoint in endpoints:
                try:
                    resp = requests.get(endpoint, timeout=2)
                    if resp.status_code in [200, 405]:
                        return True
                except Exception:
                    continue
            return False
        except Exception:
            return False

    def _get_gpu_vram_mb(self) -> Optional[int]:
        """获取当前 GPU 显存占用（MB），失败返回 None"""
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                first_line = r.stdout.strip().splitlines()[0].strip()
                return int(first_line)
        except Exception:
            pass
        return None

    def _is_port_listening(self) -> bool:
        """检查服务端口是否在监听"""
        try:
            r = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    if f":{self.port}" in line and "LISTENING" in line:
                        return True
        except Exception:
            pass
        return False

    def _test_chat_completion(self) -> bool:
        """发送测试对话请求，验证模型是否能正常回复"""
        try:
            resp = requests.post(
                f"http://localhost:{self.port}/v1/chat/completions",
                json={
                    "contents": ["User: Hi\n\nAssistant: "],
                    "max_tokens": 8,
                    "temperature": 0.5,
                    "stream": False,
                },
                timeout=60,
            )
            if resp.status_code == 200:
                return True
            if resp.status_code == 500:
                return False
        except Exception:
            pass

        try:
            resp = requests.post(
                f"http://localhost:{self.port}/openai/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 8,
                    "temperature": 0.5,
                },
                timeout=60,
            )
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices and choices[0].get("message", {}).get("content", "").strip():
                    return True
        except Exception:
            pass
        return False

    def is_service_ready(self) -> bool:
        """综合判断服务是否就绪：显存占用 + 测试对话
        
        判定逻辑:
        1. 显存占用 >= 500MB → 模型已加载到GPU
        2. 测试对话能收到回复 → 模型可正常推理
        两者都满足才返回 True
        """
        vram = self._get_gpu_vram_mb()
        if vram is not None and vram < 500:
            return False
        if not self._test_chat_completion():
            return False
        return True

    DEFAULT_MODEL_NAME = "rwkv7-g1f-2.9b-20260420-ctx8192"

    def check_prerequisites(self) -> tuple[bool, str]:
        """检查启动前置条件"""
        if not os.path.exists(self.server_exe):
            return False, f"RWKV Lightning 可执行文件不存在: {self.server_exe}"

        if not self.model_path:
            models = self.scan_available_models()
            if not models:
                return False, f"模型目录为空: {self.model_dir}"

            default_match = [m for m in models if self.DEFAULT_MODEL_NAME in m["name"]]
            if default_match:
                st_match = [m for m in default_match if m["format"] == "st"]
                self.model_path = st_match[0]["path"] if st_match else default_match[0]["path"]
            else:
                recommended = [m for m in models if m["is_recommended"]]
                self.model_path = recommended[0]["path"] if recommended else models[-1]["path"]

        if not os.path.exists(self.model_path):
            return False, (
                f"模型文件不存在: {self.model_path}\n"
                f"请先运行模型转换:\n"
                f"  python scripts/convert_model.py --input <.pth文件> --output <.st文件>"
            )

        if not os.path.exists(self.vocab_path):
            return False, f"Vocab 文件不存在: {self.vocab_path}"

        return True, "OK"

    def start_service(self, wait_ready: bool = True, timeout: int = 120) -> bool:
        """启动 RWKV 推理服务

        Args:
            wait_ready: 是否等待服务就绪
            timeout: 等待超时时间（秒），大模型需要更长时间

        Returns:
            是否成功启动
        """
        if self.is_service_running():
            print(f"[INFO] RWKV 服务已在运行 (端口 {self.port})")
            return True

        ok, msg = self.check_prerequisites()
        if not ok:
            print(f"[ERROR] {msg}")
            return False

        final_model_path, convert_msg = self._ensure_st_model(self.model_path)
        if final_model_path != self.model_path:
            self.model_path = final_model_path
        if convert_msg:
            print(f"[INFO] {convert_msg}")

        print("=" * 60)
        print(" 启动 RWKV Lightning 模型服务")
        print("=" * 60)
        print(f"  模型: {self.model_path}")
        print(f"  Vocab: {self.vocab_path}")
        print(f"  端口: {self.port}")
        print("=" * 60)

        try:
            self.process = subprocess.Popen(
                [
                    self.server_exe,
                    "--model-path", self.model_path,
                    "--vocab-path", self.vocab_path,
                    "--port", str(self.port),
                ],
                cwd=self.lightning_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
            )
            self._started_by_us = True
            print(f"[INFO] RWKV 服务已启动 (PID: {self.process.pid})")
        except Exception as e:
            print(f"[ERROR] 启动 RWKV 服务失败: {e}")
            return False

        if wait_ready:
            print(f"[INFO] 等待服务就绪 (超时: {timeout}秒)...")
            start_time = time.time()
            model_loaded = False
            while time.time() - start_time < timeout:
                vram = self._get_gpu_vram_mb()
                vram_info = f" [显存: {vram}MB]" if vram is not None else ""

                if not model_loaded:
                    if vram is not None and vram >= 500:
                        model_loaded = True
                        print(f"\n[INFO] 模型已加载到GPU{vram_info}")
                    elif vram is None and self._is_port_listening() and self.is_service_running():
                        model_loaded = True
                        print(f"\n[INFO] 服务端口已响应")
                    elif self._is_port_listening():
                        print(f"\r[INFO] 端口已监听，等待模型加载{vram_info}...", end="", flush=True)
                    else:
                        print(".", end="", flush=True)
                    time.sleep(3)
                    continue

                if model_loaded:
                    print(f"[INFO] 模型已加载{vram_info}，发送测试对话...")
                    if self._test_chat_completion():
                        print(f"[INFO] RWKV 服务已就绪{vram_info}")
                        return True
                    else:
                        print(f"[INFO] 测试对话未响应，继续等待{vram_info}...")
                        time.sleep(5)
                        continue

            vram = self._get_gpu_vram_mb()
            vram_info = f" [显存: {vram}MB]" if vram is not None else ""
            if model_loaded:
                print(f"\n[WARNING] 模型已加载但测试对话超时{vram_info}")
                print("[WARNING] 可能原因: 模型格式不兼容（如BFloat16），请用 convert_safetensors.py 重新转换")
                print("[WARNING] 命令: python scripts/convert_safetensors.py --input <.pth> --output <.st>")
            else:
                print(f"\n[WARNING] 服务启动超时{vram_info}")
            return False

        return True

    def stop_service(self):
        """停止 RWKV 服务（仅停止由本管理器启动的服务）"""
        if not self._started_by_us:
            return
        if self.process:
            print(f"[INFO] 正在停止 RWKV 服务 (PID: {self.process.pid})...")
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
                print("[INFO] RWKV 服务已停止")
            except Exception as e:
                print(f"[WARNING] 停止服务时出错: {e}")
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
            self._started_by_us = False


_global_manager: Optional[RWKVServiceManager] = None


def get_service_manager(project_root: str = None) -> RWKVServiceManager:
    """获取全局服务管理器单例"""
    global _global_manager
    if _global_manager is None:
        _global_manager = RWKVServiceManager(project_root)
    return _global_manager


def scan_available_models(project_root: str = None) -> List[Dict]:
    """扫描可用模型列表"""
    return get_service_manager(project_root).scan_available_models()


def ensure_rwkv_service(project_root: str = None, model_path: str = None) -> bool:
    """确保 RWKV 服务正在运行且推理可用，如果未运行则自动启动

    Args:
        project_root: 项目根目录路径
        model_path: 指定模型路径（可选，不指定则自动选择推荐模型）

    Returns:
        服务是否可用
    """
    global _global_manager

    if _global_manager is None:
        _global_manager = RWKVServiceManager(project_root)

    if model_path:
        _global_manager.set_model(model_path)

    if _global_manager.is_service_running():
        if _global_manager._test_chat_completion():
            return True
        print("[WARNING] RWKV 服务在运行但推理不可用，尝试重启...")
        _global_manager.stop_service()

    return _global_manager.start_service(wait_ready=True, timeout=180)


def shutdown_rwkv_service():
    """显式关闭 RWKV 服务（仅在程序退出时调用）"""
    global _global_manager
    if _global_manager is not None:
        _global_manager.stop_service()
        _global_manager = None

"""模型转换脚本 - 将 .pth 格式转换为 .st (safetensors) 格式

rwkv_lightning 需要 safetensors 格式的模型文件。
本脚本将 PyTorch .pth checkpoint 转换为 .st 格式。

用法:
    python scripts/convert_model.py --input rwkv_models/rwkv7-g1c-13.3b-20251231-ctx8192.pth --output rwkv_models/rwkv7-g1c-13.3b-20251231-ctx8192.st
"""

import argparse
import os
import sys


def convert_pth_to_safetensors(input_path: str, output_path: str):
    """将 .pth 转换为 .st (safetensors) 格式"""
    try:
        import torch
        from safetensors.torch import save_file
    except ImportError:
        print("Error: 需要安装 torch 和 safetensors")
        print("  pip install torch safetensors")
        print("")
        print("注意: Python 3.14 可能不被 PyTorch 支持，请使用 Python 3.10-3.12")
        sys.exit(1)

    print(f"Loading model from {input_path}...")
    print(f"File size: {os.path.getsize(input_path) / 1024 / 1024 / 1024:.2f} GB")

    state_dict = torch.load(input_path, map_location="cpu", weights_only=True)

    print(f"Loaded {len(state_dict)} tensors")

    # 转换为 safetensors 格式
    # safetensors 要求所有张量是 contiguous 的
    converted = {}
    for key, tensor in state_dict.items():
        if isinstance(tensor, torch.Tensor):
            converted[key] = tensor.contiguous()
        else:
            print(f"  Skipping non-tensor key: {key}")

    print(f"Saving to {output_path}...")
    save_file(converted, output_path)

    print(f"Output size: {os.path.getsize(output_path) / 1024 / 1024 / 1024:.2f} GB")
    print("Conversion complete!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert RWKV .pth model to .st (safetensors) format")
    parser.add_argument("--input", required=True, help="Input .pth file path")
    parser.add_argument("--output", required=True, help="Output .st file path")
    args = parser.parse_args()

    convert_pth_to_safetensors(args.input, args.output)

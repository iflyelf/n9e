#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 Nightingale 告警通知脚本的 ConfigMap YAML。

用法:
    python3 gen-scripts-configmap.py

说明:
    读取 scripts/ 目录下所有 *.py 脚本，打包为单个 ConfigMap，
    输出到 manifests/n9e-scripts-configmap.yaml。
    每次修改 scripts/ 下的脚本后，需重新运行本命令再执行 helm upgrade。
"""

import os

# 脚本目录和输出路径（相对本文件所在目录）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
OUTPUT_FILE = os.path.join(BASE_DIR, "manifests", "n9e-scripts-configmap.yaml")

# ConfigMap 元数据
CONFIGMAP_NAME = "n9e-scripts"
NAMESPACE = "n9e"


def main() -> None:
    files = sorted(f for f in os.listdir(SCRIPTS_DIR) if f.endswith(".py"))
    if not files:
        raise SystemExit(f"未在 {SCRIPTS_DIR} 找到任何 .py 脚本")

    lines = [
        "# =============================================================================",
        "# Nightingale 告警通知脚本 ConfigMap",
        "# 自动生成：由 scripts/*.py 打包而成，请勿手动编辑脚本内容",
        "# 重新生成：python3 gen-scripts-configmap.py",
        "# =============================================================================",
        "apiVersion: v1",
        "kind: ConfigMap",
        "metadata:",
        f"  name: {CONFIGMAP_NAME}",
        f"  namespace: {NAMESPACE}",
        "  labels:",
        "    app.kubernetes.io/name: nightingale",
        "    app.kubernetes.io/component: notify-scripts",
        "data:",
    ]

    for fn in files:
        lines.append(f"  {fn}: |")
        with open(os.path.join(SCRIPTS_DIR, fn), encoding="utf-8") as fh:
            for line in fh.read().splitlines():
                # ConfigMap block scalar 缩进 4 空格
                lines.append("    " + line)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"✅ 已生成 {OUTPUT_FILE}")
    print(f"   打包脚本: {', '.join(files)}")


if __name__ == "__main__":
    main()

# ArtifactFit-MM

*按固定契约和资源边界预检多媒体研究工件的工具。*


[English](README.md) | [简体中文](README.zh-CN.md)

**导航：**[状态](#状态) · [从源码安装](#从源码安装) · [命令行](#命令行) · [范围](#纵向切片范围)


ArtifactFit-MM 是一个确定性预检工具，用来判断固定的研究工件在指定机器和资源边界下，实际能够到达哪个工程阶段。

它不生成或修复环境，不修改科学代码，不授予工件评审徽章，也不声称复现了论文结果。

## 状态

仓库当前发布的是公开 alpha 纵向切片，供源码审阅使用。它不是带标签的正式版本，也不授权科学实验或论文提交。

## 从源码安装

Python 3.11 是主要支持版本。安装 uv 后：

~~~text
git clone https://github.com/Rainflowers686/artifactfit-mm.git
cd artifactfit-mm
uv sync --extra dev --locked
uv run artifactfit doctor
~~~

通常只有依赖解析需要网络。

## 收据与边界

每份收据都会标记：

- ENGINEERING_FEASIBILITY_ONLY: true
- SCIENTIFIC_RESULT: false
- PAPER_CLAIM_REPRODUCED: not_assessed

plan 不执行外部代码；网络默认由契约关闭。运行时限制会按指标报告为强制执行、软监控或不可用。重放时若契约、提交、命令或直接引用的命令文件发生变化，操作会失败关闭。

收据会记录主机、环境、仓库和命令元数据。分享前请检查其中的本地路径、主机名和环境覆盖项；不要把凭据放进契约或阶段环境。可从[最小契约](examples/minimal_contract.yaml)开始，再阅读[契约指南](docs/contract-guide.md)与[执行限制](docs/enforcement-limitations.md)。

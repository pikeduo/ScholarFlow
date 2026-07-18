"""允许通过 ``python -m evaluation`` 运行离线评测、通用或 PaSa 金标导入及受控候选导出。"""

from evaluation.cli import main  # 导入命令入口。


if __name__ == "__main__":  # 仅直接执行模块时启动 CLI。
    raise SystemExit(main())  # 将整数返回码传递给操作系统。

"""允许通过 ``python -m evaluation`` 运行完全离线评测。"""

from evaluation.cli import main  # 导入命令入口。


if __name__ == "__main__":  # 仅直接执行模块时启动 CLI。
    raise SystemExit(main())  # 将整数返回码传递给操作系统。

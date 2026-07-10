"""配置同时写入控制台和滚动文件的应用日志。"""

import logging  # 使用 Python 标准日志框架。
from logging.handlers import RotatingFileHandler  # 防止日志文件无限增长。

from app.core.config import settings  # 读取日志目录和级别配置。


def configure_logging() -> logging.Logger:
    """创建 ScholarFlow 命名日志器并避免重复添加处理器。

    返回：
        logging.Logger：已配置控制台和文件输出的日志器。
    """
    application_logger = logging.getLogger("scholarflow")  # 使用固定名称便于统一筛选日志。
    if application_logger.handlers:  # 在热重载或重复导入时复用现有处理器。
        return application_logger  # 避免同一条消息重复输出。

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)  # 将文本级别安全转换为常量。
    application_logger.setLevel(log_level)  # 设置日志器接收的最低级别。
    application_logger.propagate = False  # 避免被根日志器二次输出。
    settings.log_dir.mkdir(parents=True, exist_ok=True)  # 在首次运行时创建日志目录。

    formatter = logging.Formatter(  # 统一控制台和文件的日志格式。
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"  # 保留时间、级别、来源与内容。
    )
    console_handler = logging.StreamHandler()  # 创建同步输出到控制台的处理器。
    console_handler.setLevel(log_level)  # 使用与应用一致的输出级别。
    console_handler.setFormatter(formatter)  # 应用统一格式。
    file_handler = RotatingFileHandler(  # 创建可滚动的本地日志文件处理器。
        settings.log_dir / "scholarflow.log",  # 将所有基础工程日志写入统一文件。
        maxBytes=5 * 1024 * 1024,  # 单个日志文件上限设为 5 MiB。
        backupCount=5,  # 最多保留五个历史日志文件。
        encoding="utf-8",  # 保证中文日志可正确保存。
    )
    file_handler.setLevel(log_level)  # 使用与控制台一致的输出级别。
    file_handler.setFormatter(formatter)  # 应用统一格式。
    application_logger.addHandler(console_handler)  # 注册控制台处理器。
    application_logger.addHandler(file_handler)  # 注册文件处理器。
    return application_logger  # 返回已完成配置的日志器。


logger = configure_logging()  # 初始化供全项目导入的日志器。

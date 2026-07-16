"""配置应用与 Uvicorn 同时写入控制台和滚动文件的统一日志。"""

import logging  # 使用 Python 标准日志框架统一处理应用和服务器输出。
import sys  # 仅在当前解释器内识别 pytest，避免测试污染生产运行日志。
from collections.abc import Sequence  # 接收可测试的服务器日志器名称集合。
from logging.handlers import RotatingFileHandler  # 防止日志文件无限增长。

from backend.app.core.config import Settings, settings  # 读取日志目录、级别与测试隔离配置。


LOG_FILE_NAME = "scholarflow.log"  # 使用稳定文件名便于开发者和诊断工具直接定位。
TEST_LOG_FILE_NAME = "scholarflow-test.log"  # 将 pytest 的预期故障注入与实际服务日志物理隔离。
UVICORN_LOGGER_NAMES = ("uvicorn", "uvicorn.error", "uvicorn.access")  # 覆盖启动、框架异常和 HTTP 访问日志。


def resolve_default_log_file_name(is_pytest_process: bool | None = None) -> str:
    """根据当前执行上下文返回默认日志文件名。

    参数：
        is_pytest_process：可注入的 pytest 上下文，未提供时从当前解释器模块判断。
    返回：
        str：pytest 返回测试专用文件名，其他运行返回实际服务日志文件名。
    """
    if is_pytest_process is None:  # 仅在未注入上下文时读取当前解释器状态。
        is_pytest_process = "pytest" in sys.modules  # pytest 在收集测试模块前已加载到当前解释器。
    return TEST_LOG_FILE_NAME if is_pytest_process else LOG_FILE_NAME  # 保证两类日志绝不共用同一文件。


def configure_logging(
    config: Settings = settings,
    logger_name: str = "scholarflow",
    related_logger_names: Sequence[str] = UVICORN_LOGGER_NAMES,
    log_file_name: str = LOG_FILE_NAME,
) -> logging.Logger:
    """创建应用日志器，并让 Uvicorn 复用相同的控制台和滚动文件处理器。

    参数：
        config：包含日志目录和级别的集中配置，测试可注入隔离实例。
        logger_name：应用日志器名称，默认使用稳定的 scholarflow。
        related_logger_names：需要写入同一文件的服务器日志器名称。
        log_file_name：当前日志器写入的文件名，默认保留实际服务日志路径。
    返回：
        logging.Logger：已配置控制台、UTF-8 文件和服务器日志转发的应用日志器。
    """
    application_logger = logging.getLogger(logger_name)  # 使用固定或测试注入名称统一筛选应用日志。
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)  # 将文本级别安全转换为标准常量。
    application_logger.setLevel(log_level)  # 设置日志器接收的最低级别。
    application_logger.propagate = False  # 避免被根日志器再次输出造成重复记录。
    if not application_logger.handlers:  # 热重载或重复导入时复用已经打开的文件句柄。
        config.log_dir.mkdir(parents=True, exist_ok=True)  # 在首次运行时创建受 Git 忽略的日志目录。
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")  # 统一时间、级别、来源和正文格式。
        console_handler = logging.StreamHandler()  # 保留现有控制台实时输出体验。
        console_handler.setLevel(log_level)  # 使用与应用一致的输出级别。
        console_handler.setFormatter(formatter)  # 让控制台与日志文件可直接对照。
        file_handler = RotatingFileHandler(  # 创建按体积滚动的本地 UTF-8 日志文件。
            config.log_dir / log_file_name,  # 将当前执行上下文的后端信息写入独立文件。
            maxBytes=5 * 1024 * 1024,  # 单个日志文件上限为 5 MiB，避免长期运行无限增长。
            backupCount=5,  # 最多保留五个历史文件供近期错误追踪。
            encoding="utf-8",  # 保证 Windows 下中文日志可正确读取。
        )
        file_handler.setLevel(log_level)  # 文件记录与控制台使用相同最低级别。
        file_handler.setFormatter(formatter)  # 应用统一可检索格式。
        application_logger.addHandler(console_handler)  # 注册控制台处理器。
        application_logger.addHandler(file_handler)  # 注册滚动文件处理器。
    _share_handlers_with_related_loggers(application_logger, related_logger_names, log_level)  # 在 Uvicorn 完成默认配置后接管其输出。
    return application_logger  # 返回供全项目复用的统一日志器。


def _share_handlers_with_related_loggers(
    application_logger: logging.Logger,
    related_logger_names: Sequence[str],
    log_level: int,
) -> None:
    """让服务器日志器直接复用应用处理器，确保控制台内容同步写入文件。

    参数：
        application_logger：已经配置控制台和滚动文件的应用日志器。
        related_logger_names：需要纳入统一输出的日志器名称。
        log_level：所有相关日志器使用的最低级别。
    """
    shared_handlers = list(application_logger.handlers)  # 复制处理器引用列表，避免后续外部原地修改。
    for related_logger_name in related_logger_names:  # 逐个接管 Uvicorn 主日志、异常日志和访问日志。
        related_logger = logging.getLogger(related_logger_name)  # 获取 Uvicorn 已创建或尚未使用的命名日志器。
        related_logger.handlers = list(shared_handlers)  # 替换仅控制台处理器，使相同消息也进入滚动文件。
        related_logger.setLevel(log_level)  # 与应用日志级别保持一致。
        related_logger.propagate = False  # 阻止父子日志器同时处理同一消息造成重复行。


logger = configure_logging(log_file_name=resolve_default_log_file_name())  # pytest 写入测试专用文件，服务运行仍接管正式日志。

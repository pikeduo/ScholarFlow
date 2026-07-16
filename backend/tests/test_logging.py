"""验证应用与服务器日志会同时写入控制台和 UTF-8 滚动文件。"""

import logging  # 构造隔离日志器并在测试后释放处理器。
from logging.handlers import RotatingFileHandler  # 识别全局滚动文件处理器的实际目标文件。
from pathlib import Path  # 标注 pytest 临时目录类型。

from backend.app.core.config import Settings  # 构造不读取本地 .env 的隔离日志配置。
from backend.app.core.logging import LOG_FILE_NAME, TEST_LOG_FILE_NAME, configure_logging, logger, resolve_default_log_file_name  # 导入待测统一日志配置入口。


def test_default_log_file_name_separates_pytest_and_service_contexts() -> None:
    """pytest 与实际服务必须选择不同的默认日志文件名。"""
    assert resolve_default_log_file_name(is_pytest_process=True) == TEST_LOG_FILE_NAME  # 验证测试上下文只写入测试专用文件。
    assert resolve_default_log_file_name(is_pytest_process=False) == LOG_FILE_NAME  # 验证服务上下文继续使用稳定正式日志文件。


def test_module_logger_uses_test_log_file_under_pytest() -> None:
    """pytest 导入的全局应用日志器不得占用实际服务日志文件。"""
    file_names = {Path(handler.baseFilename).name for handler in logger.handlers if isinstance(handler, RotatingFileHandler)}  # 收集全局日志器所有滚动文件目标。
    assert file_names == {TEST_LOG_FILE_NAME}  # 验证测试过程只会写入独立的测试日志文件。


def test_configure_logging_saves_application_and_server_messages(tmp_path: Path) -> None:
    """应用日志与 Uvicorn 类服务器日志应进入同一个 UTF-8 文件且不重复。"""
    logger_name = "test-scholarflow-logging"  # 使用唯一名称避免影响生产应用日志器。
    server_logger_names = ("test-uvicorn", "test-uvicorn.error", "test-uvicorn.access")  # 使用隔离名称模拟三类 Uvicorn 输出。
    config = Settings(_env_file=None, log_dir=tmp_path / "logs", log_level="INFO")  # 将文件输出限制在 pytest 临时目录。
    application_logger = configure_logging(config=config, logger_name=logger_name, related_logger_names=server_logger_names)  # 配置隔离应用和服务器日志器。
    try:  # 确保 Windows 下测试结束前关闭文件句柄。
        application_logger.info("应用阶段完成")  # 写入普通应用日志。
        logging.getLogger("test-uvicorn.error").info("服务器启动完成")  # 模拟 Uvicorn 启动日志。
        logging.getLogger("test-uvicorn.access").info('127.0.0.1 - "POST /api/v1/search/multi-source HTTP/1.1" 200')  # 模拟访问日志。
        for handler in application_logger.handlers:  # 刷新所有共享处理器确保内容已落盘。
            handler.flush()  # 将缓冲内容写入临时日志文件。
        log_text = (config.log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")  # 使用显式 UTF-8 读取验证文件。
        assert log_text.count("应用阶段完成") == 1  # 验证应用消息只记录一次。
        assert log_text.count("服务器启动完成") == 1  # 验证服务器启动消息已保存且未重复。
        assert log_text.count("POST /api/v1/search/multi-source") == 1  # 验证 HTTP 访问日志已保存且未重复。
    finally:  # 清理隔离日志器，避免文件句柄污染其他测试。
        unique_handlers = set(application_logger.handlers)  # 处理器被多个日志器共享，只关闭一次。
        for logger_to_clean in [application_logger, *(logging.getLogger(name) for name in server_logger_names)]:  # 遍历应用和服务器日志器。
            logger_to_clean.handlers = []  # 先解除全部共享处理器引用。
        for handler in unique_handlers:  # 逐个释放控制台和文件资源。
            handler.close()  # 关闭 Windows 文件句柄以允许临时目录删除。

"""验证 PaSa 候选快照批处理脚本保留用户授权、单批边界与离线复核。"""

import codecs  # 校验脚本使用 Windows PowerShell 可识别的 UTF-8 BOM 编码。
from pathlib import Path  # 定位仓库内由用户显式执行的 PowerShell 脚本。


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]  # 从评测测试目录稳定回到仓库根目录。
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "export_pasa_snapshot_batch.ps1"  # 指向待核验的批处理脚本。


def test_pasa_snapshot_batch_script_requires_explicit_bounded_batch_and_validates_outputs() -> None:
    """脚本应默认单条、显式授权导出，并在每条后执行离线快照校验。"""
    assert SCRIPT_PATH.read_bytes().startswith(codecs.BOM_UTF8)  # Windows PowerShell 5.1 必须以 BOM 识别中文注释与逐行语法边界。
    script = SCRIPT_PATH.read_text(encoding="utf-8")  # 仅读取脚本文本，不执行 PowerShell、网络、模型或来源调用。
    assert "[int]$BatchSize = 1" in script  # 默认批量必须为一条，避免一次误消耗全部开发集来源配额。
    assert "[ValidateRange(1, 20)]" in script  # 批量必须受当前已封存开发集规模上限约束。
    assert "--allow-online-sources" in script  # 在线导出仍必须显式透传既有授权开关。
    assert "snapshot-check --snapshots $outputPath" in script  # 每条新快照必须在继续前进行完全离线校验。
    assert "学术来源降级" in script  # 历史全部来源失败产物不得被误判为可复用成功快照。
    assert "BatchSize=$BatchSize" in script  # 调用前必须向用户显示精确批量范围。

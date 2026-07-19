<#
.SYNOPSIS
    按已封存 QueryIntent manifest 每次导出有限数量的 PaSa 排序前候选快照。

.DESCRIPTION
    本脚本只在用户手动运行时调用 snapshot-export。默认 BatchSize 为 1，先跳过已有的
    无来源降级且通过 snapshot-check 的快照，再导出下一条查询；每条导出后立即进行离线校验。
    脚本不读取 .env、不保存 Token，也不加载模型；底层 snapshot-export 仅因本脚本显式传入
    --allow-online-sources 才会按其既有边界读取生产来源配置并调用学术 API。
#>

# BatchSize 默认一条；其余参数分别定位已封存 manifest、独立输出目录和稳定快照 ID 前缀。
[CmdletBinding()]
param(
    [ValidateRange(1, 20)]
    [int]$BatchSize = 1,

    [string]$QueryIntentManifest = "evaluation/inputs/pasa-auto-dev-ranking20-query-intents.manifest.json",

    [string]$SnapshotDirectory = "evaluation/inputs/pasa-auto-dev-ranking20-snapshots",

    [string]$SnapshotIdPrefix = "pasa-auto-dev-ranking-v1"
)

Set-StrictMode -Version Latest # 将缺失字段或拼写错误变为立即可见的脚本失败。
$ErrorActionPreference = "Stop" # 文件、manifest 或校验失败时停止批次，避免继续消耗来源配额。
$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path # 无论从何处启动都定位到仓库根目录。
Set-Location -LiteralPath $repositoryRoot # 使 manifest 中已有的相对 QueryIntent 路径保持可用。

<#
.SYNOPSIS
    判断指定查询是否已有可复用的成功候选快照。

.DESCRIPTION
    仅检查本地 JSONL 和调用完全离线的 snapshot-check。旧版“全部来源失败但仍写出零候选”
    产物包含“学术来源降级”警告，因此绝不作为已完成快照跳过。
#>
function Test-ValidatedCandidateSnapshot {
    param(
        [Parameter(Mandatory)]
        [string]$QueryId,

        [Parameter(Mandatory)]
        [string]$Directory
    )

    $snapshotFiles = @(Get-ChildItem -LiteralPath $Directory -Filter "*.snapshot.jsonl" -File -ErrorAction SilentlyContinue) # 只枚举候选快照，不读取数据集或配置文件。
    foreach ($snapshotFile in $snapshotFiles) { # 同一查询可能存在历史重试文件，逐个选择唯一可复用成功结果。
        try { # 损坏 JSON 或早期不完整文件应被视为不可用，而不是阻断其他候选文件检查。
            $snapshot = Get-Content -LiteralPath $snapshotFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json # 仅读取单条 JSONL 快照以检查查询关联和安全警告。
            if ($snapshot.query_id -ne $QueryId) { # 仅校验与当前 manifest 查询相同的快照。
                continue # 其他查询的快照不能影响当前查询是否待导出。
            }
            $warnings = @($snapshot.warnings) # 将缺失、空值或数组统一为可遍历集合。
            if ($warnings | Where-Object { [string]$_ -like "学术来源降级*" }) { # 拒绝历史全部来源失败产物，即使其旧契约哈希形式上有效。
                continue # 继续检查同一查询的后续重试快照。
            }
            & python -m evaluation snapshot-check --snapshots $snapshotFile.FullName *> $null # 只读复核契约、去重和 SHA-256，不调用来源、LLM 或模型。
            if ($LASTEXITCODE -eq 0) { # 只有正式加载器认可的封存文件才可被批次跳过。
                return $true # 当前查询已具备可复用的成功快照。
            }
        }
        catch { # 个别历史文件不可解析时继续寻找同查询的其他重试结果。
            continue # 不允许损坏文件被误判为已完成。
        }
    }
    return $false # 没有找到可复用成功快照时交由当前批次导出。
}

$manifestPath = Join-Path $repositoryRoot $QueryIntentManifest # 将用户可覆盖的相对 manifest 参数解析到仓库内绝对路径。
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { # 防止没有已封存输入时误发起在线调用。
    throw "QueryIntent manifest 不存在: $QueryIntentManifest" # 明确要求先准备可审计的本地输入。
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json # 仅读取无需密钥的 QueryIntent manifest。
if ($manifest.schema_version -ne "query-intent-manifest-v1") { # 防止未确认的数据契约被脚本猜测兼容。
    throw "不支持的 QueryIntent manifest 版本: $($manifest.schema_version)" # 让用户显式迁移或选择正确输入。
}
$queryIds = @($manifest.query_id_order) # 保留 manifest 已冻结的 20 条稳定查询顺序。
if ($queryIds.Count -eq 0) { # 空 manifest 不应静默报告批次成功。
    throw "QueryIntent manifest 不包含 query_id_order" # 阻止无输入的误操作。
}
$snapshotDirectoryPath = Join-Path $repositoryRoot $SnapshotDirectory # 解析候选快照目录而不依赖当前终端目录。
New-Item -ItemType Directory -Path $snapshotDirectoryPath -Force | Out-Null # 仅创建评测输出目录，不覆盖任何已有快照。

$pendingTasks = @() # 保存按 manifest 顺序等待本批次处理的查询。
for ($offset = 0; $offset -lt $queryIds.Count; $offset++) { # 逐条保留冻结顺序，禁止随机或并发重排。
    $queryId = [string]$queryIds[$offset] # 读取当前 GoldQuery 稳定标识。
    if (Test-ValidatedCandidateSnapshot -QueryId $queryId -Directory $snapshotDirectoryPath) { # 跳过已通过完整性校验且没有来源降级的历史成功结果。
        Write-Host "[INFO] 已复用有效候选快照：$queryId" # 只显示标识，不输出查询正文或来源响应。
        continue # 不为同一查询重复调用学术 API。
    }
    $queryIntentPath = [string]$manifest.query_intent_files.$queryId # 从已封存映射读取对应的 Windows 安全文件路径。
    if ([string]::IsNullOrWhiteSpace($queryIntentPath)) { # 防止 manifest 顺序和文件映射漂移时误构造请求。
        throw "QueryIntent manifest 缺少文件映射: $queryId" # 在来源调用前失败。
    }
    $pendingTasks += [pscustomobject]@{ QueryId = $queryId; QueryIntentPath = $queryIntentPath; Ordinal = $offset + 1 } # 保存后续导出所需的完整本地参数。
}

if ($pendingTasks.Count -eq 0) { # 所有 manifest 查询均已有有效候选快照时无需调用任何来源。
    Write-Host "[OK] 没有待导出的查询；学术 API=0，LLM=0，本地模型=0" # 明确本次脚本没有消耗外部资源。
    exit 0 # 以成功状态结束纯本地检查。
}

$tasksToRun = @($pendingTasks | Select-Object -First $BatchSize) # 只选择用户显式允许的有限批量，并保持原始稳定顺序。
Write-Host "[INFO] 本批次将导出 $($tasksToRun.Count) 条查询；BatchSize=$BatchSize" # 在调用来源前公布精确调用范围。
foreach ($task in $tasksToRun) { # 顺序调用，避免同批并发超出来源 RPS 或造成难以定位的失败。
    $intentPath = Join-Path $repositoryRoot $task.QueryIntentPath # 将 manifest 文件映射解析为绝对本地路径。
    if (-not (Test-Path -LiteralPath $intentPath -PathType Leaf)) { # 在来源调用前确认结构化输入真实存在。
        throw "QueryIntent 文件不存在: $($task.QueryIntentPath)" # 阻止脚本以缺失输入继续运行。
    }
    $querySuffix = ($task.QueryId -split ":")[-1] # 仅使用原始查询标识末段构造 Windows 安全输出名。
    $ordinalText = "{0:D3}" -f [int]$task.Ordinal # 固定三位编号以保持目录自然排序与 manifest 顺序一致。
    $snapshotId = "$SnapshotIdPrefix-$ordinalText-$querySuffix" # 构造稳定且不含 Windows 禁用冒号的快照标识。
    $outputFileName = "{0}_{1}.snapshot.jsonl" -f $ordinalText, $querySuffix # 使用格式化字符串构造 Windows 安全且不依赖变量边界转义的文件名。
    $outputPath = Join-Path $snapshotDirectoryPath $outputFileName # 独立输出避免把多条在线结果混入同一文件。
    if (Test-Path -LiteralPath $outputPath -PathType Leaf) { # 未通过复用检查但标准输出名已存在时绝不覆盖历史文件。
        throw "快照输出已存在但未通过复用校验，请人工检查后使用新的隔离目录: $outputPath" # 防止失败产物被静默替换或掩盖。
    }
    Write-Host "[INFO] 正在导出 $ordinalText/$($queryIds.Count)：$($task.QueryId)" # 调用前记录不含查询正文的可审计进度。
    & python -m evaluation snapshot-export --query-intent $intentPath --query-id $task.QueryId --snapshot-id $snapshotId --output $outputPath --allow-online-sources # 唯一在线动作，复用既有受控导出入口。
    if ($LASTEXITCODE -ne 0) { # 所有来源失败或其他导出错误时不得继续后续查询。
        throw "候选快照导出失败，批次已停止: $($task.QueryId)" # 让用户先处理失败原因，避免无边界消耗来源配额。
    }
    & python -m evaluation snapshot-check --snapshots $outputPath # 每份新快照立即执行完全离线的契约、去重和哈希复核。
    if ($LASTEXITCODE -ne 0) { # 导出成功但契约校验失败时不能把文件视为可用于排序。
        throw "候选快照离线校验失败，批次已停止: $($task.QueryId)" # 保留文件供诊断但拒绝继续消耗更多来源调用。
    }
}

Write-Host ("[OK] Candidate snapshot batch completed: {0}; run the script again for the next batch" -f $tasksToRun.Count) # 不自动递归执行全部 20 条，保持用户逐批授权边界。

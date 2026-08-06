# 开发环境故障排查与避坑手册

本文记录开发过程中可跨阶段、跨项目复用的故障现象、根因、验证方法和解决方案。目标不是收集所有日志，而是让同类问题再次出现时能够通过错误关键字快速定位。

## 使用规则

新增记录时至少包含：

- **错误关键字**：保留最有辨识度的一行错误；
- **影响范围**：代码、Git、网络、Python、Node 或自动化环境；
- **根因**：只写有证据支持的结论；
- **解决方案**：优先使用命令级、会话级配置，避免污染全局环境；
- **验证方法**：说明如何证明问题已经解决；
- **状态**：`已验证`、`待验证` 或 `仅规避`。

不得在本文写入 API Key、Token、Cookie、密码、完整连接串或其他秘密值。

## 快速排查顺序

遇到安装、拉取或启动失败时，按以下顺序排查：

1. 确认当前目录、分支、Python/Node 环境；
2. 区分权限问题、网络问题、依赖问题和代码问题；
3. 用最小命令验证端口、远端或解释器；
4. 优先使用单次命令参数或当前会话环境变量；
5. 修复后执行一个明确的验证命令；
6. 保留原始错误，不把警告误判为根因。

## 问题索引

| 错误关键字或现象 | 分类 | 根因摘要 | 状态 |
| --- | --- | --- | --- |
| `detected dubious ownership` | Git / 权限 | 执行 Git 的系统用户与仓库所有者不同 | 已验证 |
| `.git/config: Permission denied` | Git / 沙箱 | 受限自动化用户只有 `.git` 读取权限 | 已验证 |
| GitHub 请求长时间无响应 | Git / 网络 | Git 未使用可工作的代理路径或 TLS 后端不兼容 | 已验证 |
| `schannel: failed to receive handshake` | Git / TLS | Windows Git 的 Schannel 与当前代理握手失败 | 已验证 |
| `Failure when receiving data from the peer` | Git / 代理 | Git 与代理的传输路径不稳定 | 已验证 |
| `couldn't find remote ref refs/tags/...` | Git / 远端 | Fork 未复制上游 Release Tag | 已验证 |
| `Set-Location ... 路径不存在`（fetch 后） | Git / 概念 | `fetch` 不创建新目录 | 已验证 |
| `CondaHTTPError: HTTP 000 CONNECTION FAILED` | Conda / 网络 | Conda 不读取 Git 的代理配置 | 已验证 |
| Python 与 pip 路径不一致 | Python / 环境 | 自动化 Shell 未继承用户激活的 Conda 环境 | 已验证 |
| `npm ERR! EPERM ... node_cache` | npm / 权限 | 全局 npm 缓存目录不可写 | 已验证 |
| `deprecated` 警告 | npm / 依赖风险 | 上游锁文件包含旧依赖，不是本次安装失败根因 | 已识别 |
| `Configuration file not found: main.yaml` | pytest / 配置 | 测试导入了要求运行时配置的 API 模块 | 已定位 |
| `No module named 'telegram'` | pytest / 可选依赖 | `.[dev]` 不包含上游全套测试使用的 Partners 依赖 | 已定位 |
| `No module named 'resource'` | pytest / 平台 | Windows 不提供 POSIX `resource` 标准库模块 | 已定位 |
| `PermissionError: ... Temp\\pytest-of-<user>` | pytest / 临时目录 | pytest 默认 basetemp 存在 ACL/所有权问题 | 已验证 |
| `UnicodeDecodeError: 'gbk' codec` | pytest / 编码 | Windows 默认编码与仓库 UTF-8 文件不一致 | 已验证 |
| `/tmp`、`sleep`、`false` 或 `/` 分隔符断言失败 | pytest / 平台 | 测试隐含 POSIX 命令与路径语义 | 已定位 |
| `gh`、指定 WSL 发行版或浏览器自动化不可用 | 工具链 | 运行前未确认可选工具是否存在 | 仅规避 |

## Git：仓库所有权与受限环境

### `detected dubious ownership`

典型错误：

```text
fatal: detected dubious ownership in repository at '<path>'
```

当 Codex、CI、容器或其他沙箱用户操作由本机用户创建的仓库时，Git 会阻止访问。这是 Git 的安全保护，不代表仓库损坏。

优先使用只对当前命令生效的配置：

```powershell
git -c safe.directory=D:/path/to/repo status
```

不应在不了解影响范围时直接执行：

```powershell
git config --global --add safe.directory '*'
```

验证：`git status` 能读取仓库，且没有放宽所有仓库的全局安全限制。

### `.git/config: Permission denied`

受限自动化环境可能允许读取源码，却禁止修改 `.git`。此时添加 remote、fetch、创建分支或写引用可能失败，而用户自己的终端可以正常执行。

排查：

```powershell
git remote -v
git status --short --branch
```

处理原则：需要修改 Git 元数据时使用明确授权的命令，或让仓库所有者在本机终端执行；不要把权限失败误判为 Git 历史损坏。

## Git：代理与 TLS

### 先验证代理端口

以本机混合代理端口 `10808` 为例：

```powershell
Test-NetConnection 127.0.0.1 -Port 10808

curl.exe --proxy http://127.0.0.1:10808 -I https://github.com
curl.exe --proxy socks5h://127.0.0.1:10808 -I https://github.com
```

端口连通只证明代理在监听；收到 GitHub 的 HTTP 响应才证明代理链路可用。

### Git 使用命令级代理

Git 不一定自动继承系统代理。优先对单条命令显式配置：

```powershell
git -c http.proxy=http://127.0.0.1:10808 `
    -c http.version=HTTP/1.1 `
    fetch <remote> <refspec>
```

若 Windows Git 报 Schannel 握手错误，可只对当前命令切换到 OpenSSL：

```powershell
git -c http.sslBackend=openssl `
    -c http.proxy=http://127.0.0.1:10808 `
    -c http.version=HTTP/1.1 `
    fetch <remote> <refspec>
```

ExamMem 已用该组合成功获取 DeepTutor `v1.5.9`。

禁止通过以下方式绕过问题：

```powershell
git config --global http.sslVerify false
```

关闭证书验证会引入中间人攻击风险，不能作为网络故障的解决方案。

## Git：`origin`、`upstream`、Tag 与工作区

### Fork 中找不到上游 Tag

典型错误：

```text
fatal: couldn't find remote ref refs/tags/v1.5.9
```

Fork 可能只复制默认分支，没有复制 Release Tag。推荐约定：

```text
origin   → 个人 Fork，保存项目分支
upstream → 官方仓库，获取 Release、Tag 和上游更新
```

从官方远端精确获取 Tag：

```powershell
git fetch --depth 1 upstream `
    refs/tags/v1.5.9:refs/tags/v1.5.9

git rev-parse "v1.5.9^{}"
```

Tag 名称不是不可变证据；必须额外保存解析后的完整 Commit SHA。

### `fetch` 不会创建目录

`clone`、`fetch` 和 `switch` 的职责不同：

```text
clone  → 创建新目录、新工作区和 .git
fetch  → 将远端对象下载到当前仓库的 .git/objects
switch → 将目标提交的文件写入当前工作区
```

因此，fetch 成功后进入一个预期的新目录会得到 `Set-Location: 路径不存在`。此时应检查：

```powershell
git show --stat <tag>
git ls-tree --name-only <tag>
git switch -c <branch> <tag>
```

切换分支时，已跟踪文件会随目标提交变化；未跟踪且不冲突的文件通常保留。Git 会在覆盖未提交修改或冲突的未跟踪文件前中止操作。

## Conda：代理不会继承 Git 配置

典型错误：

```text
CondaHTTPError: HTTP 000 CONNECTION FAILED
```

Git 的 `http.proxy` 只对 Git 生效。可在当前 PowerShell 会话设置 Conda、pip 等工具通用的代理变量：

```powershell
$env:HTTP_PROXY = "http://127.0.0.1:10808"
$env:HTTPS_PROXY = "http://127.0.0.1:10808"

conda create -n <env-name> python=3.11 pip -y
```

这些变量在关闭当前终端后失效，适合先验证问题。ExamMem 已通过该方式成功创建独立 Conda 环境。

禁止使用：

```powershell
conda config --set ssl_verify false
```

网络不可达和证书校验失败应分别处理，不能用关闭 TLS 校验掩盖。

## Python：确认实际解释器和 pip

终端提示符显示环境名，并不能单独证明 Python 和 pip 指向正确位置。尤其在 IDE、Codex、CI、Conda 和系统 Python 并存时，应同时检查：

```powershell
$env:CONDA_DEFAULT_ENV
where.exe python
python --version
python -c "import sys; print(sys.executable); print(sys.prefix)"
python -m pip --version
```

始终使用：

```powershell
python -m pip <command>
```

这样 pip 与当前 Python 解释器绑定，避免裸 `pip` 指向另一个安装位置。

自动化 Shell 可能不会继承用户终端中激活的 Conda 环境。遇到路径不一致时，应在实际执行安装和测试的终端重复检查，不要根据另一个进程的 PATH 推断结果。

## npm：缓存目录无写权限

典型错误：

```text
npm error code EPERM
npm error syscall mkdir
npm error path D:\Software\node\node_cache\_cacache\...
```

这表示 npm 包已经开始解析，但 npm 无法写入配置的全局缓存目录。它不是依赖冲突，也不是应用源码错误。

先检查缓存位置：

```powershell
npm config get cache
```

优先为当前命令指定一个可写缓存，而不是使用管理员权限：

```powershell
New-Item -ItemType Directory -Force D:\tmp\project-npm-cache | Out-Null

npm ci `
    --legacy-peer-deps `
    --cache=D:\tmp\project-npm-cache
```

如果需要代理，可同时添加命令级参数：

```powershell
--proxy=http://127.0.0.1:10808 `
--https-proxy=http://127.0.0.1:10808
```

ExamMem 已确认原始失败来自不可写缓存，并通过替代缓存完成安装：`node_modules` 存在、Next.js 可执行且版本为 `16.2.3`、Git 跟踪文件保持干净。

### `deprecated` 不是当前失败根因

安装日志可能同时出现：

```text
npm warn deprecated inflight@1.0.6
npm warn deprecated glob@7.2.3
npm warn deprecated uuid@8.3.2
```

这些是上游依赖风险，应记录并在后续依赖治理中处理；当同一日志最终以 `EPERM` 退出时，不能把 deprecated 警告当作安装失败根因。固定第三方底座的审计阶段也不应擅自升级这些包或改写锁文件。

## pytest：收集失败不等于测试断言失败

`pytest --collect-only` 会导入测试模块及其依赖。此阶段报错说明测试尚未开始执行，应归类为配置、依赖或平台问题，不能记为产品断言失败。

### 缺少运行时配置 `main.yaml`

典型错误：

```text
FileNotFoundError: Configuration file not found: main.yaml
```

部分 API 模块在导入时读取 `data/user/settings/main.yaml`。应先检查上游 CI 是否创建最小配置，不要凭空编造完整用户配置。DeepTutor `v1.5.9` 的 CI 使用：

```yaml
system:
  language: en
logging:
  level: WARNING
```

测试最小配置与真实用户的模型/API 配置用途不同。测试配置不得包含秘密值。

### `.[dev]` 不一定包含全套测试依赖

典型错误：

```text
ModuleNotFoundError: No module named 'telegram'
```

不能根据 extra 名称猜测覆盖范围，应读取 `pyproject.toml` 和上游 CI。DeepTutor 的 `.[dev]` 引入 Server 与测试工具，但官方 Python 测试工作流还单独安装 `requirements/partners.txt`。

通用验证方法：

```powershell
rg -n "pip install|pytest" .github/workflows
python -m pip show <missing-package>
```

只安装上游测试实际声明的依赖，不因单个缺包直接改用包含所有重型组件的 `[all]`。

### POSIX 标准库模块在 Windows 缺失

典型错误：

```text
ModuleNotFoundError: No module named 'resource'
```

`resource` 是 POSIX 平台的 Python 标准库模块，不是应从 PyPI 安装的第三方包。正确处理是：

1. 将对应测试标记为平台专属，或在 Windows 基线中明确排除；
2. 在 Linux CI、容器或已确认存在的 WSL 发行版中补跑；
3. 若源码声称跨平台可导入却在平台判断前导入该模块，记录为上游兼容性缺陷。

不要安装来源不明的同名 PyPI 包来伪造标准库能力，也不要为了让收集变绿而修改固定底座源码。

### pytest 默认临时目录无权限

典型错误：

```text
PermissionError: [WinError 5] 拒绝访问: C:\Users\<user>\AppData\Local\Temp\pytest-of-<user>
```

一个不可用的共享 `tmp_path` 根目录会让大量无关测试在 setup 阶段同时报错，形成数百或数千个级联 `ERROR`。先确认第一条 setup error，不要逐个修复测试。

可为本次项目指定独立临时目录：

```powershell
python -m pytest --basetemp=D:\tmp\project-pytest ...
```

注意：pytest 会管理并清理 `--basetemp` 指定目录的内容，因此只能使用为该测试专门创建的路径，不能指向工作区、用户目录或包含其他数据的目录。

### Windows 默认 GBK 导致 UTF-8 文件读取失败

典型错误：

```text
UnicodeDecodeError: 'gbk' codec can't decode byte ...
```

如果测试使用 `Path.read_text()` 却没有显式传入 `encoding`，Windows 中文环境可能按 GBK 读取仓库中的 UTF-8 YAML/Python 文件。可先用解释器级 UTF-8 模式验证：

```powershell
python -X utf8 -m pytest ...
```

或在当前测试会话设置：

```powershell
$env:PYTHONUTF8 = "1"
```

长期代码修复应在读取文本时显式使用 `encoding="utf-8"`，但固定第三方底座的基线审计阶段只记录问题，不擅自改写上游测试。

### 测试隐含 POSIX 命令或路径语义

Windows 上常见表现：

- `Path("/tmp/...")` 变为 `\\tmp\\...`，但测试仍断言 POSIX 字符串；
- 测试调用 `sleep` 或 `false`，Windows 找不到同名可执行文件；
- 断言使用 `/`，实际路径使用 `\\`；
- 测试导入 `resource` 等 POSIX-only 模块。

这类测试应在 Linux CI/容器补跑，或由上游增加平台标记与跨平台断言。Windows 基线可以明确 deselect，但必须保存名单，不能把排除后的结果声称为完整跨平台全绿。

本次 Windows 基线还确认了几种具体差异：Python venv 的可执行目录是 `Scripts` 而非 `bin`；NTFS 将文件名中的冒号解释为 Alternate Data Stream；Windows 不提供 POSIX `600/700` 权限位语义；打开的 SQLite 文件不能像 POSIX 一样被原子重命名。它们都应保留原始失败证据，并在 Linux 基线中补跑。

## 可选工具不可用时的降级原则

自动化开始前先探测工具，而不是假定存在：

```powershell
Get-Command gh -ErrorAction SilentlyContinue
wsl.exe --list --quiet
```

本次遇到过：

- GitHub CLI `gh` 未安装：改为在 GitHub 页面手动创建 Fork；
- 预期的 `Ubuntu-22.04` WSL 发行版不存在：继续使用 Windows PowerShell；
- 浏览器自动化连接不可用：让用户完成最小的已授权页面操作。

降级方案必须保持任务范围不变，不能因为某个辅助工具不可用而跳过版本、权限或安全验证。

## 当前项目的已验证实例

以下值用于复现本次 ExamMem 环境，不应直接复制到其他机器：

| 项目 | 实测值 |
| --- | --- |
| 工作区 | `D:\intern\goup\ExemMem` |
| Git 个人远端 | `https://github.com/LLLQAQFFF/ExamMem.git` |
| Git 官方远端 | `https://github.com/HKUDS/DeepTutor.git` |
| DeepTutor Tag | `v1.5.9` |
| DeepTutor Commit | `37c3db6df7e886aee4f61c97ec5e618b8ab379e8` |
| 开发分支 | `exam-mem/main` |
| Python 环境 | Conda `exammem`，Python 3.11 |
| Node/npm | Node 22.14.0，npm 11.12.1 |
| Docker | Engine 28.4.0，Compose 2.39.4 |
| 本机代理实例 | v2rayN 混合端口 `127.0.0.1:10808` |

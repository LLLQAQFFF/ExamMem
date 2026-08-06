# ExamMem 从 Windows 迁移到 WSL2 Ubuntu 操作手册

> 适用项目：ExamMem / DeepTutor `v1.5.9` 底座  
> 更新日期：2026-08-06  
> 目标：将 Windows 当前审计成果上传到 ExamMem GitHub 仓库，再在 WSL2 Ubuntu 的 Linux 文件系统中重新克隆、安装、测试和启动。

## 1. 迁移完成后的结构

推荐结构：

```text
Windows
├─ VS Code 与浏览器
├─ Docker Desktop
└─ 原 Windows 仓库（迁移确认前保留，不再作为主要运行目录）

WSL2 Ubuntu
└─ /home/<linux-user>/code/ExamMem
   ├─ Git 工作区
   ├─ Conda 环境 exammem
   └─ Node/npm 依赖
```

Windows 浏览器通常可以直接访问 WSL 服务：

```text
http://localhost:<端口>
ws://localhost:<端口>/<WebSocket 路径>
```

源码和文档通过 GitHub 迁移。下列本机内容不会随 `git clone` 自动迁移：

- Conda 环境和已经安装的 Python 包；
- `web/node_modules`；
- 被 `.gitignore` 排除的 `data/`，包括模型设置和密钥；
- 未提交、未推送的文件；
- Windows npm 缓存和 pytest 缓存。

因此，“上传后在 WSL 重新下载并配置环境”是正确方案，但必须先完成第 2 节的 Windows 收尾。

## 2. Windows：检查、提交并推送当前成果

本节在 **Windows PowerShell** 中执行，工作目录为：

```powershell
Set-Location D:\intern\goup\ExemMem
```

### 2.1 确认当前分支和未提交文件

```powershell
git status --short --branch
git log --oneline --decorate -4
git remote -v
```

预期：

- 分支为 `exam-mem/main`；
- `origin` 指向 `LLLQAQFFF/ExamMem.git`；
- `upstream` 指向 `HKUDS/DeepTutor.git`；
- 至少看到 `TROUBLESHOOTING.md`、`artifacts/` 和本手册尚未提交。

### 2.2 确认底座 Tag

```powershell
git rev-parse "v1.5.9^{}"
```

必须输出：

```text
37c3db6df7e886aee4f61c97ec5e618b8ab379e8
```

注意：当前分支 `HEAD` 可以包含后续文档提交，不要求等于这个 SHA。固定底座由 `v1.5.9` 对应的 SHA 表示。

### 2.3 暂存迁移资料

```powershell
git add -- TROUBLESHOOTING.md artifacts/stage01 "doc/WSL迁移与环境搭建操作手册.md"
git diff --cached --name-only
git diff --cached --stat
git diff --cached --check
```

这里的输入是排障文档、阶段证据和迁移手册；输出应该只包含准备提交的这些文件。不要使用 `git add -f data`，也不要上传任何模型密钥。

### 2.4 提交前秘密扫描

优先运行仓库已有的 pre-commit 秘密扫描：

```powershell
python -m pre_commit run detect-secrets --all-files
```

如果本机没有可用的 pre-commit 命令，可补充运行：

```powershell
detect-secrets scan TROUBLESHOOTING.md artifacts/stage01 "doc/WSL迁移与环境搭建操作手册.md"
```

成功标准：没有真实 API Key、Bearer Token、Cookie、密码或私有连接串。测试夹具可能产生误报，应查看内容后判断，不能为了通过而盲目加入白名单。

同时检查 `data/` 确实被忽略：

```powershell
git check-ignore -v .\data\user\settings\main.yaml
```

如果秘密扫描发现真实凭据：停止提交，从暂存区移除相应文件并轮换该凭据。

### 2.5 提交并推送

扫描通过后：

```powershell
git commit -m "chore(baseline): record Windows environment audit"
```

使用已经验证过的本机代理推送分支：

```powershell
git -c http.sslBackend=openssl `
    -c http.proxy=http://127.0.0.1:10808 `
    -c http.version=HTTP/1.1 `
    push origin exam-mem/main
```

再推送固定底座 Tag：

```powershell
git -c http.sslBackend=openssl `
    -c http.proxy=http://127.0.0.1:10808 `
    -c http.version=HTTP/1.1 `
    push origin v1.5.9
```

如果提示 Tag 已存在且指向同一 Commit，可视为正常。

最后确认：

```powershell
git status --short --branch
git rev-parse HEAD
```

成功标准：工作区干净，分支已跟踪 `origin/exam-mem/main`。迁移确认完成以前，不要删除 Windows 原仓库。

## 3. Windows：准备 WSL2 Ubuntu

以下命令在 **管理员 PowerShell** 中执行。

### 3.1 检查现有 WSL

```powershell
wsl --status
wsl --list --verbose
```

如果已经存在 Ubuntu 且 `VERSION` 为 `2`，直接进入第 3.3 节。

### 3.2 尚未安装时安装 Ubuntu

先查看可用发行版：

```powershell
wsl --list --online
```

本项目建议使用 Ubuntu 22.04 LTS：

```powershell
wsl --install -d Ubuntu-22.04
```

按照提示重启 Windows，并在第一次打开 Ubuntu 时创建 Linux 用户名和密码。输入 Linux 密码时终端不会显示字符，这是正常行为。

确认它运行在 WSL2：

```powershell
wsl --set-version Ubuntu-22.04 2
wsl --list --verbose
```

### 3.3 更新 WSL 并进入 Ubuntu

```powershell
wsl --update
wsl -d Ubuntu-22.04
```

后续标注为 `bash` 的命令都在这个 Ubuntu 终端中运行，不要复制回 PowerShell。

微软官方文档：

- <https://learn.microsoft.com/windows/wsl/install>
- <https://learn.microsoft.com/windows/wsl/setup/environment>
- <https://learn.microsoft.com/windows/wsl/networking>

## 4. Ubuntu：安装基础工具

本节开始使用 **Ubuntu Bash**。

### 4.1 确认系统和架构

```bash
uname -a
uname -m
cat /etc/os-release
```

常见 PC 的 `uname -m` 应输出 `x86_64`。如果输出 `aarch64`，不要使用后文的 x86_64 Miniconda 安装包。

### 4.2 安装最小系统依赖

```bash
sudo apt update
sudo apt install -y build-essential git curl ca-certificates pkg-config
```

检查：

```bash
git --version
curl --version
```

本阶段暂不安装 Manim、LaTeX、Matrix E2EE 等可选系统依赖；需要相应能力时再单独审计，避免扩大环境变量。

## 5. Ubuntu：确认网络和 Windows 代理

WSL 的“访问 GitHub”和“被 Windows 浏览器访问”是两个方向：

- Windows 浏览器访问 WSL 服务，通常直接使用 `localhost`；
- WSL 使用 Windows 上的 v2rayN 代理时，NAT 模式下可能不能使用 WSL 自己的 `127.0.0.1`。

### 5.1 先测试不使用代理

```bash
curl -I https://github.com
```

如果成功，跳到第 6 节，不要配置不必要的代理。

### 5.2 测试 mirrored networking 下的 localhost 代理

```bash
curl -I -x http://127.0.0.1:10808 https://github.com
```

如果成功，只对当前终端设置：

```bash
export EXAMMEM_PROXY_URL=http://127.0.0.1:10808
export HTTP_PROXY="$EXAMMEM_PROXY_URL"
export HTTPS_PROXY="$EXAMMEM_PROXY_URL"
```

### 5.3 localhost 代理失败时使用 Windows 主机地址

截图中 v2rayN 的“允许来自局域网的连接”已经开启；保持开启，然后在 WSL 执行：

```bash
EXAMMEM_WINDOWS_IP=$(ip route show default | awk '{print $3; exit}')
printf 'Windows host IP: %s\n' "$EXAMMEM_WINDOWS_IP"
```

测试代理：

```bash
EXAMMEM_PROXY_URL="http://${EXAMMEM_WINDOWS_IP}:10808"
curl -I -x "$EXAMMEM_PROXY_URL" https://github.com
```

成功后，对当前终端导出：

```bash
export HTTP_PROXY="$EXAMMEM_PROXY_URL"
export HTTPS_PROXY="$EXAMMEM_PROXY_URL"
```

先只做会话级配置，不立即写入 `~/.bashrc`。确认 Git、Conda、pip 和 npm 都正常后，再决定是否持久化。

不要在 Linux Git 中设置 Windows 专用的 `http.sslBackend=schannel`；Linux Git 默认使用自己的 TLS 后端。

## 6. Ubuntu：克隆 ExamMem 到 Linux 文件系统

不要把 `/mnt/d/intern/goup/ExemMem` 当作新开发目录。它仍使用 Windows 文件系统语义，可能重新引入权限、大小写、文件锁和性能问题。

创建 Linux 工作目录：

```bash
mkdir -p ~/code
cd ~/code
```

网络可以直连或已经导出代理环境变量后，克隆项目分支：

```bash
git clone --branch exam-mem/main --single-branch https://github.com/LLLQAQFFF/ExamMem.git ExamMem
cd ExamMem
```

如果只想为这一条 Git 命令使用代理：

```bash
git -c http.proxy="$EXAMMEM_PROXY_URL" \
    -c http.version=HTTP/1.1 \
    clone --branch exam-mem/main --single-branch \
    https://github.com/LLLQAQFFF/ExamMem.git ExamMem
cd ExamMem
```

重新增加官方 upstream；远端列表本身不会随 clone 迁移：

```bash
git remote add upstream https://github.com/HKUDS/DeepTutor.git
git fetch upstream refs/tags/v1.5.9:refs/tags/v1.5.9
```

验证：

```bash
git remote -v
git status --short --branch
git rev-parse HEAD
git rev-parse 'v1.5.9^{}'
```

其中 `v1.5.9^{}` 必须解析为：

```text
37c3db6df7e886aee4f61c97ec5e618b8ab379e8
```

若 `git fetch upstream` 需要代理，可增加：

```bash
git -c http.proxy="$EXAMMEM_PROXY_URL" \
    -c http.version=HTTP/1.1 \
    fetch upstream refs/tags/v1.5.9:refs/tags/v1.5.9
```

## 7. Ubuntu：用 VS Code 打开 WSL 仓库

Windows VS Code 需要安装 Microsoft 的 **WSL** 扩展。然后在 Ubuntu 仓库目录运行：

```bash
cd ~/code/ExamMem
code .
```

VS Code 左下角应显示类似 `WSL: Ubuntu-22.04`。集成终端执行：

```bash
pwd
```

预期是 `/home/.../code/ExamMem`，而不是 `/mnt/d/...`。

## 8. Ubuntu：安装 Miniconda 和 Python 环境

Windows 中的 Conda 环境不能直接给 WSL 使用，因为二者的 Python 可执行文件和二进制包属于不同操作系统。

### 8.1 已安装 Conda 时

```bash
conda --version
```

能正常输出就跳到第 8.3 节。

### 8.2 未安装时安装 Miniconda

以下安装包适用于前面确认的 `x86_64`：

```bash
cd /tmp
curl -fsSLo Miniconda3-latest-Linux-x86_64.sh \
    https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
```

需要严格供应链校验时，应将 `sha256sum` 结果与 Conda 官方发布的安装包哈希对照后再执行。然后安装：

```bash
bash Miniconda3-latest-Linux-x86_64.sh -b -p "$HOME/miniconda3"
"$HOME/miniconda3/bin/conda" init bash
exec bash
```

验证：

```bash
conda --version
conda info
```

Conda 官方 Linux 安装说明：

<https://docs.conda.io/projects/conda/en/latest/user-guide/install/linux.html>

### 8.3 创建独立环境

```bash
conda create -n exammem python=3.11 pip -y
conda activate exammem
```

确认当前解释器确实来自 Linux 环境：

```bash
python --version
which python
python -c "import sys; print(sys.executable); print(sys.prefix)"
python -m pip --version
```

路径应位于 Linux 的 Conda 目录中，不能出现 `D:\`、`C:\` 或 `/mnt/d`。

### 8.4 安装项目和测试依赖

```bash
cd ~/code/ExamMem
python -m pip install --upgrade pip
python -m pip install -e ".[dev,partners]"
```

这里：

- `-e` 让 Python 直接加载当前源码；
- `dev` 提供 pytest、ruff 等开发工具；
- `partners` 补齐上游完整测试会收集的 Partner SDK。

验证：

```bash
python -m pip check
python -c "import deeptutor, sys; print(deeptutor.__file__); print(sys.executable)"
python -m pytest --version
ruff --version
```

`deeptutor.__file__` 应位于 `~/code/ExamMem/deeptutor/`。

## 9. Ubuntu：安装 Node 22 和前端依赖

项目要求 Node 20+，上游 CI 使用 Node 22。本手册使用 NVM 管理 Linux Node，避免与 Windows Node 混用。

### 9.1 安装 NVM

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.6/install.sh | bash
```

关闭并重新打开 Ubuntu 终端，或执行：

```bash
source ~/.bashrc
```

验证并安装 Node 22：

```bash
command -v nvm
nvm install 22
nvm use 22
node --version
npm --version
```

NVM 官方说明：

<https://github.com/nvm-sh/nvm>

### 9.2 安装前端依赖

```bash
cd ~/code/ExamMem/web
npm ci --legacy-peer-deps
```

如果 npm 必须显式使用代理：

```bash
npm ci --legacy-peer-deps \
    --proxy="$EXAMMEM_PROXY_URL" \
    --https-proxy="$EXAMMEM_PROXY_URL"
```

Linux 默认 npm 缓存位于 Linux 用户目录，一般不需要 Windows 使用过的 `D:\tmp` 缓存规避参数。

验证：

```bash
test -d node_modules
./node_modules/.bin/next --version
npm run test:node
```

## 10. Ubuntu：创建最小测试配置

`data/` 被 Git 忽略，所以 Windows 的 `main.yaml` 不会通过 clone 出现。手动创建目录：

```bash
cd ~/code/ExamMem
mkdir -p data/user/settings
nano data/user/settings/main.yaml
```

写入：

```yaml
system:
  language: en
logging:
  level: WARNING
```

保存后检查：

```bash
sed -n '1,20p' data/user/settings/main.yaml
git status --short
```

`git status` 不应显示这个配置文件，因为 `data/` 本来就不应进入 Git。

此时只创建测试所需最小配置。MiniMax API Key 后续通过 DeepTutor 的 Settings → Models 或正式运行时设置机制填写，不写入源码、迁移手册、Shell 历史、测试日志或 Git。

## 11. Ubuntu：记录环境快照

```bash
cd ~/code/ExamMem
mkdir -p artifacts/stage01/logs
```

执行：

```bash
{
  date -Iseconds
  uname -a
  cat /etc/os-release
  python --version
  which python
  python -m pip --version
  node --version
  npm --version
  git --version
  git rev-parse HEAD
  git rev-parse 'v1.5.9^{}'
  docker --version
  docker compose version
} 2>&1 | tee artifacts/stage01/logs/wsl-environment.txt
```

如果 Docker 命令失败，先看第 14 节。失败输出也应保留，它属于环境审计证据。

## 12. Ubuntu：运行 Linux 完整测试基线

这次不使用 Windows 的 24 项排除清单，也不忽略 `resource` 测试。目标是复现上游 Ubuntu 测试入口。

```bash
cd ~/code/ExamMem
```

先收集：

```bash
python -m pytest --collect-only -q tests deeptutor/learning/tests \
    2>&1 | tee artifacts/stage01/logs/pytest-collect-wsl.txt
```

再完整执行并保留 pytest 的真实退出码：

```bash
printf 'started_at=%s\n' "$(date -Iseconds)" \
    | tee artifacts/stage01/logs/pytest-wsl.txt

python -m pytest -q tests deeptutor/learning/tests --durations=20 \
    2>&1 | tee -a artifacts/stage01/logs/pytest-wsl.txt

EXAMMEM_TEST_EXIT=${PIPESTATUS[0]}

printf 'finished_at=%s\nexit_code=%s\n' \
    "$(date -Iseconds)" "$EXAMMEM_TEST_EXIT" \
    | tee -a artifacts/stage01/logs/pytest-wsl.txt
```

输入：`tests` 和 `deeptutor/learning/tests` 的完整测试集合。  
处理：在 Linux 文件系统和 Linux Python 中执行，不做 Windows 平台排除。  
输出：测试通过、失败、跳过、耗时、慢测试和退出码的原始日志。

成功标准是 `exit_code=0`。如果仍有失败，不立即改源码；先保存最后的汇总和首个完整 traceback，再按“产品缺陷、环境问题、配置缺失、不稳定测试”分类。

补充执行上游 CI 的代码和前端检查：

```bash
ruff check .
ruff format --check .
cd web
npm run test:node
cd ..
```

## 13. Ubuntu 服务与 Windows 浏览器连通性

### 13.1 先验证 WSL 端口转发

Ubuntu 中执行：

```bash
mkdir -p /tmp/exammem-port-test
cd /tmp/exammem-port-test
python3 -m http.server 8001 --bind 0.0.0.0
```

Windows 浏览器访问：

```text
http://localhost:8001
```

看到目录页面即通过。按 `Ctrl+C` 停止测试服务器。

如果 `localhost` 不通，在 PowerShell 查询 WSL IP：

```powershell
wsl -d Ubuntu-22.04 hostname -I
```

然后临时访问 `http://<WSL-IP>:8001`。WSL IP 可能在重启后变化，不应写死到项目配置中。

### 13.2 启动 DeepTutor

回到项目并激活环境：

```bash
cd ~/code/ExamMem
conda activate exammem
deeptutor start --dev
```

输入是当前项目源码和 `data/user/settings/` 下的运行时配置；启动器会拉起后端和前端。使用终端实际打印的 URL，仓库 README 记录的默认前端地址是：

```text
http://127.0.0.1:3782
```

在 Windows 浏览器中可以使用：

```text
http://localhost:3782
```

完成浏览器验证后按 `Ctrl+C` 停止服务。后续阶段再配置 MiniMax 并保存一次带输入、脱敏输出、Trace、Token、延迟和退出码的真实调用。

## 14. Ubuntu：连接 Docker Desktop

Docker Desktop 仍安装在 Windows，不需要在 WSL 中再安装一套 Docker Engine。

在 Docker Desktop 中打开：

```text
Settings → Resources → WSL Integration
```

为 `Ubuntu-22.04` 开启集成，然后在 Ubuntu 验证：

```bash
docker version
docker compose version
docker run --rm hello-world
```

如果只有 `docker --version` 成功而 `docker version` 的 Server 部分失败，通常说明 Docker Desktop 未启动或 WSL Integration 未开启。

## 15. 迁移验收清单

- [ ] Windows 当前变更经过秘密扫描；
- [ ] `exam-mem/main` 已推送到 `origin`；
- [ ] `v1.5.9` 已推送，且解析到固定 SHA；
- [ ] Ubuntu 运行在 WSL2；
- [ ] 仓库位于 `/home/.../code/ExamMem`，不是 `/mnt/d`；
- [ ] WSL 仓库有 `origin` 和 `upstream`；
- [ ] Conda `exammem` 使用 Linux Python 3.11；
- [ ] `deeptutor` 从 WSL 当前源码导入；
- [ ] Node 22 和前端依赖安装完成；
- [ ] 最小 `main.yaml` 已在 WSL 本机重建且未进入 Git；
- [ ] Linux 完整 pytest 日志和真实退出码已保存；
- [ ] Windows 浏览器能访问 WSL 测试端口；
- [ ] Docker Desktop 的 WSL Integration 可用；
- [ ] Windows 原仓库在上述检查完成前仍保留。

## 16. 常见问题判断

### `git clone` 超时或 TLS 失败

先执行第 5 节的两个 `curl` 测试，确认问题属于直连、localhost 代理还是 NAT 主机地址。Linux 中不要照搬 Windows 的 `schannel` 设置。

### Conda 再次报 HTTP 000

确认当前 Ubuntu 终端中：

```bash
printf '%s\n' "$HTTP_PROXY"
printf '%s\n' "$HTTPS_PROXY"
curl -I -x "$EXAMMEM_PROXY_URL" https://repo.anaconda.com
```

不要通过关闭 SSL 校验掩盖网络或证书问题。

### Windows 浏览器打不开 WSL 服务

确认服务没有立即退出，并尽量监听 `0.0.0.0`。先用第 13.1 节的 Python HTTP Server 区分“WSL 网络问题”和“DeepTutor 启动问题”。

### VS Code 又打开了 Windows 仓库

在 VS Code 终端执行 `pwd`。正确路径是 `/home/.../code/ExamMem`，左下角应显示 `WSL: Ubuntu-22.04`。

### clone 后找不到 `main.yaml` 或 MiniMax 配置

这是预期行为。`data/` 被 Git 忽略，本机配置和密钥必须在新环境重新创建，不能为了方便而强制提交。

### clone 后没有 `upstream`

这是预期行为。Git clone 只自动创建 `origin`，按第 6 节重新添加官方 upstream。

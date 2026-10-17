# GenTactix → Cloudflare 部署手册

## 一、结论先行

**只有一条路：Cloudflare Containers。** 免费计划做不到。

| 方案 | 能否跑 Streamlit | 原因 |
|---|---|---|
| Workers（纯 JS/WASM） | ❌ | 128 MB 内存上限、无文件系统、免费版 CPU 仅 10 ms。Streamlit 是长驻 Python 进程 |
| Python Workers（Pyodide） | ❌ | 无法长期驻留监听端口，无法跑 Tornado/WebSocket 服务 |
| Workers + 静态资源（Pages） | ❌ | 只能托管前端，没有 Python 运行时 |
| **Workers + Containers** | ✅ | 完整 Linux 容器，任意镜像，且官方明确支持 WebSocket 转发 |

Streamlit 的交互全靠 `_stcore/stream` 这条 WebSocket 长连接。Containers 的 `Container.fetch()`
**会自动转发 WebSocket 升级请求**，这是整个方案成立的前提（已对官方文档核实）。

**前置要求：Cloudflare Workers Paid 订阅，$5/月。** 没有它，Containers 不可用。

### 我该走哪条路

| 你的情况 | 走哪条 |
|---|---|
| 本机能装 Docker（有管理员权限） | **三·A** —— `./deploy.sh` 一条命令搞定 |
| 装不了 / 不想装 Docker | **三·B** —— GitHub Actions 构建，本机零 Docker |
| 只是想有个公网地址随时能打开 | **九** —— Streamlit Community Cloud，免费、也不需要 Docker |

---

## 二、前置条件

| 项目 | 要求 | 本机现状（2026-09-27 复核） |
|---|---|---|
| Cloudflare 账号 | 已订阅 Workers Paid（$5/月） | ⚠️ 需你确认 |
| Cloudflare 登录 | 执行过一次 `wrangler login` | ⚠️ 尚未登录 |
| Docker Desktop | **必须运行中**。`wrangler deploy` 会调用本地 Docker 构建镜像 | ❌ **未安装**（`winget` 可装，见 三·A·1） |
| Node.js | ≥ 18 | ✅ v24.14.0 |
| 网络 | 能访问 `api.cloudflare.com`、`registry.npmjs.org`、Docker Hub | ✅ **已通**（走代理 `127.0.0.1:60660`） |
| 磁盘 | Docker 镜像 + 构建缓存约需 5 GB | ✅ C 盘剩 16 GB |
| DeepSeek API Key | 运行时在侧栏输入，**不进镜像、不进代码** | — |

> **现状一句话**：网络这关已经过了，**只剩 Docker 一个坎**。
> 两条路 —— 本机装 Docker 走 **三·A**，不想装 Docker 走 **三·B（CI 部署）**。
>
> ⚠️ 上面网络那一项是用 `curl` 带代理测的。**你自己的 CMD / PowerShell 窗口未必带这个代理**，
> 动手前先跑一次 `curl -s -o /dev/null -w "%{http_code}" https://api.cloudflare.com/client/v4/`
> 确认返回不是 `000`。不通就先配好代理再继续。

---

## 三、部署步骤

有两条路。**本机没装 Docker 就走 B**，其余情况 A 更直接。

### 三·A 本机部署（需 Docker）

已封装成一键脚本，自带部署前自检，不会盲跑：

```bash
cd C:\Gentactix

# 1) 先自检 —— 安全，不上传任何东西
./deploy.sh --check

# 2) 确认无误后真正部署
./deploy.sh
```

脚本依次做：环境自检 → 配置结构校验 → 装依赖 → `wrangler login` → `wrangler deploy`
→ 提示等容器就绪。每步状态都会打印，失败即停在那步并说明原因与出路。

自检覆盖 11 项，当前实测输出：

```
① 环境自检    Node.js v24.14.0 ✅   Docker 未安装 ⚠️
② 文件完整性  Dockerfile / requirements.txt / wrangler.jsonc / src/index.ts / package.json / app.py / gentactix/ ✅
③ 配置校验    wrangler.jsonc 结构合法（containers / DO 绑定 / migrations 齐备且自洽）✅
              Dockerfile 指定 linux/amd64 ✅   监听 0.0.0.0 ✅
④ 依赖        wrangler 已安装（4.141.0）✅
⑤ 登录        尚未登录 ⚠️
通过 11 项，失败 0 项
```

<details>
<summary>不想用脚本，手工三条命令</summary>

```bash
cd C:\Gentactix
npm install                  # 国内加速：npm install --registry=https://registry.npmmirror.com
npx wrangler login           # 受限网络改用：export CLOUDFLARE_API_TOKEN=<token>
npx wrangler deploy
```
</details>

部署完成后访问：`https://gentactix.<你的 workers.dev 子域>.workers.dev`

#### 三·A·1 装 Docker Desktop（若尚未安装）

```powershell
winget install -e --id Docker.DockerDesktop
```

装完**启动 Docker Desktop**，等托盘图标变绿，用 `docker info` 确认守护进程在跑
（只装了不开，`wrangler deploy` 一样报连不上 Docker）。

- 需要管理员权限
- 依赖 WSL2；若系统提示需启用虚拟化功能，按提示重启
- 首次启动会初始化 WSL 发行版，约几分钟
- `winget search` 实测可查到 `Docker.DockerDesktop 4.91.0`

#### 三·A·2 国内网络构建加速

**pip 源已默认配好** —— `wrangler.jsonc` 里预置了：

```jsonc
"image_vars": { "PIP_INDEX_URL": "https://mirrors.aliyun.com/pypi/simple/" }
```

若在海外或 Cloudflare Workers Builds 上构建，删掉这一项即可走 PyPI 官方源。

**另一道坎：拉基础镜像。** `Dockerfile` 的 `FROM python:3.13-slim` 要从 Docker Hub 拉，
国内直连基本不通。需给 Docker Desktop 配镜像加速器
（Settings → Docker Engine → 加 `"registry-mirrors": ["<可用加速器地址>"]`），
或把 `FROM` 改成可访问的 registry 地址。

### 三·B CI 部署（不需要本地 Docker）⭐ 本机没 Docker 就走这条

**原理**：GitHub Actions 的 runner **自带 Docker**，`wrangler deploy` 在 runner 上
构建并推送镜像 —— 你的电脑完全不参与构建。代价只是要把代码放到 GitHub。

工作流已写好：`.github/workflows/deploy.yml`（含 `tsc --noEmit` 类型检查与容器状态快照）。

```
1) 建 Cloudflare API Token
   Dashboard → My Profile → API Tokens → Create Token
   → 用 "Edit Cloudflare Workers" 模板
   → 记下 token，以及 Accounts 页里的 Account ID

2) 把项目推到 GitHub 私有仓库
   cd C:\Gentactix
   git init && git add . && git commit -m "deploy setup"
   git branch -M main
   git remote add origin git@github.com:<你>/gentactix.git
   git push -u origin main
   （.gitignore 已配好，216 MB 的 node_modules 不会被推上去）

3) 仓库 Settings → Secrets and variables → Actions，加两个 secret
   CLOUDFLARE_API_TOKEN   = 第 1 步的 token
   CLOUDFLARE_ACCOUNT_ID  = 你的 Account ID

4) 仓库 Actions 页 → 选 "Deploy GenTactix → Cloudflare Containers"
   → Run workflow
```

之后每次往 `main` 推改动（且动到 `app.py` / `gentactix/` / `Dockerfile` / `wrangler.jsonc`
/ `src/**` / `.streamlit/**` 等）都会自动重新部署。也可以连 Cloudflare 自家的
Workers Builds（官方托管 CI），构建环境在境外，不会有拉基础镜像超时的问题。

> 注意：CI 构建时 `image_vars` 里的阿里云 pip 源是**面向全球可达**的，
> 保留不影响；若你想统一走官方源，删掉 `wrangler.jsonc` 里那一项即可。

---

### 三·C 部署包文件清单

`C:\Gentactix` 下与部署相关的文件：

| 文件 | 作用 | 需要你改吗 |
|---|---|---|
| `deploy.sh` | **一键部署脚本**（`--check` 只自检） | 不用 |
| `.github/workflows/deploy.yml` | CI 部署工作流（三·B） | 不用 |
| `Dockerfile` | 运行镜像定义（python:3.13-slim + Streamlit） | 一般不用 |
| `requirements.txt` | Python 依赖，版本区间已实测锁定 | 一般不用 |
| `wrangler.jsonc` | Cloudflare 部署配置（容器规格 / DO 绑定） | 改规格时 |
| `src/index.ts` | Worker 入口，把请求转发给容器 | 改 `sleepAfter` 时 |
| `package.json` / `package-lock.json` | Worker 侧 npm 依赖，已钉死版本 | 不用 |
| `tsconfig.json` | TypeScript 配置 | 不用 |
| `.dockerignore` | 构建上下文排除项 | 不用 |
| `.gitignore` | 版本控制排除项（防 `node_modules` 与密钥入库） | 不用 |
| `DEPLOY-cloudflare.md` | 本文档 | — |
| `app.py` / `gentactix/` / `logo.png` / `.streamlit/config.toml` | 应用本体（入口 `app.py` + `gentactix/` 包），进镜像 | — |

> 首次部署前建议把 `package.json` 里各依赖的版本号再核一遍是否仍在 npm 上存在。

---

## 四、部署后必等：容器预置期

**首次部署完，Worker 地址可能立刻能打开，但容器还没 provision 好，请求会报错。**
官方明确说明这个阶段要「等几分钟」。建议：

```bash
# 反复执行直到状态正常
npx wrangler containers list
```

另外：容器**首次冷启动**要拉起 VM + 启动 Python 进程，第一次访问较慢（十几秒到几十秒），
属正常现象，之后会快。

---

## 五、关键参数与取舍

| 配置 | 当前值 | 位置 | 说明 |
|---|---|---|---|
| `defaultPort` | `8501` | `src/index.ts` | 必须与 Dockerfile 的 `--server.port` 一致 |
| `sleepAfter` | `"1h"` | `src/index.ts` | 空闲 1 小时后休眠。调短省钱但重访要冷启动；调长反之 |
| `instance_type` | `basic`（1 GiB / ¼ vCPU / 4 GB） | `wrangler.jsonc` | 若出现 OOM 升到 `standard-1`（4 GiB / 8 GB） |
| `max_instances` | `1` | `wrangler.jsonc` | **全局单实例**。见下方说明 |
| 固定实例名 | `"gentactix-main"` | `src/index.ts` | 所有请求汇聚到同一 Streamlit 进程 |

### 为什么必须是单实例

Streamlit 是「一个进程服务多个浏览器 session」的模型。我们必须让所有请求都落到**同一个**
Streamlit 进程上，session 才能被正确识别与保持。因此用固定实例名 + `max_instances: 1`。

不要改成按用户 ID 分片——那会让每个用户各自启动一个完整 Streamlit 进程，
内存成本成倍上涨，且首访都要冷启动。

### 容器休眠会断 WebSocket

容器一辈子性休眠，所有 WebSocket 连接会断。页面开着时 Streamlit 会持续心跳，
不会触发休眠；**关掉页面后**才开始计时。所以 `sleepAfter` 主要影响「关页后多久释放」。

---

## 六、成本估算

在 $5/月 Workers Paid 基础上，Containers 按「实例运行时长 × 预置资源」计费：

| 资源 | 每月含额 | 超出单价 |
|---|---|---|
| 内存 | 25 GiB-hours | $0.0000025 / GiB-second |
| CPU | 375 vCPU-minutes | $0.000020 / vCPU-second |
| 磁盘 | 200 GB-hours | $0.00000007 / GB-second |

`basic` 档（1 GiB 内存 / 4 GB 磁盘）粗算：

| 使用强度 | 容器运行时长/月 | 内存用量 | 约计费用 |
|---|---|---|---|
| 每天用 1 小时 | 30 h | 30 GiB-h（含额 25） | ≈ $5.1 |
| 每天用 3 小时 | 90 h | 90 GiB-h | ≈ $5.6 |
| 几乎常驻（sleepAfter 很长） | 720 h | 720 GiB-h | ≈ $11.3 |

> **注意**：内存与磁盘按**预置**资源计费，CPU 按**实际**用量计费。
> 所以把 `instance_type` 从 `basic` 调到 `standard-1`，内存成本直接涨 4 倍。
> 没有 OOM 就别升档。

出口流量：北美/欧洲 $0.025/GB（含 1 TB），其他区域 $0.04/GB（含 500 GB）。个人使用基本可忽略。

---

## 七、本部署包做过哪些实测

以下结论都是**实际跑出来的**，不是查文档推断：

| 验证项 | 方法 | 结果 |
|---|---|---|
| Worker 代码类型正确 | `npx tsc --noEmit`（TypeScript 7.0.2） | ✅ 退出码 0，零错误 |
| 依赖模块真实存在 | 读 `@cloudflare/containers@0.3.7` 的 `index.d.ts` | ✅ 确认导出 `Container` / `getContainer` |
| 6 个 pip 包名都存在 | 逐个探测 PyPI 镜像索引 | ✅ 全部命中 |
| **依赖树可完整求解** | `pip install --dry-run -r requirements.txt`（pip 26.1.2） | ✅ 全树解析成功，无冲突 |
| **依赖真实安装成功** | 建独立 venv 实际 `pip install -r requirements.txt` | ✅ 6 个直接依赖 + 全部传递依赖安装完成 |
| **应用真跑得起来** | Streamlit 官方 `AppTest` 框架跑全链路 | ✅ **41/41 通过**（详见下方） |
| **容器启动命令可用** | 按 Dockerfile 的 CMD 参数实际起服务并探测端点 | ✅ 见下方「启动冒烟」 |
| SRTM 数据源可用 | 实下珍珠港瓦片 `N21W158` | ✅ HTTP 200，202,589 B，解压 2.88 MB `.hgt` |
| wrangler 配置结构合法 | 去注释后 `JSON.parse` + 字段核对 | ✅ 五个关键字段全部就位 |

### 真实运行期验证（AppTest 41/41）

在独立 venv 里真实安装依赖后，用 Streamlit 官方测试框架 `streamlit.testing.v1.AppTest`
跑脚本本体（真 import、真组件渲染、真异常捕获），mock 掉 LLM 返回预设 JSON：

| 阶段 | 验证内容 | 结果 |
|---|---|---|
| 0 | 6 个依赖版本落在锁定区间；`import srtm` 确实提供 `get_data()` | ✅ |
| 2 | 冷启动零异常；7 个 `session_state` 键全部初始化；标题渲染；4 个按钮齐全 | ✅ |
| 3 | 空输入走防御分支（警告而非崩）；填 Key+场景后初始化成功；**智库锚点吸附生效**（亚利桑那号 `[25.0,-170.0]` → `[21.365,-157.95]`）；`history` 写入 T+0 | ✅ |
| 4 | 下一轮推演推进 `turn_count`，保存上一回合快照，写入交战数据 | ✅ |
| 5 | 参谋长复盘落库（正文 + 思维链） | ✅ |
| 6 | 脏数据渲染：字符串坐标 / `None` / 非数字 `radius` 全不崩 | ✅ |

> 附注：运行时确实打印了 `use_container_width will be removed after 2025-12-31`。
> 这条告警走的是 `show_in_browser=False`，**只写日志、不在页面显示**，不影响观感。
> 但也印证了「锁 `streamlit<1.50`」是必要的——升上去这 4 处会真的报错。

### 启动冒烟（容器 CMD 实测）

按 `Dockerfile` 里那条 `CMD` 的原样参数起服务，探测结果：

| 探测项 | 结果 |
|---|---|
| 启动日志 | 干净，无报错，正常输出 `You can now view your Streamlit app` |
| `GET /_stcore/health` | **200**，body = `ok` |
| `GET /` | 200，1522 字节 HTML |
| `GET /_stcore/stream` | 400（无 `Upgrade` 头时被正确拒绝 —— 说明 WebSocket 端点存在且在鉴权） |
| `GET /static/js/*` | 200 |

启动耗时很短（秒级），说明**冷启动慢主要来自 Cloudflare 侧拉起 VM**，而非应用自身初始化。

### pip 实测锁定的版本组合

以下版本既经 `--dry-run` 解析确认可解，也经**真实安装**确认落地（直接依赖加粗）：

**streamlit 1.49.1** · folium 0.20.0 · **streamlit-folium 0.27.4** · **openai 3.17.0**
· **global-land-mask 1.0.0** · **SRTM.py 0.3.7** · requests 2.34.2 · numpy 2.5.3
· pandas 2.3.3 · pyarrow 25.0.1 · altair 5.5.0 · pydeck 0.9.3 · tornado 6.5.10
· pydantic 2.13.5 · protobuf 6.33.6 · pillow 11.3.0

三个值得注意的点：

- **folium 落在 0.20.0 而非最新的 1.0.0** —— 依赖约束把大版本拦住了。这对代码是好事：
  0.20.0 与 `folium.plugins.AntPath` 等用法完全兼容。
- **streamlit 落在 1.49.1** —— 正是 `<1.50` 上限生效的位置。
- **`SRTM.py` 没有预构建 wheel**，安装时是从源码现场 build 的（`Building wheel for SRTM.py`）。
  实测成功，无需编译器；但意味着构建环境要有 setuptools（`python:3.13-slim` 自带，无碍）。

### 镜像 Python 版本 = 实测环境

`Dockerfile` 钉 **`python:3.13-slim`**，与上面跑出 41/41 的那个 Python 3.13.12 环境一致。
这样「验证过的环境」和「部署的环境」不会存在 ABI 层面的偏差。

### 部署脚本的桩验证（15/15）

`deploy.sh` 的登录 / 部署段用桩 `docker` + 桩 `wrangler` 跑穿，覆盖五种分支：

| 用例 | 场景 | 期望 | 结果 |
|---|---|---|---|
| T1 | 全流程正常 | 退出码 0，打印「部署完成」 | ✅ |
| T2 | wrangler 部署失败 | 退出码 4，回显失败原因，不误报成功 | ✅ |
| T3 | 未登录且登录失败 | 退出码 3，且不继续部署 | ✅ |
| T4 | 登录成功但输出被过滤成空 | 退出码 0（**不得误判为失败**） | ✅ |
| T5 | 已登录 | 跳过登录直接部署 | ✅ |
| T6 | 旧写法回归对照 | 复现「登录成功被误判为失败」 | ✅ |
| T7 | `pipefail` 语义对照 | 记录差异，判定不依赖 pipefail | ✅ |

顺带纠正了一个容易搞反的判断：用 `if ! cmd | grep -v "PATTERN"` 判定成败时，
管道退出码取决于 `grep`。实测四种组合：

| 情景 | `pipefail=on` | `pipefail=off` |
|---|---|---|
| 命令失败（输出含错误行） | 判定失败 ✅ | 判定成功 ❌ 漏报 |
| 命令成功（输出被滤空） | 判定失败 ❌ 误报 | 判定失败 ❌ 误报 |

本脚本有 `set -uo pipefail`，所以「漏报」不会发生，真实风险是**反方向的误报**。
现已统一改为「输出落盘 + 取命令自身退出码」，不依赖任何隐含前提。

### 顺手排掉的两个「必炸」的坑

1. **`srtm` 这个包名已经不能用了。** PyPI 上现存的 `srtm` 是另一个 Rust 重写版
   （`SrtmService` API，且要求 `Python>=3.12`，有不自动下载瓦片的差异）。
   而 `geo` 模块用的 `srtm.get_data()` 来自 PyPI 包 **`SRTM.py`**。
   写错包名 → 镜像构建直接失败。
2. **`global_land_mask` 没有 1.0.1 版本。** PyPI 上只有 `0.0.1 / 0.0.2 / 0.0.3 / 1.0.0`。

---

## 八、常见故障对照

| 现象 | 原因 | 处理 |
|---|---|---|
| 页面 403 或无限重连 | WebSocket 握手被 Streamlit 的 XSRF/CORS 校验拒绝 | Dockerfile 里已关掉 `--server.enableCORS=false --server.enableXsrfProtection=false`，如仍出现，检查参数是否生效 |
| 502 / 连接被拒 | 容器还在 provision，或 Streamlit 没监听 `0.0.0.0` | 等几分钟；确认 Dockerfile 有 `--server.address=0.0.0.0` |
| 容器反复重启 | 内存不足（OOM） | 把 `instance_type` 升到 `standard-1` |
| `wrangler deploy` 报连不上 Docker | Docker Desktop 没启动 | 启动 Docker Desktop，`docker info` 能跑通再试 |
| 首次查询落水坐标很慢 | 该区域的 SRTM 瓦片要现下（约 200 KB） | 正常。冷启动后缓存清空会重下 |
| 地图整片空白 / 灰白 | **瓦片服务在你这网络下不可达** | 见下方附注 —— 这是最可能踩到的一个 |
| 构建时 pip 超时 | PyPI 官方源国内不可达 | 见第三节「国内网络构建加速」 |
| `./deploy.sh` 退出码 2 | 自检判定本机无可用 Docker | 走三·B（CI 部署），或按三·A·1 装 Docker Desktop |
| `./deploy.sh` 退出码 3 | 浏览器登录没走完 | 改用 API Token：`export CLOUDFLARE_API_TOKEN=...` 后重跑 |
| `./deploy.sh` 退出码 1 | 自检有 ❌ 项 | 看上面哪一项标红，按提示修 |
| `curl api.cloudflare.com` 返回 `000` | 你自己终端没带代理 | 本机代理是 `127.0.0.1:60660`，`export HTTPS_PROXY=...` 后重试 |
| CI 里 `npm ci` 报 lock 不同步 | 改过 `package.json` 版本但没更新 lock | 本地跑一次 `npm install` 后把 `package-lock.json` 一起提交 |
| CI 类型检查失败 | `src/index.ts` 类型错 | 本地 `npx tsc --noEmit` 能复现同样报错，先修再推 |
| CI 报 `A request to the Cloudflare API (/accounts/***/containers/me) failed.`（无详情） | wrangler 把这个 API 的响应体吞掉了，只留一行错误 | 别猜，直接裸 curl 看真实 body：<br>`curl -s -H "Authorization: Bearer $CF_TOKEN" "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/containers/me"`<br>也可在 Actions 里手动 **Run workflow**，会自动跑诊断步骤打印 body |
| 上面 curl 返回 `Unauthorized: You do not have access to Cloudflare Containers. Deploying containers requires the Workers Paid plan.` | **账号是免费版**，Containers 属于 Workers Paid 权益 | 去 https://dash.cloudflare.com/?to=/:account/workers/plans 订阅 **Workers Paid（$5/月）**。注意：这与 token 权限无关，token 配得再对也没用 |
| 上面 curl 返回 401 且 token 权限里确实没有 Containers | token 缺 **Account → Workers Containers: Edit**（控制台在 *Developer Platform* 分组下，名字叫 "Workers Containers"，不叫 "Cloudflare Containers"） | 编辑 token 补该权限。多数情况下改权限不改 token 值，secret 无需更新 |
| CI 报 `Wrangler requires at least Node.js v22.0.0` | workflow 里 `setup-node` 版本太低 | 保持 `node-version: '22'` |

### 🗺️ 附注：地图底图已改为「多源可切换」（已修复，重要）

**问题**：原代码默认底图是 OpenTopoMap。实测 `tile.openstreetmap.org` 与
`a.tile.opentopomap.org` 在部分网络（尤其国内）**完全不可达**，地图会整片灰白 ——
而地图是本应用的核心 UI，第一眼就会让人以为「部署坏了」。

**已修复**：`viz` 模块的底图改为图层组，默认切到 **Esri 卫星实景**，
其余底图全部挂到右上角图层控制台，可随时切换：

| 图层 | 坐标系 | 用途 |
|---|---|---|
| 🛰️ 天眼卫星实景（**默认**） | WGS-84 | Esri World Imagery，战术推演观感也更好 |
| 🌊 海洋底图 | WGS-84 | Esri Ocean Base，海战场景更清爽 |
| 🏔️ 战术地形图 | WGS-84 | Esri World Topo |
| 🗺️ OpenTopoMap | WGS-84 | 保留给海外用户 |
| ~~🇨🇳 高德路网~~ | ~~GCJ-02~~ | **已隐藏**（`enabled=False`），见下 |

> ⚠️ **高德的坑**：它是 GCJ-02 坐标系，而本应用所有单位坐标是 WGS-84，
> 直接叠加会有**数百米偏移**。原先只在图层名里标注偏移、仍放在控件里可选，
> 后来判断这属于「把一个已知会错的东西摆在用户手边」，直接隐藏了：
> 几百米偏移在长平这种几公里纵深的战场上足以把赵军垒摆到丹河对岸。
> 配置仍留在 `TILE_LAYERS` 里（`enabled=False`），将来做了 GCJ-02→WGS-84 纠偏可一键放回。

**关于可达性实测：结论是「测不准」，别拿单次结果下判断。**

同一台机器、同一个 Esri 地址，先后测出过：

- **5/5 成功**，每次恒定 2425 字节
- **0/10 成功**，全部超时

高德曾返回过有效 PNG（magic `89504e47`），OSM 与 OpenTopoMap 则始终 `000`。
这说明本机网络对这类请求是**间歇性放行**的，
**无法据此断定某个源在你的浏览器里一定可用**。

所以：**部署完请用真实浏览器打开确认**。若默认底图不显示，在右上角图层控制台
依次试其他几个即可 —— 这正是把它做成可切换的原因，而不是押注单一数据源。

（`assets.zyrosite.com` 的侧栏 Logo 同样不可达，属纯装饰，不影响功能。）

---

## 九、更省事的替代方案

Cloudflare Containers 是能跑，但对「个人工具型 Streamlit 应用」来说偏重：
要 $5/月、要本地 Docker、有冷启动、还要管内存计费。若你的目标只是「有个公网地址能打开」，可对比：

| 平台 | 成本 | 优势 | 劣势 |
|---|---|---|---|
| **Streamlit Community Cloud** | 免费 | 连 GitHub 仓库即可，零配置，原生支持 Streamlit | 需公开/私有 GitHub 仓库；资源有限 |
| **Hugging Face Spaces** | 免费 | 支持 Docker SDK，公网地址现成 | 冷启动；国内访问一般 |
| **Fly.io / Railway** | 约 $0~5/月 | 部署简单，原生长驻进程，无需改架构 | 非 Cloudflare 生态 |
| **Cloudflare Containers** | $5/月 + 用量 | 与 Cloudflare 生态一体，全球边缘，WebSocket 原生支持 | 需 Docker；冷启动；内存计费敏感 |

**我的建议**：如果只是自己想随时打开用，**Streamlit Community Cloud 是性价比最高的选择**，
不用掏钱也不用装 Docker。除非你有明确的理由要放在 Cloudflare（已有域名/生态、
要挂 Cloudflare 的 WAF/Access 鉴权、或要和其他 Workers 项目打通），否则不必上 Containers。

---

## 十、部署后建议顺手做的事

1. **给 Worker 加访问控制。** 现在任何人拿到地址都能打开。这个应用虽然没有服务端密钥
   （API Key 由访问者自己输入），但推演结果和你的 Key 使用额度是共享的。
   推荐用 Cloudflare Access（Zero Trust）加一层邮箱/OTP 登录，免费版够用。
2. **确认落水算法精度。** `is_water_precise` 的微观波段依赖 SRTM。
   若 `npx wrangler tail` 里看到 SRTM 相关异常，说明降级成了单波段海陆判断，
   近岸（港口、海峡）的判定精度会下降。
3. **导出数据是存在浏览器 session 里的**，容器重启后 `session_state` 清空。
   重要推演结果记得用侧栏的 JSON 导出按钮落盘。

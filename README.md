# GenTactix-DSH

**简体中文** | [English](README.en.md)

历史战役推演与决策复盘工作台。基于 Vue 3、Electron、FastAPI 和 DeepSeek Harness（DSH），将战役资料、地图态势、逐回合推演与决策轨迹放在同一界面中，面向历史教学、想定讨论和软件研究。

![启动与战役选择](docs/screenshots/launch.png)

## 主要功能

- **战役选择与想定创建**：启动后显示中国及周边地图；内置长平之战与九场抗美援朝战役，可选择资料库场景，也可通过问答生成想定。
- **地图与态势**：展示双方单位、移动轨迹及状态；有对应数据时可显示地形要素、射界、远程火力范围、补给节点与路线、人员和装备损失。缺失数据保留为未知。
- **逐回合推演**：设置回合时长，由程序维护时钟；保留单位名单与状态，并检查异常坐标、数值及单位遗漏。可开启几何裁决，约束位移、补给连通与包围判定。
- **DSH 智能体与插件**：采用 Native 工具模式，支持持久角色会话、只读领域工具、运行事件观测、预算控制和在途取消；保留应用层 JSON 工具兼容模式。
- **决策复盘**：查看战报、关键决策与转折点，结合轨迹进行归因分析；支持几何反事实与成对模型分支，分支结果不直接覆盖主战局。
- **资料与存档**：本地史料检索、战役裁决参数、会话检查点、恢复以及 JSON 导入导出。
- **模型配置**：菜单中编辑 vLLM / OpenAI 兼容接口或 DeepSeek 接口、模型、密钥、超时和角色预算；配置加密保存。

![推演工作台，使用固定响应进行界面验收](docs/screenshots/workspace.png)

## 安装与启动

从 [Releases](https://github.com/snow-wind-001/Gentactix-DSH/releases) 下载 Linux x86_64 安装包。包内包含后端和 DSH runtime，无需另装 Python 或 Conda；运行推演仍需可用的模型服务。

| 格式 | 使用方式 |
| --- | --- |
| Ubuntu `.deb` | 使用系统安装器安装，或执行 `sudo apt install ./GenTactix-DSH-<版本>-amd64.deb`；安装后在应用菜单搜索 **GenTactix** |
| `.AppImage` | 赋予执行权限后双击运行；无 FUSE 时可加 `APPIMAGE_EXTRACT_AND_RUN=1` 启动 |
| `.tar.gz` | 解压后运行其中的 `gentactix-dsh` |

在源码工作区中，也可为 AppImage 安装当前用户的菜单项和桌面图标：

```bash
python3 tools/install_linux.py release/GenTactix-DSH-3.1.2-x86_64.AppImage --project-root .
```

详细步骤、构建方法及配置迁移见[桌面使用说明](docs/桌面启动与配置.md)。当前提供 Linux 包，尚未验证 Windows 或 macOS 安装包。

## 开始一次推演

1. 打开菜单 **配置 → DSH · LLM配置**（`Ctrl+,`），填写接口与模型，保存并测试连接。vLLM 的模型 ID 应与服务端的 `served-model-name` 一致。
2. 在启动页选择战役，或通过问答创建想定；确认回合时长、史料检索与裁决选项后初始化。
3. 在工作台推进回合、观察地图与战报，也可提问、复盘或比较反事实分支。
4. 导出战局 JSON，或从启动页恢复已保存的会话。

配置通过 Fernet 认证加密保存为项目目录中的 `.gentactix-llm.enc`，解密密钥单独存于用户配置目录。安装目录不可写时，桌面程序使用用户数据目录下的可写项目目录。公开仓库与安装包不含个人 API Key；同一用户同时取得密文与密钥后仍可解密。

## 源码运行

使用 Python 3.10+、Node.js 22 和图形桌面。在仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dsh.txt
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8700
```

另开终端启动 Electron 开发界面：

```bash
cd frontend
npm ci
npm run dev
```

原有 Streamlit 界面保留于 `app.py`，可通过 `.venv/bin/streamlit run app.py` 启动；其功能与桌面版不完全相同。

## 项目结构

| 目录 | 内容 |
| --- | --- |
| `frontend/` | Vue 界面、地图和 Electron 桌面入口 |
| `backend/` | FastAPI 接口、会话管理与分析服务 |
| `gentactix/` | 推演、状态守恒、时钟、地理、史料检索和裁决逻辑 |
| `plugins/dsh-gentactix/` | DSH 原生领域工具、运行观测与控制插件 |
| `corpus/`、`adjudication/` | 史料卡片与各战役裁决参数 |
| `verify_*.py`、`tools/` | 回归检查、真实模型测试和打包安装工具 |

## 验证与使用边界

```bash
.venv/bin/python verify_all.py
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

完整测试范围见 [TESTING.md](TESTING.md)。[3.1.2 验收报告](docs/发布验收-3.1.2.md)记录了上甘岭局部四单位想定的 20 回合真实模型测试、恢复过程、中途修复和未覆盖项目；这不代表所有战役与模型行为均已验证。

程序提供教学推演与解释辅助。模型生成的伤亡、士气和归因不是经过验证的战斗效能或因果结论；HP 也不等于人员数量。几何裁决默认关闭，不能替代弹道、地形遮蔽或后勤吞吐模型。地图射界为示意图层，离线底图为粗略轮廓，在线瓦片需要网络。DSH 使用方式及反事实边界见[架构说明](docs/因果分析与DSH插件架构.md)。

## 开发维护与致谢

**开发维护：沈阳理工大学装备工程学院。** 研发团队：深度学习实验室。

本项目基于 [pystrategylab/Gentactix](https://github.com/pystrategylab/Gentactix) 扩展，感谢原作者开源。保留上游版权声明，采用 [MIT 许可证](LICENSE)；完整署名见 [NOTICE.md](NOTICE.md)。

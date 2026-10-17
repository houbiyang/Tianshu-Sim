#!/usr/bin/env bash
# ============================================================
# GenTactix → Cloudflare Containers 一键部署脚本
#
# 用法：
#   ./deploy.sh --check     只做部署前自检，不上传（安全，随时可跑）
#   ./deploy.sh             自检 + 登录 + 部署（需要本机 Docker 在运行）
#
# 前提：
#   1) Cloudflare 账号已订阅 Workers Paid（$5/月），否则 Containers 不可用
#   2) 本机装了 Docker Desktop 且**正在运行**（wrangler 要调它构建镜像）
#   3) 能访问 api.cloudflare.com
#
# 如果本机装不了 Docker，改用 GitHub Actions 那条路：
#   见 DEPLOY-cloudflare.md「三·B CI 部署（不需要本地 Docker）」
# ============================================================

set -uo pipefail

cd "$(dirname "$0")" || exit 1

# 归一化参数：--check / -check / check 都视作自检模式
MODE="${1:-deploy}"
MODE="${MODE#--}"
MODE="${MODE#-}"
PASS=0
FAIL=0

c_ok()   { printf "  \033[32m✅\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
c_bad()  { printf "  \033[31m❌\033[0m %s\n" "$1"; FAIL=$((FAIL+1)); }
c_warn() { printf "  \033[33m⚠️ \033[0m %s\n" "$1"; }
c_info() { printf "  \033[36mℹ️ \033[0m %s\n" "$1"; }
sec()    { printf "\n\033[1m%s\033[0m\n" "$1"; }

# ---------- 找 wrangler：优先用项目内已安装的，避免 npx 现场下载 ----------
WRANGLER=""
if [ -x "./node_modules/.bin/wrangler" ]; then
  WRANGLER="./node_modules/.bin/wrangler"
fi

# ⚠️ 退出码的坑（实测结论，别凭直觉）
#   不要写 `if ! "$WRANGLER" deploy 2>&1 | grep -v "xxx"; then`
#   —— 管道的退出码取决于最后一个命令，也就是 grep，而 grep 的规则是：
#      有输出→0，无输出→1。这带来两种错判（实测数据）：
#
#       情景                               pipefail=on   pipefail=off
#       命令失败(输出含错误行)             判定失败 ✅    判定成功 ❌ 漏报
#       命令成功(输出被 grep 全部滤空)      判定失败 ❌    判定失败 ❌ 误报
#
#   本脚本开头有 `set -uo pipefail`，所以「失败漏报」不会发生；
#   但**「成功却被误判为失败」两种情况下都会发生** —— 一旦 wrangler 的输出
#   恰好只有那行代理告警（会被 grep 滤掉），就会误报失败并退出。
#   而且这套判断还隐含依赖 pipefail 这个前提，换 shell / 去掉这行就崩。
#
#   所以统一改成：输出落盘，直接用命令**自身**的退出码判断。不依赖任何隐含前提。
# 🐛 修复：不再用 `mktemp -t` —— Git Bash 下它可能返回 "C:/Users/.../Temp/xxx"
#    或 "/c/Users/..." 两种 Windows 形态。删除拦截层（安全删除包装器）会把它们
#    误判成**相对路径**并与 cwd 拼接，得到 "c:\Gentactix\C:\Users\..." 这种非法路径
#    ⇒ 删除 FAIL_CLOSED、临时文件残留，还会在自检全部通过之后补打一行
#    SAFE_DELETE_FAIL_CLOSED，看起来像部署失败（实际是清理步骤）。
#    /tmp 是 MSYS 里 Windows Temp 的固定别名，直接用固定形态，不让路径形式飘。
LOG="/tmp/gentactix-deploy.$$.log"
if ! : > "$LOG" 2>/dev/null; then
  LOG="./.gentactix-deploy.$$.log"          # /tmp 不可写时退回工作目录
  : > "$LOG" 2>/dev/null || LOG="/dev/null"
fi
trap 'rm -f "$LOG"' EXIT

# 取输出文本（用于展示与判断内容），不关心退出码
wout() { "$WRANGLER" "$@" 2>&1 | grep -vE "Proxy environment variables detected"; }
# 执行并把输出落盘，保留 wrangler **自身**的退出码
wrun() { "$WRANGLER" "$@" >"$LOG" 2>&1; }
# 展示过滤后的日志
wshow() { grep -vE "Proxy environment variables detected" "$LOG" 2>/dev/null \
          | grep -v '^[[:space:]]*$' | tail -"${1:-20}" | sed 's/^/     /'; }

sec "① 环境自检"

# Node
if command -v node >/dev/null 2>&1; then
  NODE_V=$(node --version)
  NODE_MAJOR=$(echo "$NODE_V" | sed 's/^v//' | cut -d. -f1)
  if [ "${NODE_MAJOR:-0}" -ge 18 ]; then
    c_ok "Node.js $NODE_V"
  else
    c_bad "Node.js $NODE_V 版本过低，需要 ≥ 18"
  fi
else
  c_bad "未找到 node，请先安装 Node.js ≥ 18"
fi

# Docker —— 只在真正要部署时才判定为硬失败
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    c_ok "Docker 守护进程在运行（$(docker --version 2>/dev/null | head -1)）"
    DOCKER_OK=1
  else
    c_warn "装了 docker 但守护进程没起来 —— 请启动 Docker Desktop"
    DOCKER_OK=0
  fi
else
  c_warn "未安装 Docker Desktop —— 本机无法构建镜像"
  c_info "改用 CI 部署可跳过这一步：见 DEPLOY-cloudflare.md「三·B」"
  DOCKER_OK=0
fi

# 配置文件齐全
sec "② 部署文件完整性"
MISSING=0
for f in Dockerfile requirements.txt wrangler.jsonc src/index.ts package.json app.py gentactix; do
  if [ -e "$f" ]; then
    c_ok "$f"
  else
    c_bad "$f 缺失"
    MISSING=1
  fi
done

# wrangler.jsonc 结构校验
# ⚠️ wrangler.jsonc 是 JSONC（Cloudflare 官方支持）：允许 // 行注释、/* */ 块注释、尾随逗号。
#    标准 JSON.parse 不支持这些，直接 parse 会误报「期望属性名」（实为尾随逗号）。
#    所以先走 JSONC→JSON 清洗（去注释 + 去尾随逗号）再校验。
sec "③ Wrangler 配置校验"
if node -e '
const fs=require("fs");
let raw=fs.readFileSync("wrangler.jsonc","utf8");
function stripJsonc(t){let out="",i=0,n=t.length;while(i<n){const c=t[i];
 if(c==="\""||c==="\x27"){const q=c;out+=c;i++;while(i<n){const d=t[i];out+=d;i++;if(d==="\\"){out+=t[i];i++;}else if(d===q){break;}}continue;}
 if(c==="/"&&t[i+1]==="/"){i+=2;while(i<n&&t[i]!=="\n")i++;out+="\n";continue;}
 if(c==="/"&&t[i+1]==="*"){i+=2;while(i<n&&!(t[i]==="*"&&t[i+1]==="/"))i++;i+=2;out+=" ";continue;}
 out+=c;i++;}
 return out;}
try{
 let s=stripJsonc(raw).replace(/,(\s*[}\]])/g,"$1");
 const o=JSON.parse(s);
 const c=o.containers?.[0]||{};
 const need=["class_name","image","instance_type","max_instances"];
 const missing=need.filter(k=>!(k in c));
 if(missing.length){console.error("缺少字段: "+missing.join(","));process.exit(1);}
 if(!o.durable_objects?.bindings?.[0]){console.error("缺少 durable_objects 绑定");process.exit(1);}
 if(!o.migrations?.[0]){console.error("缺少 migrations");process.exit(1);}
 if(c.class_name!==o.durable_objects.bindings[0].class_name){console.error("class_name 与 DO 绑定不一致");process.exit(1);}
}catch(e){console.error("解析失败: "+e.message);process.exit(1);}
' 2>/tmp/wr_err; then
  c_ok "wrangler.jsonc 结构合法（containers / DO 绑定 / migrations 齐备且自洽，JSONC 兼容）"
else
  c_bad "wrangler.jsonc 有问题：$(cat /tmp/wr_err 2>/dev/null | tail -1)"
fi

# Dockerfile 关键项
if grep -q "linux/amd64" Dockerfile 2>/dev/null; then
  c_ok "Dockerfile 指定了 linux/amd64（Cloudflare Containers 强制要求）"
else
  c_bad "Dockerfile 缺少 --platform=linux/amd64"
fi
if grep -q "0.0.0.0" Dockerfile 2>/dev/null; then
  c_ok "Streamlit 监听 0.0.0.0（容器外可访问）"
else
  c_bad "Dockerfile 未让 Streamlit 监听 0.0.0.0"
fi

# 依赖
sec "④ Worker 侧依赖"
if [ -n "$WRANGLER" ]; then
  c_ok "wrangler 已安装（$(wout --version | tail -1)）"
else
  c_warn "未安装 Worker 依赖"
  if [ "$MODE" = "check" ]; then
    c_info "运行 ./deploy.sh 会自动执行 npm install"
  fi
fi

# 登录状态
sec "⑤ Cloudflare 登录状态"
AUTHED=0
if [ -n "$WRANGLER" ]; then
  WHO=$(wout whoami)
  if echo "$WHO" | grep -qi "You are not authenticated"; then
    c_warn "尚未登录 Cloudflare"
    c_info "运行 ./deploy.sh 会执行 wrangler login（会打开浏览器）"
  else
    c_ok "已登录"
    echo "$WHO" | grep -iE "associated|email|account" | head -4 | sed 's/^/     /'
    AUTHED=1
  fi
fi

# ---------- 汇总 ----------
sec "自检结果"
printf "  通过 %d 项，失败 %d 项\n" "$PASS" "$FAIL"

if [ "$MODE" = "check" ]; then
  echo ""
  c_info "这是 --check 模式，未做任何上传。去掉 --check 即真正部署。"
  exit 0
fi

if [ "$FAIL" -gt 0 ]; then
  echo ""
  c_bad "存在失败项，已中止部署。"
  exit 1
fi

# ---------- 安装依赖 ----------
if [ -z "$WRANGLER" ]; then
  sec "⑥ 安装 Worker 侧依赖"
  if npm install --no-audit --no-fund 2>&1 | tail -3; then
    WRANGLER="./node_modules/.bin/wrangler"
    c_ok "依赖安装完成"
  else
    c_warn "直连 npm 失败，改用国内镜像重试"
    npm install --registry=https://registry.npmmirror.com --no-audit --no-fund 2>&1 | tail -3
    WRANGLER="./node_modules/.bin/wrangler"
  fi
fi

# ---------- Docker 硬校验（此刻才卡） ----------
if [ "${DOCKER_OK:-0}" -ne 1 ]; then
  sec "⛔ 无法继续"
  echo "  本机没有可用的 Docker，wrangler 无法构建容器镜像。"
  echo ""
  echo "  三个选择："
  echo "    A) 装 Docker Desktop，然后重跑本脚本："
  echo "         winget install -e --id Docker.DockerDesktop"
  echo "    B) 用 CI 部署（GitHub Actions runner 自带 Docker，本机无需安装）："
  echo "         见 DEPLOY-cloudflare.md「三·B CI 部署（不需要本地 Docker）」"
  echo "    C) 换平台（Streamlit Community Cloud 免费且不需要 Docker）："
  echo "         见 DEPLOY-cloudflare.md 第九节"
  exit 2
fi

# ---------- 登录 ----------
if [ "$AUTHED" -ne 1 ]; then
  sec "⑦ 登录 Cloudflare"
  c_info "即将打开浏览器完成授权…"
  # 注意：判断的是 wrun（即 wrangler 自身）的退出码，不是 grep 的。
  # 旧写法在这里有个**反向**误报：登录成功、但输出恰好被 grep 滤空时，
  # 会被判成「登录失败」并退出 3。已修。
  if ! wrun login; then
    c_bad "登录失败，输出如下："
    wshow 20
    echo ""
    c_info "受限网络可改用 API Token："
    echo "         export CLOUDFLARE_API_TOKEN=<token>   # 需 Workers Scripts: Edit 权限"
    echo "         export CLOUDFLARE_ACCOUNT_ID=<account id>"
    exit 3
  fi
  c_ok "登录完成"
fi

# ---------- 部署 ----------
sec "⑧ 构建镜像并部署（首次通常 5~15 分钟）"
c_info "过程中会看到 Docker 层层构建，属正常现象"
echo ""
if ! wrun deploy; then
  c_bad "部署失败，输出如下："
  wshow 40
  echo ""
  c_info "常见原因见 DEPLOY-cloudflare.md 第八节「常见故障对照」"
  exit 4
fi
# 成功时也回显末尾几行，确认拿到部署地址
wshow 12

sec "⑨ 部署完成"
echo "  下一步（重要）：容器还需要几十秒到几分钟 provision，"
echo "  期间访问地址可能报错，属正常。反复执行下面这条直到状态正常："
echo ""
echo "      npx wrangler containers list"
echo ""
echo "  看实时日志："
echo "      npx wrangler tail"
echo ""
c_info "部署地址形如：https://gentactix.<你的 workers.dev 子域>.workers.dev"

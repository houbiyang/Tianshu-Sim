# ============================================================
# GenTactix · Cloudflare Containers 运行镜像
# 构建上下文 = 项目根目录（须含 app.py / gentactix/ / logo.png / .streamlit）
# 目标平台：linux/amd64 —— Cloudflare Containers 强制要求
#
# 为什么钉 3.13：这套 requirements 与整条应用链路，已在 Python 3.13.12 上
# 实测跑通（真实安装 + Streamlit AppTest 41/41 + 启动冒烟）。
# 用同版本镜像，可让「验证过的环境」与「部署的环境」完全一致。
# ============================================================
FROM --platform=linux/amd64 python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/root

# ca-certificates 必装：openai / srtm 走 HTTPS，缺了会 SSL 校验失败
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# pip 源可配置：国内网络直连 PyPI 官方源会超时。
# 通过 wrangler.jsonc 的 image_vars 传入 PIP_INDEX_URL 即可切换（相当于 docker build --build-arg）。
ARG PIP_INDEX_URL=https://pypi.org/simple

# 先单独装依赖 —— 利用 Docker 层缓存：之后只改 Python 代码不必重装依赖
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r /app/requirements.txt

# 再拷应用代码（入口 app.py + gentactix 包 + 静态资源 + 史料语料）
COPY app.py /app/app.py
COPY gentactix /app/gentactix
COPY corpus /app/corpus
COPY adjudication /app/adjudication
COPY logo.png /app/logo.png
COPY .streamlit /app/.streamlit

# SRTM 高程瓦片缓存目录 —— SRTM.py 的默认路径是 ~/.cache/srtm（不是 ~/.srtm）
# 容器磁盘是临时的，冷启动后瓦片缓存会清空，首次查询对应区域时重新下载（约 200KB/瓦片）
RUN mkdir -p /root/.cache/srtm

EXPOSE 8501

# 两个关键点：
# 1. 必须监听 0.0.0.0，否则容器外的 Worker 连不进来
# 2. 关闭 CORS / XSRF 校验：Cloudflare Worker 代理会改写请求头，
#    Streamlit 默认的 Origin↔Host 一致性校验会拒绝 WebSocket 握手
#    （表现为页面 403 或无限重连）。此应用不在服务端存任何密钥，
#    鉴权由用户在侧栏自行输入 API Key，风险可接受。
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false", \
     "--server.fileWatcherType=none", \
     "--browser.gatherUsageStats=false"]

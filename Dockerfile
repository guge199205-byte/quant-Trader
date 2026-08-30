# BayMax-Trader 运行镜像（python 3.14，与本机 .venv 一致）
# 用法见 README.docker.md
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖（requirements.lock.txt 由 .venv pip freeze 生成，层缓存友好）
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt

# 再拷代码（data/logs/configs 等已在 .dockerignore 排除，运行时 bind-mount）
COPY . .

CMD ["python", "main.py"]

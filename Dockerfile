FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOCALGAZ_DB=/data/localgaz.db

WORKDIR /srv/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
# 单进程运行：确认接口的进程内写锁 + SQLite BEGIN IMMEDIATE 保证并发下
# 冲突提交被退回且保留当前结果。多副本部署需共享同一 SQLite/换用集中式锁。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

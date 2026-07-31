"""启动入口：python run.py

默认开启代码热重载（reload=1），改 app/ 下文件即时生效；
若需关掉（如生产 / 内存敏感场景），设环境变量 RELOAD=0 即可。
"""
import os

# DATA_DIR 默认 "./data"（相对路径），从别的 cwd 启动会落到错误目录。
# 这里用绝对路径锁死，并同时切 cwd，覆盖 reload 子进程 / 直接启动两种情况。
_HERE = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.join(_HERE, "app")
os.environ.setdefault("DATA_DIR", os.path.join(_HERE, "data"))
os.chdir(_HERE)

import uvicorn  # noqa: E402

from app.config import settings  # noqa: E402


if __name__ == "__main__":


    uvicorn.run(
        "app.main:app",
        # 默认仅本机访问，避免把开发服务器暴露到 LAN/公网。
        # 如需外网访问，显式 HOST=0.0.0.0 启动并自行确认安全风险。
        host=os.getenv("HOST", "127.0.0.1"),
        port=settings.port,
        reload=bool(int(os.getenv("RELOAD", "1"))),
        # reload 模式下只监听 app 目录，避免 data/ 等运行期写入引发反复重启
        reload_dirs=[_APP_DIR],
        log_level="info",
    )



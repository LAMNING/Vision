"""ImageLabel 后端包。

import 时载入仓库根的 ``.env``（app/env.py）：``IMAGELABEL_SECRET_KEY`` 必须在
``app.utils.security`` 计算密钥*之前*就位，否则每次重启都会换成临时密钥、让所有
已签发的 token 失效；``IMAGELABEL_DATABASE_URL`` 也要让 CLI 和服务看到同一个库。

放在包里而不是各个入口里，是为了 ``run.py`` / ``python -m app.cli.*`` / pytest
拿到的环境完全一致。真实环境变量优先级更高，不会被 .env 覆盖。
"""

from app.env import load_env_file as _load_env_file

_load_env_file()

from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

T = TypeVar('T')


class ORMModel(BaseModel):
    '''响应基类：可直接从 SQLAlchemy 实例构造。'''

    model_config = ConfigDict(from_attributes=True)


class InputModel(BaseModel):
    '''请求基类：多传的字段直接 422，避免路由层拿不到数据却看不出原因。'''

    model_config = ConfigDict(extra='forbid')


class Page(BaseModel, Generic[T]):
    '''统一分页响应，例：``Page[ProjectRead]`` 可直接用作 response_model。'''

    items: list[T]
    total: int
    offset: int = 0
    limit: int = 20


# ---- 复用约束 ----

Username = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=30,
        pattern=r'^[\w.\-]+$',
    ),
]

Password = Annotated[str, StringConstraints(min_length=8, max_length=128)]

# 展示名：可选。空串由路由层转成 None（表示清空），所以不给 min_length。
DisplayName = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=50)
]

# 刻意不用 pydantic.EmailStr：它会引入 email-validator 依赖，
# 而库里 email 只是 String(50)，且账号/邀请的匹配走的是原始字符串比较。
Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=50,
        pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$',
    ),
]

Phone = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=30,
        pattern=r'^\+?[0-9\-() ]+$',
    ),
]

GroupName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)
]

ProjectName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]

FileName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]

# 标签：先 strip 再判长度，纯空白会被拒
Label = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)
]

# 类目：中文展示名与英文检测提示词（LLMDet 的文本提示），长度对齐列宽
CategoryZhName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)
]

CategoryEnName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]

# 可空英文提示词：留空表示「保存时由中文名自动翻译」
CategoryEnNameInput = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=100)
]

# 推理阈值：0~1；Optional 的外层语义由各字段自己决定
Threshold = Annotated[float, Field(ge=0.0, le=1.0)]

# sha256 十六进制；入库前统一小写。
# 注意 pydantic 的 pattern 在 to_lower 之前执行，所以匹配要写成大小写不敏感，
# 否则客户端传大写 hex 会被误拒。
ContentHash = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_lower=True, pattern=r'(?i)^[0-9a-f]{64}$'
    ),
]

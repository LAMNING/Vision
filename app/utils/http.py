'''HTTP 细节的共用实现（目前只有条件请求）。'''


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    '''判断 If-None-Match 是否命中当前 ETag。

    支持 ``*``、``"a", "b"`` 列表和 ``W/"a"`` 弱校验写法；比较时忽略引号。
    '''
    if not if_none_match:
        return False
    raw = if_none_match.strip()
    if raw == '*':
        return True
    for token in raw.split(','):
        candidate = token.strip()
        if candidate.startswith('W/'):
            candidate = candidate[2:]
        if candidate.strip('"') == etag.strip('"'):
            return True
    return False

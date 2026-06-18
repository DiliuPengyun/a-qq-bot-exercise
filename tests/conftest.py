"""pytest 公共夹具：在导入 agent 模块前 stub 掉重依赖。

tools.py → from memstore import mem_delete，而 memstore 在模块顶层
执行 memory = Memory(...)，会连接本地 Qdrant + 读 .env API Key。
测试环境没有这些，导入即炸。这里往 sys.modules 注入一个轻量假模块，
让 tools 只拿到一个空 mem_delete，不触发 Mem0 构造。

必须在被测模块导入之前执行，所以放在 conftest.py 的模块顶层
（pytest 收集时最先加载 conftest）。
"""

import sys
import types
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 仅为类型检查器提供形状；运行期不执行
    pass


def _install_stub(name: str, attrs: dict[str, object]) -> types.ModuleType:
    """若 sys.modules 没有 name，注入一个假模块；已有则不动。"""
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
        return mod
    return sys.modules[name]


# memstore stub：handle_tool_calls 的非法 JSON 路径不会走到 mem_delete，
# should_quote 路径也不需要它。给一个可追踪的空函数即可。
def _fake_mem_delete(_memory_id: str) -> None:
    return None


_install_stub("memstore", {
    "mem_delete": _fake_mem_delete,
})

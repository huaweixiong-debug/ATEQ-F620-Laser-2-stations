#!/bin/bash
# 本地镜像测试：把代码拷到本地盘跑 pytest（网络盘上 PySide6 太慢）。
# 用法: bash tools/run_tests_local.sh [pytest 参数...]
set -e
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="C:/Users/Administrator/laserleak-run"
PY="C:/Users/Administrator/venvs/laserleak/Scripts/python.exe"

export MSYS2_ARG_CONV_EXCL="*"
export MSYS_NO_PATHCONV=1

mkdir -p "$DEST/app" "$DEST/config" "$DEST/tests"
robocopy "$(cygpath -w "$SRC/app")" "$(cygpath -w "$DEST/app")" /MIR /NFL /NDL /NJH /NJS /NP > /dev/null || [ $? -le 7 ]
robocopy "$(cygpath -w "$SRC/config")" "$(cygpath -w "$DEST/config")" /MIR /NFL /NDL /NJH /NJS /NP > /dev/null || [ $? -le 7 ]
robocopy "$(cygpath -w "$SRC/tests")" "$(cygpath -w "$DEST/tests")" /MIR /NFL /NDL /NJH /NJS /NP > /dev/null || [ $? -le 7 ]

cd "$DEST"
exec "$PY" -m pytest "$@"

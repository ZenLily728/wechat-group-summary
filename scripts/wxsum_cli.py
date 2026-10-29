"""wxsum 命令行启动器（免设 PYTHONPATH）。

用法（在目标工作目录下运行，outputs/ 将生成在该目录）：
  python wxsum_cli.py groups
  python wxsum_cli.py export --group "完整群名" [--range 24h|48h|72h | --start "YYYY-MM-DD HH:MM" --end "YYYY-MM-DD HH:MM"] [--account 账号目录名] [--out outputs]
  python wxsum_cli.py render --dir outputs/<导出目录>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wxsum.__main__ import main

if __name__ == "__main__":
    main()

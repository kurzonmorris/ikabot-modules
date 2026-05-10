import sys
import ikafixed

# All internal modules import from 'ikabot.*' (inherited naming from the
# original package). Register ikafixed under that name so every
# 'from ikabot.xxx import' resolves to ikafixed/xxx at runtime —
# this works because Python uses ikafixed.__path__ to locate submodules.
sys.modules.setdefault('ikabot', ikafixed)

from ikafixed.command_line import main

main()

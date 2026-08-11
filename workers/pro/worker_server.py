# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import sys

COMMON = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "common"))
if COMMON not in sys.path:
    sys.path.insert(0, COMMON)

from worker_core import main


if __name__ == "__main__":
    sys.exit(main("pro"))

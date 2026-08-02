"""Allow `python -m channels_shm.inspect` invocation."""

import sys

from channels_shm.inspect import main

if __name__ == "__main__":
    sys.exit(main())

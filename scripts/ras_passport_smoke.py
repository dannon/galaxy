"""Dev-only smoke test of NIH RAS passport support against a real RAS server.

See galaxy.authnz.ras.smoke for usage. Needs RAS_CLIENT_ID (and RAS_CLIENT_SECRET
for ``exchange``) in the environment.
"""

import os
import sys

sys.path.insert(1, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "lib")))

from galaxy.authnz.ras.smoke import main

if __name__ == "__main__":
    sys.exit(main())

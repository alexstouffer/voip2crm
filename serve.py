#!/usr/bin/env python3
"""Start the telephony webhook receiver. Equivalent to `gv-crm-webhook`."""
import sys

from gv_crm.webhook.server import main

if __name__ == "__main__":
    sys.exit(main())

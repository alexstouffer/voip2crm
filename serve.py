#!/usr/bin/env python3
"""Start the telephony webhook receiver. Equivalent to `voip2crm-webhook`."""
import sys

from voip2crm.webhook.server import main

if __name__ == "__main__":
    sys.exit(main())

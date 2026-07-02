#!/usr/bin/env python3
"""Convenience launcher. Equivalent to the installed `voip2crm` console command."""
import sys

from voip2crm.cli import main

if __name__ == "__main__":
    sys.exit(main())

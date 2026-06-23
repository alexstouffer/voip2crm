#!/usr/bin/env python3
"""Convenience launcher. Equivalent to the installed `gv-crm` console command."""
import sys

from gv_crm.cli import main

if __name__ == "__main__":
    sys.exit(main())

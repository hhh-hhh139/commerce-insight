#!/usr/bin/env python3
"""Git asks this child process for the temporary in-memory release credential."""
import os
import sys

prompt = sys.argv[1] if len(sys.argv) == 2 else ''
if prompt == "Username for 'https://github.com/hhhhao139-ai/commerce-insight.git': ":
    print('hhhhao139-ai')
elif prompt == "Password for 'https://hhhhao139-ai@github.com/hhhhao139-ai/commerce-insight.git': ":
    print(os.environ['COMMERCE_PERSONAL_GIT_TOKEN'])
else:
    raise SystemExit(1)

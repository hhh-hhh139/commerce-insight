#!/usr/bin/env python3
"""Git asks this child process for the temporary in-memory release credential."""
import os
import sys

prompt = sys.argv[1] if len(sys.argv) == 2 else ''
if prompt == "Username for 'https://github.com/hhh-hhh139/commerce-insight.git': ":
    print('hhh-hhh139')
elif prompt == "Password for 'https://hhh-hhh139@github.com/hhh-hhh139/commerce-insight.git': ":
    print(os.environ['COMMERCE_PERSONAL_GIT_TOKEN'])
else:
    raise SystemExit(1)

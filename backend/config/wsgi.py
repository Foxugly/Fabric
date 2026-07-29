from __future__ import annotations

import os
import sys
from pathlib import Path

from django.core.wsgi import get_wsgi_application

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()

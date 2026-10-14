"""``python -m pdf_studio [file.pdf ...]``"""

import sys

from pdf_studio.ui.app import main

if __name__ == "__main__":
    sys.exit(main())

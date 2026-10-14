"""Console/GUI entry point (``pdf-studio``)."""

import sys


def main() -> int:
    from pdf_studio.ui.app import main as run
    return run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())

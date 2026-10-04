"""Same as `python -m trustissues.features.text_shared` (share hand-labelled text features without the text)."""
import sys

from trustissues.features.text_shared import main

if __name__ == "__main__":
    sys.exit(main())

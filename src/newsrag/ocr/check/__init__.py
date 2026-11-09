"""OCR sanity check (ticket 009): a Telugu reader compares ~10 varied pages' scans with their
OCR text in a local web page, gives each a verdict, and signs off or names the page types whose
OCR fails (those become ticket 010's Sarvam fallback rules).

- ``pages``: choosing the 10 pages (``data/ocr-check.txt``)
- ``verdicts``: ``<ocr_check_dir>/verdicts.json`` and the sign-off rules
- ``server``: the web page
- ``cli``: ``newsrag ocr check build | serve | summary``
"""

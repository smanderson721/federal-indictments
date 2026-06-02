"""Image fetchers / synthesizers for The Verdict (vertical YouTube Shorts).

Modules:
    mugshot_fetcher    — defendant photos via Wikipedia / Gemini-grounded search
    doc_synthesizer    — PIL-rendered court-document images
    streetview_fetcher — Google Street View (if key) / OSM static map fallback
    image_resolver     — walks a script.json, fills in card.image_path for each
                         layout that needs an image.
"""

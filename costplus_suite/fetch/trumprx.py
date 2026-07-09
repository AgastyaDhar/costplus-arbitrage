"""
TrumpRx listed-price comparison -- NOT IMPLEMENTED.

trumprx.gov is a client-rendered Next.js app (confirmed by inspection: the
static HTML ships no pricing data, only JS bundles). No public bulk-data feed
or documented API was found on the site. Getting prices out of it would mean
either (a) reverse-engineering an undocumented internal API, which can change
without notice and isn't something to bake into an unattended pipeline without
review, or (b) headless-browser scraping, which the build spec explicitly
deferred for Cost Plus's own site for the same reason ("Do not scrape yet") --
the same caution applies here, doubly so since this benchmark would sit next
to the Cost Plus numbers in the same leaderboard.

This module is left as a stub on purpose rather than silently faked. To wire
it up for real: either get an official data source/API from TrumpRx, or
explicitly ask for a one-off reviewed scraper (mirroring the fetch/costplus_
scrape.py pattern already flagged as optional for Cost Plus).
"""
from __future__ import annotations


def load_trumprx_prices():
    raise NotImplementedError(
        "TrumpRx has no discovered public API or bulk data feed. See module "
        "docstring. Module E (modules/e_brand_trumprx.py) runs its brand "
        "list-price leaderboard without a TrumpRx comparison until this is "
        "wired up deliberately."
    )

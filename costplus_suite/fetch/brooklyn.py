"""
Placeholder -- data source unresolved.

The original project layout names a fetch/brooklyn.py alongside nadac.py,
partd.py, sdud.py, asp.py, trumprx.py, and shortages.py, but no dataset called
"Brooklyn" is described anywhere else in the build spec, and nothing by that
name turned up as a recognizable public CMS/FDA/HHS drug-pricing dataset
during Phase 1/2 research. Rather than guess and silently wire up the wrong
source under a confident-looking module name, this is left unimplemented.

If you can say what "Brooklyn" refers to (an internal codename, a specific
vendor/dataset, a typo for something else), this is a five-minute fill-in
once that's known -- the fetch/*.py pattern (discover -> cache -> load) is
already established by every other module in this package.
"""
from __future__ import annotations


def load_brooklyn():
    raise NotImplementedError(
        "Unresolved data source -- see module docstring. Ask the suite's "
        "author what 'Brooklyn' refers to before implementing this."
    )

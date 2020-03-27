# freeproxieslight

Module for generating proxy list from free proxies and testing this list for response

**Usage:**
```python

import freeproxieslight as fpl

result = fpl.get_tested_proxies()
the_list = result['proxies']

```

**Additional info: **

You should have/or create file **proxylist.py** with list of needed proxies in this simpliest format:

``` python

PROXIES_URLS = ['URL1', 
                'URL2',
                'URL3']

```

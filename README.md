# freeproxieslight

Module for generating proxy list from free proxies and testing this list for response

**Usage:**
```python

import freeproxieslight as fpl

result = fpl.get_tested_proxies()
the_list = result['proxies']

```

**Additional info:**

You should have/or create file **proxylist.py** with list of needed proxies in this simpliest format:

``` python

PROXIES_URLS = ['URL1', 
                'URL2',
                'URL3']

```

**Example of result:**

```

C:\Python35\Projects\freeproxieslight>python freeproxieslight.py

FreeProxiesLight Module connected, version 2.2-light[3/20/2019]
Time: 14:18:00, all proxies: 497, good: 16, speed: 45.1 prx/sec in 11.0 sec for 100 threads

```

  c:> 

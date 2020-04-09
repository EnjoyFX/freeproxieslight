#!/usr/bin/env python3
"""
(c) 2017-2020 AndyFX proxy getter and checker, light version  
"""
__version__ = '2.4'

from multiprocessing.dummy import Pool as ThreadPool

import proxylist  # file with list of proxy urls
import requests
import time

DEBUG = False  # show or not debug messages
THREADS = 0  # 0 - autoselect, otherwise - number of threads
PROXIES_URLS = proxylist.PROXIES_URLS  # list of urls needed, which have lists of proxies
MAIN_TIMEOUT = 2  # in seconds
TEST_PATTERN = {'url': 'https://slack.com/api/api.test',
                'answer': '{"ok":true}',
                'partially': 0}

print('FreeProxiesLight Module connected, version {}'.format(__version__))


def tst_one(url, output=DEBUG):
    url_for_check = TEST_PATTERN['url']
    proxies = {'http': url, 'https': url}
    try:
        handle = requests.get(url_for_check, proxies=proxies, timeout=MAIN_TIMEOUT).text
        if TEST_PATTERN['partially'] != 0:
            handle = handle[:TEST_PATTERN['partially']]
        if handle == TEST_PATTERN['answer']:
            return url
    except Exception as err:
        if output:
            print(err)


def parse_proxies_to_list(html_text):
    dlm1, dlm2 = 'class="table table-striped table-bordered', '<div class="list-bottom">'
    html = g_mid(html_text, dlm1, dlm2)

    dlm1, dlm2 = '</thead><tbody><tr><td>', '</tr></tbody><tfoot><tr>'
    html = g_mid(html, dlm1, dlm2)

    rows = html.split("</tr><tr><td>")

    lst = []
    for r in rows:
        r = r.replace("</td>", "").replace("<td class='hm'>", "<td>").replace("<td class='hx'>", "<td>")
        r = r.replace('<td class="hm">', '<td>').replace('<td class="hx">', '<td>')
        the_s = r.split('<td>')
        lst.append(the_s[0] + ':' + the_s[1])
    return lst


def get_htm(url):
    lst, total, txt = [], '', ''
    try:
        r = requests.get(url)
        status = r.status_code
        txt = r.text
    except Exception as err:
        status = err
    if status != 200 or txt == '':
        if txt == '':
            status = 'empty page'
        total = total + url + '- error, details: ' + str(status) + '\n'
    else:
        lst = parse_proxies_to_list(txt)
    return lst, total


def get_proxies(url_list=PROXIES_URLS):
    num_threads = len(url_list)
    pool = ThreadPool(num_threads)  # Make the Pool of workers
    results = pool.map(get_htm, url_list)  # Open the urls in their own threads and return the results
    pool.close()  # Close the pool and wait for the work to finish
    pool.join()
    proxies, errs = [], ''
    for p in results:
        proxies = proxies + p[0]
        errs = errs + p[1]
    proxies = make_list_unique(proxies)
    return {'proxy': proxies, 'errors': errs}


def g_left(expression, delimiter):
    r = str(expression).split(delimiter)
    return r[0]


def g_right(expression, delimiter, only_first=True):
    r = str(expression).split(delimiter)
    len_txt = len(r)
    if len_txt >= 2:
        r = r[1] if only_first else r[1:]
    else:
        r = expression
    return r


def g_mid(expression, delimiter1, delimiter2):
    return g_left(g_right(expression, delimiter1), delimiter2)


def make_list_unique(lst):
    set_lst = set(lst)
    return list(set_lst)


def txt_to_list(txt):
    txt = txt.replace('\r', '')
    return txt.split('\n')


def tst_proxies(proxies, test_proxies=True):
    start = time.time()
    if test_proxies:
        num_threads = len(proxies) if THREADS == 0 else THREADS
        pool = ThreadPool(num_threads)  # Make the Pool of workers
        results = pool.map(tst_one, proxies)  # Open the urls in their own threads and return the results
        pool.close()  # Close the pool and wait for the work to finish
        pool.join()
        results = list(filter(lambda x: x is not None, results))
    else:
        num_threads = len(proxies)
        results = list(proxies)

    stop = time.time()

    the_size = len(proxies)
    the_time = stop - start  # in sec
    try:
        the_speed = the_size / the_time
    except ZeroDivisionError:
        the_speed = the_size

    good_proxies = len(results)
    txt = {'all': the_size,
           'good': good_proxies,
           'threads': num_threads,
           'time': round(the_time, 1),
           'speed': round(the_speed, 1),
           'timestamp': str(time.strftime('%X'))}
    return results, txt


def get_tested_proxies(test_proxies=True):
    """
    Main function for generation [tested] proxies

    Keyword arguments:
    test_proxies - bool flag for test proxies or not (default - True)

    Output:
    dict {'proxies': [list], 'info': str, 'errors': str}
    """
    results = get_proxies()
    goods, info, errors = [], '', ''
    if results['errors'] == '':
        goods, info = tst_proxies(results['proxy'], test_proxies)
    else:
        print(results['errors'])
        errors = results['errors']
    return {'proxies': goods, 'info': info, 'errors': errors}


def proxy_pretty(js):
    # template: {'all': 680, 'good': 40, 'threads': 200, 'time': 8.24, 'speed': 82.48}
    if js and type(js) is dict:
        return 'Time: {}, all proxies: {},'.format(js['timestamp'], js['all']) + \
               ' good: {},'.format(js['good']) + \
               ' speed: {} prx/sec'.format(js['speed']) + \
               ' in {} sec'.format(js['time']) + \
               ' for {} threads'.format(js['threads'])
    else:
        return 'Data not received...'


if __name__ == '__main__':
    THREADS = 100
    result = get_tested_proxies()
    the_list = result['proxies']
    print(proxy_pretty(result['info']))

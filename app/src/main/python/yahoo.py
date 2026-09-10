# -*- coding: utf-8 -*-
"""
야후 파이낸스 일봉 수집 — yfinance 대체 모듈

  왜 만들었나
    최신 yfinance 는 curl_cffi(C 컴파일 필요)를 필수로 끌어오는데 안드로이드용이
    없습니다. 그래서 안드로이드 앱에서는 yfinance 를 쓸 수 없습니다.
    이 모듈은 야후의 차트 엔드포인트 하나만 직접 호출해 같은 결과를 만듭니다.
    필요한 것은 파이썬 표준 라이브러리와 pandas 뿐입니다.

  바깥에 내놓는 함수는 딱 둘이고, 기존 watch_download.py 가 쓰던 것과
  시그니처·반환형이 완전히 같습니다.

      fetch(tickers, start, end)   -> DataFrame[Ticker,Date,Open,High,Low,Close,Adj Close,Volume]
      fetch_single(ticker, start, end) -> 같은 형태 (한 종목)

  날짜는 거래소 현지 기준입니다(야후가 주는 gmtoffset 을 적용). 'Date' 는 'YYYY-MM-DD' 문자열.
"""
import json, time, random, datetime
import urllib.request, urllib.error
from urllib.parse import quote
import pandas as pd

COLS = ['Ticker', 'Date', 'Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']

HOSTS = ['query1.finance.yahoo.com', 'query2.finance.yahoo.com']
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
TIMEOUT = 30
RETRY = 3
PAUSE = 0.35          # 종목 사이 간격. 너무 빠르면 야후가 429 를 준다.


class YahooError(Exception):
    pass


def _to_epoch(d):
    """'2021-01-01' / date / Timestamp 를 UTC 자정 기준 유닉스 초로."""
    ts = pd.Timestamp(d)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    return int((ts - pd.Timestamp('1970-01-01')).total_seconds())


def _get_json(path):
    """실패하면 호스트를 바꿔 가며 재시도. 마지막까지 안 되면 YahooError.

    path 는 호스트 뒤에 붙일 부분('/v8/finance/chart/...')이다.
    URL 안에 %2C 같은 퍼센트 인코딩이 들어 있어 '%' 서식은 절대 쓰지 않는다.
    """
    last = None
    for attempt in range(RETRY):
        host = HOSTS[attempt % len(HOSTS)]
        full = 'https://' + host + path
        req = urllib.request.Request(full, headers={
            'User-Agent': UA,
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            last = 'HTTP %s' % e.code
            if e.code == 404:
                raise YahooError('404 (없는 티커이거나 상장폐지)')
            if e.code in (429, 999):
                time.sleep(2.0 * (attempt + 1) + random.random())
                continue
        except Exception as e:
            last = str(e)
        time.sleep(0.8 * (attempt + 1))
    raise YahooError(last or '알 수 없는 실패')


def chart(ticker, start, end):
    """한 종목의 일봉을 DataFrame 으로. 실패하면 YahooError."""
    path = ('/v8/finance/chart/' + quote(str(ticker)) +
            '?period1=' + str(_to_epoch(start)) +
            '&period2=' + str(_to_epoch(end)) +
            '&interval=1d&events=div%2Csplit&includeAdjustedClose=true')
    js = _get_json(path)

    ch = (js or {}).get('chart') or {}
    if ch.get('error'):
        raise YahooError(str(ch['error'].get('description', ch['error'])))
    res = ch.get('result') or []
    if not res:
        raise YahooError('결과 없음')
    r = res[0]

    ts = r.get('timestamp') or []
    if not ts:
        return pd.DataFrame(columns=COLS)

    ind = r.get('indicators') or {}
    q = (ind.get('quote') or [{}])[0]
    adj = (ind.get('adjclose') or [{}])[0].get('adjclose')

    off = int((r.get('meta') or {}).get('gmtoffset') or 0)

    o, h, l, c = (q.get('open'), q.get('high'), q.get('low'), q.get('close'))
    v = q.get('volume')
    n = len(ts)
    if not c or len(c) != n:
        raise YahooError('종가 배열이 비었거나 길이가 다름')

    def pick(arr, i):
        try:
            return arr[i]
        except Exception:
            return None

    rows = []
    for i in range(n):
        cl = pick(c, i)
        if cl is None:
            continue          # 거래 없는 날 / 미확정 봉
        d = datetime.datetime.utcfromtimestamp(ts[i] + off).date()
        ac = pick(adj, i) if adj else None
        rows.append((str(ticker), d.strftime('%Y-%m-%d'),
                     pick(o, i), pick(h, i), pick(l, i), cl,
                     cl if ac is None else ac,
                     pick(v, i)))
    if not rows:
        return pd.DataFrame(columns=COLS)

    df = pd.DataFrame(rows, columns=COLS)
    # 같은 날짜가 두 번 오는 경우(드묾) 뒤엣것을 남긴다.
    df = df.drop_duplicates(subset=['Ticker', 'Date'], keep='last')
    for cc in ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']:
        df[cc] = pd.to_numeric(df[cc], errors='coerce')
    return df[COLS].reset_index(drop=True)


def fetch(tickers, start, end):
    """여러 종목. yfinance 의 yf.download 자리를 대신한다."""
    if isinstance(tickers, str):
        tickers = [tickers]
    tickers = [t for t in tickers if t]
    if not tickers:
        return pd.DataFrame(columns=COLS)

    frames = []
    for i, tk in enumerate(tickers):
        try:
            d = chart(tk, start, end)
        except YahooError as e:
            print('  [건너뜀] %s - %s' % (tk, e))
            continue
        except Exception as e:
            print('  [건너뜀] %s - %s: %s' % (tk, type(e).__name__, e))
            continue
        if d.empty:
            print('  [건너뜀] %s - 빈 데이터' % tk)
            continue
        frames.append(d)
        if i + 1 < len(tickers):
            time.sleep(PAUSE)
    if not frames:
        return pd.DataFrame(columns=COLS)
    return pd.concat(frames, ignore_index=True)[COLS]


def fetch_single(ticker, start, end):
    """한 종목만 다시 받아 보는 경로. 빠진 봉 수복에 쓴다."""
    try:
        return chart(ticker, start, end)
    except Exception as e:
        print('    [단건 %s] %s' % (ticker, e))
        return pd.DataFrame(columns=COLS)


if __name__ == '__main__':
    import sys
    tk = sys.argv[1] if len(sys.argv) > 1 else 'AAPL'
    df = fetch([tk], '2026-08-01', '2026-09-11')
    print(df.tail(10).to_string(index=False))
    print('행 수:', len(df))

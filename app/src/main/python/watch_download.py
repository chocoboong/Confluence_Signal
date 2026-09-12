# -*- coding: utf-8 -*-
"""
관찰 티커 일봉 다운로드 (야후 파이낸스)

  사용법 : 같은 폴더의 `실행하기_관찰알림.bat` 을 더블클릭 (이 스크립트가 먼저 돕니다)
  수동   : pip install pandas  ->  python watch_download.py
           (야후 수집은 같은 폴더의 yahoo.py 가 담당합니다. yfinance 는 쓰지 않습니다.)

받을 종목은 이 파일이 아니라 같은 폴더의 `관찰티커.txt` 에서 고칩니다.

출력 : 데이터\관찰_일봉.csv   (관찰 티커)
       데이터\지수_일봉.csv   (시장 국면 판정용 지수)
       컬럼 Ticker, Date, Open, High, Low, Close, Adj Close, Volume

기존 파일이 있으면 뒷부분만 이어받습니다. 새로 추가된 티커만 전체 기간을 받습니다.
"""
import os, re, sys, time, json
import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

HERE = os.environ.get('WATCH_HOME') or os.path.dirname(os.path.abspath(__file__))
TICKER_FILE = os.path.join(HERE, '관찰티커.txt')
DATADIR = os.path.join(HERE, '데이터')
WATCH_CSV = os.path.join(DATADIR, '관찰_일봉.csv')
INDEX_CSV = os.path.join(DATADIR, '지수_일봉.csv')

START = '2021-01-01'      # 새 티커를 받을 때의 시작일
OVERLAP_DAYS = 10         # 기존 티커를 겹쳐 받는 일수 (배당/분할 소급수정 대비)
CHUNK_SIZE = 25           # 한 번에 요청할 종목 수. 한꺼번에 150종목을 부르면 야후가
                          # 일부 종목의 최근 봉을 통째로 빼먹고 응답하는 일이 있다.
REPAIR_DAYS = 15          # 최근 몇 거래일을 점검해 빠진 봉을 다시 받을지
MIN_REAL = 0.05           # 이 비율(또는 3종목) 이상이 가진 날짜면 '진짜 거래일'로 본다.
                          # 과반(0.5)으로 잡았더니 2026-08-28 처럼 절반 넘게 빠진 날이
                          # '거래일이 아님'으로 판정돼 구멍이 통째로 안 보였다. 큰 구멍일수록
                          # 더 잘 보여야 하므로 기준을 낮게 둔다.
MAX_SINGLE = 50           # 단건 재시도(Ticker.history)를 시도할 최대 종목 수
GIVEUP_AFTER = 3          # 같은 날짜를 이만큼 실패하면 포기한다. 야후가 끝내 안 주는 봉을
                          # 매일 50번씩 다시 요청하면 정작 필요한 다운로드가 차단당한다.
GIVEUP_FILE = '_포기한날짜.json'
DELAY_FILE = '_야후지연.json'   # 야후가 최근 봉을 아직 안 준 상태를 기록
DEFAULT_INDEX = ['RSP']

COLS = ['Ticker', 'Date', 'Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']
PRICE = ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']


def load_tickers():
    """관찰티커.txt 를 읽어 (관찰목록, 지수목록) 을 돌려준다. 매우 관대하게 파싱한다."""
    if not os.path.exists(TICKER_FILE):
        print('[오류] 관찰티커.txt 가 없습니다: ' + TICKER_FILE)
        return [], DEFAULT_INDEX
    try:
        with open(TICKER_FILE, encoding='utf-8-sig') as f:
            lines = f.readlines()
    except Exception as e:
        print('[오류] 관찰티커.txt 를 읽지 못했습니다: %s' % e)
        return [], DEFAULT_INDEX

    groups, section = {}, None
    for raw in lines:
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        m = re.match(r'^\[(.+)\]$', line)
        if m:
            section = m.group(1).strip()
            groups.setdefault(section, [])
            continue
        if section is None:
            continue
        for tok in re.split(r'[,\s]+', line):
            tok = tok.strip().upper()
            if tok:
                groups[section].append(tok)

    watch = list(dict.fromkeys(groups.get('관찰', [])))
    index = list(dict.fromkeys(groups.get('지수', []))) or DEFAULT_INDEX
    return watch, index


def _end_date():
    return (pd.Timestamp.today().normalize() + pd.Timedelta(days=1)).strftime('%Y-%m-%d')


def fetch(tickers, start, end):
    if not tickers:
        return pd.DataFrame(columns=COLS)
    import yahoo
    out = yahoo.fetch(tickers, start, end)
    if out is None or out.empty:
        return pd.DataFrame(columns=COLS)
    return out[COLS]


def _fetch_chunked(label, tickers, start, end):
    """CHUNK_SIZE 씩 나눠서 받는다. 한 번에 많이 부르면 야후가 일부 종목을 누락시킨다."""
    if not tickers:
        return pd.DataFrame(columns=COLS)
    parts = []
    for i in range(0, len(tickers), CHUNK_SIZE):
        grp = tickers[i:i + CHUNK_SIZE]
        tag = '%s %d-%d' % (label, i + 1, i + len(grp))
        parts.append(_fetch_with_retry(tag, grp, start, end))
        if i + CHUNK_SIZE < len(tickers):
            time.sleep(1)
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLS)


def fetch_single(ticker, start, end):
    """묶음 요청으로 봉이 안 나올 때 한 종목만 다시 두드린다."""
    import yahoo
    d = yahoo.fetch_single(ticker, start, end)
    if d is None or d.empty:
        return pd.DataFrame(columns=COLS)
    return d[COLS]


def _giveup_path():
    return os.path.join(DATADIR, GIVEUP_FILE)


def load_giveup():
    try:
        with open(_giveup_path(), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_giveup(g):
    try:
        os.makedirs(DATADIR, exist_ok=True)
        with open(_giveup_path(), 'w', encoding='utf-8') as f:
            json.dump(g, f, ensure_ascii=False, indent=1, sort_keys=True)
    except Exception as e:
        print('  [경고] 포기 목록을 저장하지 못했습니다: %s' % e)


def find_holes(df, tickers, ref_dates=None):
    """최근 REPAIR_DAYS 거래일 중 '다른 종목들은 있는데 이 종목만 없는 날' 을 찾는다.
    ref_dates 를 주면(지수처럼 종목이 하나뿐일 때) 그 날짜 집합을 기준으로 본다."""
    if df.empty:
        return {}
    if ref_dates is not None:
        real = sorted(ref_dates)[-REPAIR_DAYS:]
    else:
        alldates = sorted(df['Date'].unique())[-REPAIR_DAYS:]
        if not alldates:
            return {}
        cnt = df[df['Date'].isin(alldates)].groupby('Date')['Ticker'].nunique()
        n = df['Ticker'].nunique()
        need = max(3, n * MIN_REAL) if n >= 3 else 1
        real = [d for d in alldates if cnt.get(d, 0) >= need]
    first = df.groupby('Ticker')['Date'].min()
    holes = {}
    for t in tickers:
        if t not in first.index:
            continue                       # 아예 못 받은 종목은 별도로 경고한다
        have = set(df.loc[df['Ticker'] == t, 'Date'])
        miss = [d for d in real if d not in have and d >= first[t]]
        if miss:
            holes[t] = miss
    return holes


def _drop_given_up(holes, giveup, group):
    """포기한 날짜는 구멍 목록에서 뺀다."""
    dead = {d for d, v in giveup.get(group, {}).items() if v >= GIVEUP_AFTER}
    if not dead:
        return holes, set()
    out = {}
    for t, days in holes.items():
        left = [d for d in days if d not in dead]
        if left:
            out[t] = left
    return out, dead


def repair(df, tickers, end, ref_dates=None, group='관찰'):
    """빠진 봉만 표적 재수복. 최대 2회 시도하고 남은 구멍을 돌려준다.
    야후가 끝내 안 주는 날짜는 GIVEUP_AFTER 회 뒤 포기해서 매일 헛요청하지 않는다."""
    giveup = load_giveup()
    before = find_holes(df, tickers, ref_dates)
    _, dead = _drop_given_up(before, giveup, group)
    if dead:
        still = sorted({d for v in before.values() for d in v} & dead)
        if still:
            print('  [포기] %s 은(는) %d회 시도해도 야후가 주지 않아 더 받지 않습니다.'
                  % (', '.join(still), GIVEUP_AFTER))
    for attempt in range(2):
        holes, _ = _drop_given_up(find_holes(df, tickers, ref_dates), giveup, group)
        if not holes:
            break
        tks = sorted(holes)
        earliest = min(min(v) for v in holes.values())
        start = (pd.Timestamp(earliest) - pd.Timedelta(days=5)).strftime('%Y-%m-%d')
        shown = ', '.join(tks[:15]) + ('...' if len(tks) > 15 else '')
        how = '묶음' if attempt == 0 else '단건'
        print('  [수복 %d/2 %s] %d종목에서 빠진 봉 발견 - %s 부터 다시 받습니다: %s'
              % (attempt + 1, how, len(tks), start, shown))
        if attempt == 0:
            got = _fetch_chunked('수복', tks, start, end)
        else:
            # 묶음 요청으로 안 나오면 종목별로 다른 경로를 한 번씩 두드려 본다.
            if len(tks) > MAX_SINGLE:
                print('    (단건 재시도는 %d종목까지만 합니다)' % MAX_SINGLE)
            parts = []
            for t in tks[:MAX_SINGLE]:
                one = fetch_single(t, start, end)
                if not one.empty:
                    parts.append(one)
                time.sleep(0.3)
            got = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLS)
        if got.empty:
            break
        df = tidy(pd.concat([df, got], ignore_index=True))

    left, dead = _drop_given_up(find_holes(df, tickers, ref_dates), giveup, group)
    # 이번에도 못 채운 날짜의 실패 횟수를 올린다
    g = giveup.setdefault(group, {})
    failed = {d for v in left.values() for d in v}
    for d in failed:
        g[d] = int(g.get(d, 0)) + 1
    for d in list(g):
        if d not in failed and g[d] < GIVEUP_AFTER:
            g.pop(d)                       # 채워졌으면 기록도 지운다
    save_giveup(giveup)
    return df, left


def _fetch_with_retry(label, tickers, start, end):
    if not tickers:
        return pd.DataFrame(columns=COLS)
    for attempt in range(3):
        try:
            got = fetch(tickers, start, end)
            if got.empty:
                raise RuntimeError('받은 데이터가 비어 있습니다')
            return got
        except Exception as e:
            print('  [%s] 재시도 %d/3 : %s' % (label, attempt + 1, e))
            time.sleep(5)
    print('  [%s] 실패 - 건너뜁니다' % label)
    return pd.DataFrame(columns=COLS)


def tidy(df):
    for c in ['Open', 'High', 'Low', 'Close', 'Adj Close']:
        df[c] = pd.to_numeric(df[c], errors='coerce').round(2)
    df['Volume'] = pd.to_numeric(df['Volume'], errors='coerce').fillna(0).astype('int64')
    df = df.dropna(subset=['Close'])
    df = df.drop_duplicates(['Ticker', 'Date'], keep='last')
    return df.sort_values(['Ticker', 'Date']).reset_index(drop=True)


def run_group(label, tickers, path, ref_dates=None):
    print('')
    print('[%s] %d종목: %s' % (label, len(tickers), ', '.join(tickers) if tickers else '(없음)'))
    if not tickers:
        return
    end = _end_date()
    old = None
    plan = []

    if os.path.exists(path):
        try:
            old = pd.read_csv(path, dtype={'Date': str})
            last = old['Date'].max()
            have = set(old['Ticker'].unique())
            keep = [t for t in tickers if t in have]
            fresh = [t for t in tickers if t not in have]
            refetch_from = (pd.Timestamp(last) - pd.Timedelta(days=OVERLAP_DAYS)).strftime('%Y-%m-%d')
            print('  기존 파일 %s ~ %s' % (old['Date'].min(), last))
            print('  이어받기: 기존 %d종목은 %s 이후만' % (len(keep), refetch_from))
            if fresh:
                print('  새로 추가된 %d종목은 %s 부터 전체: %s' % (len(fresh), START, ', '.join(fresh)))
            plan.append(('이어받기', keep, refetch_from))
            plan.append(('신규종목', fresh, START))
            # 목록에서 빠진 티커는 파일에서도 지운다 (알림 대상과 데이터를 일치시킨다)
            drop = sorted(have - set(tickers))
            if drop:
                print('  목록에서 빠져 데이터도 제거: %s' % ', '.join(drop))
                old = old[old['Ticker'].isin(tickers)]
        except Exception as e:
            print('  기존 파일을 읽지 못했습니다(%s). 전체를 다시 받습니다.' % e)
            old = None
            plan = [('전체', tickers, START)]
    else:
        print('  기존 파일 없음 - 전체 다운로드')
        plan.append(('전체', tickers, START))

    parts = [_fetch_chunked(lb, tks, st, end) for lb, tks, st in plan if tks]
    new = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLS)
    if new.empty and (old is None or old.empty):
        print('  [%s] 받은 데이터가 없어 건너뜁니다' % label)
        return

    df = tidy(pd.concat([old, new], ignore_index=True) if old is not None else new)

    # 빠진 봉 표적 수복. 야후가 여러 종목을 한꺼번에 줄 때 일부 종목의 최근 봉을
    # 통째로 빼먹는 일이 있고, 그대로 두면 이어받기 창 밖으로 밀려나 영구 손실된다.
    df, left = repair(df, tickers, end, ref_dates, group=label)
    if left:
        days = sorted({d for v in left.values() for d in v})
        print('  [주의] 아직 빠진 봉이 있습니다 (%d종목, 날짜 %s).'
              % (len(left), ', '.join(days[:5])))
        print('         야후가 아직 그 봉을 주지 않는 것일 수 있습니다. 다음 실행 때 다시 시도합니다.')

    # ★ 야후가 '가장 최근에 닫힌 장'의 봉을 아직 안 준 경우를 잡는다.
    #   모든 종목이 같은 날을 빠뜨리면 find_holes 는 기준이 될 종목이 없어 못 본다.
    #   그래서 야후가 스스로 알려주는 마지막 장 날짜와 직접 대조한다.
    df = _check_lagging(df, label, tickers, end)

    os.makedirs(DATADIR, exist_ok=True)
    df.to_csv(path, index=False)
    print('  저장 %s' % path)
    print('  %d종목 / %s행 / %s ~ %s' % (df.Ticker.nunique(), format(len(df), ','),
                                        df.Date.min(), df.Date.max()))
    missing = [t for t in tickers if t not in set(df['Ticker'])]
    if missing:
        print('  [주의] 끝내 못 받은 종목: %s (티커 철자를 확인하세요)' % ', '.join(missing))


def _delay_path():
    return os.path.join(DATADIR, DELAY_FILE)


def load_delay():
    try:
        with open(_delay_path(), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_delay(d):
    try:
        os.makedirs(DATADIR, exist_ok=True)
        with open(_delay_path(), 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
    except Exception:
        pass


def _check_lagging(df, label, tickers, end):
    """받아온 마지막 봉이 야후가 말하는 마지막 장보다 뒤처졌는지 본다.

    뒤처졌으면 한 번 더 받아 보고, 그래도 안 채워지면 기록해 둔다.
    (watch_scan 이 그 기록을 읽어 알림 메시지에 경고를 붙인다.)
    """
    import yahoo
    d = load_delay()
    have = str(df['Date'].max()) if not df.empty else ''
    sess = str(getattr(yahoo, 'LAST_SESSION', '') or '')
    synth = list(getattr(yahoo, 'SYNTH', []) or [])

    if not sess or not have or sess <= have:
        if synth:
            # 마지막 봉을 meta 요약값으로 채워서 따라잡은 경우
            print('  [보정] %s 봉이 야후 일봉에 없어 마감 요약값으로 채웠습니다 (%d종목).'
                  % (sess, len(synth)))
            print('         시가·고가·저가는 정확하지 않습니다. 종가만 씁니다.')
            print('         다음 실행에서 진짜 봉이 들어오면 자동으로 교체됩니다.')
            d[label] = {'합성': sess, '종목수': len(synth)}
        else:
            d.pop(label, None)
        save_delay(d)
        return df

    print('  [주의] 야후가 %s 장의 봉을 아직 주지 않았습니다 (지금 마지막 %s).'
          % (sess, have))
    print('         30초 뒤 한 번 더 받아 봅니다.')
    time.sleep(30)
    start = (pd.Timestamp(have) - pd.Timedelta(days=3)).strftime('%Y-%m-%d')
    again = _fetch_chunked('재시도', tickers, start, end)
    if not again.empty:
        merged = tidy(pd.concat([df, again], ignore_index=True))
        new_have = str(merged['Date'].max())
        if new_have > have:
            print('  [해결] %s 봉이 들어왔습니다.' % new_have)
            d.pop(label, None)
            save_delay(d)
            return merged
        df = merged

    print('  [미해결] %s 봉은 여전히 없습니다. 다음 실행에서 다시 시도합니다.' % sess)
    d[label] = {'기대': sess, '실제': have}
    save_delay(d)
    return df


def main():
    watch, index = load_tickers()
    print('=' * 60)
    print('관찰 티커 일봉 다운로드')
    print('설정 파일: ' + TICKER_FILE)
    print('=' * 60)
    if not watch:
        print('')
        print('[안내] 관찰티커.txt 의 [관찰] 아래에 종목이 하나도 없습니다.')
        print('       메모장으로 열어 티커를 적고 다시 실행하세요.')
    run_group('관찰', watch, WATCH_CSV)
    # 지수는 종목이 하나라 '남들은 있는데 나만 없는 날' 을 스스로 못 찾는다.
    # 관찰 파일의 거래일을 기준으로 삼아 같은 방식으로 점검한다.
    ref = None
    if os.path.exists(WATCH_CSV):
        try:
            w = pd.read_csv(WATCH_CSV, usecols=['Ticker', 'Date'], dtype=str)
            cnt = w.groupby('Date')['Ticker'].nunique()
            n = w['Ticker'].nunique()
            # 한두 종목만 가진 날짜(야후의 유령 행)를 거래일로 오인하면
            # 지수가 매번 있지도 않은 봉을 찾아 헛돈다.
            need = max(3, n * MIN_REAL) if n >= 3 else 1
            ref = set(cnt[cnt >= need].index)
        except Exception:
            ref = None
    run_group('지수', index, INDEX_CSV, ref_dates=ref)
    print('')
    print('완료. 출력 폴더: %s' % DATADIR)


if __name__ == '__main__':
    main()
